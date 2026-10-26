"""aiogram wiring — exercised by the Docker smoke run, not unit tests.

Moved from `gateway/telegram.py::create_dispatcher` in Wave 2. Nothing
here decides anything: every branch delegates to an application service that IS
unit-tested, because a dispatcher body is untested by construction.

`presenter_factory` is an argument rather than an import because `presentation`
and `infrastructure` are independent siblings in the module's layer contract —
infrastructure may not reach into presentation. The composition root passes
`presentation.presenter.Presenter`.
"""
from __future__ import annotations

import structlog

from claude_hermes.core.config import Config
from claude_hermes.core.session_keys import build_session_key

from ..application.ports import (
    AclPort,
    AskBrokerPort,
    ContentDecisionsPort,
    SenderPort,
    ThreadTitlerPort,
    TurnEventBusPort,
    VoicePort,
)
from ..application.services.callbacks import handle_callback
from ..application.services.content_cards import (
    handle_content_callback,
    intercept_card_reply,
    is_content_callback,
    skip_title,
)
from ..application.services.gating import should_title
from ..application.services.model_menu import is_model_callback
from ..application.services.gating import verdict as _verdict
from ..application.services.intake import IntakeService
from ..application.services.routing import MessageRouter
from ..domain.messages import _CB_STALE, _REFUSAL, _VOICE_FAIL, _VOICE_OFF
from ..domain.models import MessageEvent, SenderProfile
from .aiogram_mapping import reply_target, thread_lane
from .media import download_media, media_filename

log = structlog.get_logger(__name__)


def create_dispatcher(cfg: Config, *, bot_username: str, router: MessageRouter,
                      acl: AclPort, sender: SenderPort, titler: ThreadTitlerPort,
                      intake: IntakeService, bus: TurnEventBusPort,
                      presenter_factory, voice: VoicePort | None = None,
                      send_voice=None, broker: AskBrokerPort | None = None,
                      model_menu=None,
                      content_decisions: ContentDecisionsPort | None = None):
    """aiogram wiring — exercised by the Docker smoke run, not unit tests.

    `content_decisions` is the content plan's side of the approval cards; left
    `None`, a `cnt:` button answers stale and every message behaves as before.
    """
    from aiogram import Dispatcher, types

    dp = Dispatcher()

    @dp.callback_query()
    async def on_callback(cb: types.CallbackQuery):
        # aiogram runs updates as concurrent tasks (handle_as_tasks=True by
        # default), which is what lets this resolve a question while the turn
        # that asked it is still parked on the per-session lock. If that default
        # is ever flipped, this design self-deadlocks.
        #
        # The model menu is routed FIRST, before the ask broker's early return:
        # `/model` works in a deployment with no ask broker wired, and the
        # `broker is None` branch below would otherwise answer every model button
        # with "that question is no longer active".
        if model_menu is not None and is_model_callback(cb.data):
            await cb.answer(await model_menu.on_callback(cb))
            return
        # A content card's buttons, before the broker for the same reason. The
        # approval is enacted HERE, by the gateway, on the owner's literal tap —
        # never by a model tool.
        if is_content_callback(cb.data):
            user_id = cb.from_user.id if cb.from_user else 0
            await cb.answer(await handle_content_callback(
                cb.data, user_id, content_decisions, acl.is_owner))
            return
        if broker is None:
            await cb.answer(_CB_STALE)
            return
        user_id = cb.from_user.id if cb.from_user else 0
        text = await handle_callback(cb.data, user_id, broker)
        # Answer on EVERY branch, success included, or the button spins forever.
        await cb.answer(text)

    @dp.message()
    async def on_message(message: types.Message):
        media = message.voice or message.audio
        files = intake.specs(message)
        if not message.text and media is None and not files:
            return

        mention = f"@{bot_username}"
        reply_to_bot = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.username == bot_username
        )
        thread_id = thread_lane(message)
        user_id = message.from_user.id if message.from_user else 0
        # Read once, here: the two MessageEvents below are the same person, and
        # this is the only place in the module that touches aiogram's `from_user`.
        profile = SenderProfile.of(message.from_user)
        is_voice = media is not None
        saved: list = []
        reply_to = reply_target(message)

        if content_decisions is not None and reply_to:
            # A reply to a content card is a redraft request. Asked FIRST —
            # before a voice note is transcribed or an attachment downloaded,
            # before the titler and before the router — because it must never
            # become a turn. The card is matched on (chat, message id) by the
            # content side; anything else answers None and carries on below.
            probe = MessageEvent(
                chat_type=message.chat.type, chat_id=message.chat.id,
                user_id=user_id,
                text=(message.text or message.caption or "").replace(mention, ""),
                mentioned=True, thread_id=thread_id,
                message_id=message.message_id, profile=profile,
                reply_to_message_id=reply_to,
            )
            answer = await intercept_card_reply(probe, content_decisions,
                                                acl.is_owner)
            if answer is not None:
                await sender.send(message.chat.id, thread_id, answer)
                return

        async def send_typing() -> None:
            await message.bot.send_chat_action(
                message.chat.id, "typing", message_thread_id=thread_id
            )

        if is_voice or files:
            # Non-text intake. Gate on the caption/reply BEFORE spending a
            # transcription call or a download — a note or a photo in a group we
            # aren't addressed in must cost nothing. Then fall through to the
            # normal turn machinery.
            caption = message.caption or ""
            gate = MessageEvent(
                chat_type=message.chat.type, chat_id=message.chat.id, user_id=user_id,
                text=caption.replace(mention, "").strip(),
                mentioned=(mention in caption) or reply_to_bot,
                thread_id=thread_id, message_id=message.message_id,
                profile=profile, reply_to_message_id=reply_to,
            )
            if is_voice:
                verdict = await _verdict(cfg, gate, acl)
                if verdict == "ignore":
                    return
                if verdict == "deny":
                    if message.chat.type == "private":
                        await sender.send(message.chat.id, thread_id, _REFUSAL)
                    return
                if voice is None:
                    await sender.send(message.chat.id, thread_id, _VOICE_OFF)
                    return
                try:
                    await send_typing()
                    audio = await download_media(message.bot, media)
                    content = await voice.transcribe(audio, media_filename(media))
                except Exception:
                    log.exception("voice intake failed for chat %s", message.chat.id)
                    await sender.send(message.chat.id, thread_id, _VOICE_FAIL)
                    return
            else:
                result = await intake.intake(message, gate=gate,
                                             chat_action=send_typing)
                if result.verdict == "ignore":
                    return
                if result.verdict == "deny":
                    if message.chat.type == "private":
                        await sender.send(message.chat.id, thread_id, _REFUSAL)
                    return
                if result.error:
                    await sender.send(message.chat.id, thread_id, result.error)
                    return
                content, saved = result.content, result.attachments
            mentioned = True  # already authorized by the gate above
        else:
            content = message.text.replace(mention, "").strip()
            mentioned = (mention in message.text) or reply_to_bot

        event = MessageEvent(
            chat_type=message.chat.type,
            chat_id=message.chat.id,
            user_id=user_id,
            text=content,
            mentioned=mentioned,
            thread_id=thread_id,
            message_id=message.message_id,
            attachments=saved,
            profile=profile,
            reply_to_message_id=reply_to,
        )

        key = build_session_key(event.chat_type, event.chat_id,
                                user_id=event.user_id, thread_id=thread_id)
        # BEFORE the turn: a thread is 'new' only until the turn writes its session row.
        # The content cards' topic is never renamed: its name is how the owner finds it.
        if (await should_title(cfg, event, acl)
                and not await skip_title(event, content_decisions)
                and await titler.is_new_thread(key, thread_id)):
            titler.spawn(event.chat_id, thread_id, event.text, session_key=key)

        async def delete_message() -> None:
            await message.delete()

        presenter = presenter_factory(sender, event.chat_id, thread_id,
                                      chat_action=send_typing)
        # The status line is driven by the turn bus now, not by a progress callback
        # threaded through the runner: the same events also feed the web stream, and
        # a turn started from the browser must not draft into this chat.
        unsubscribe = bus.subscribe(key, presenter.on_turn_event)
        # `subscribe` only SCHEDULES the subscription in redis mode, and the
        # worker can claim the row and emit `turn.start` before it is live.
        # `Presenter.on_turn_event` binds its turn on `turn.start` alone, so one
        # missed event means no status line for the whole turn, silently.
        await bus.ready(key)
        try:
            async with presenter:
                reply = await router.handle(event, delete_message=delete_message)
            if reply:
                await presenter.finalize(reply)
                # Voice in → voice out (alongside the text). Best-effort: a failed
                # synthesis or send must never turn a delivered answer into an error.
                if (is_voice and voice is not None and send_voice is not None
                        and cfg.voice_reply_enabled):
                    try:
                        spoken = await voice.synthesize(reply)
                        await send_voice(event.chat_id, thread_id, spoken)
                    except Exception:
                        log.warning("voice reply failed for chat %s",
                                    message.chat.id, exc_info=True)
        except Exception:
            log.exception("turn failed for chat %s", message.chat.id)
            await presenter.finalize("Something went wrong on my side — try again.")
        finally:
            unsubscribe()

    return dp

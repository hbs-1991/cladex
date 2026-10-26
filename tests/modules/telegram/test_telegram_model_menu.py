"""`/model` in Telegram — the menu, the ACL, the taps and the keyboard.

Every decision is in `application/services/model_menu.py`, so everything here
drives that service through fakes of the two ports it uses; the last three cases
drive the infrastructure half, which exists only to read an aiogram object and
call the Bot API.
"""
from __future__ import annotations

from claude_hermes.core.config import Config
from claude_hermes.core.exceptions import NotFound
from claude_hermes.core.model_catalog import CATALOG, EFFORTS, lane_is_shared, resolve
from claude_hermes.modules.telegram.application.services.callbacks import handle_callback
from claude_hermes.modules.telegram.application.services.model_menu import (
    ModelMenuService,
    is_model_callback,
)
from claude_hermes.modules.telegram.application.services.routing import MessageRouter
from claude_hermes.modules.telegram.domain.constants import (
    MODEL_CALLBACK_PREFIX,
    MODEL_MENU_TTL_SECONDS,
)
from claude_hermes.modules.telegram.domain.models import MessageEvent, MessageView
from claude_hermes.modules.telegram.infrastructure.model_menu import (
    TelegramModelMenu,
    message_view,
)

NOW = 1_700_000_000.0


# --- fakes ------------------------------------------------------------------

class Acl:
    def __init__(self, decision="allow", owner=True):
        self._decision, self._owner = decision, owner

    async def check(self, user_id, chat_type, profile=None):
        return self._decision

    async def is_owner(self, user_id):
        return self._owner


class Defaults:
    """identity's users service, as `ModelDefaultsPort` sees it."""

    def __init__(self):
        self.pairs: dict[int, tuple] = {}
        self.calls: list[tuple] = []

    async def default_of(self, user_id):
        return self.pairs.get(user_id, (None, None))

    async def set_default(self, user_id, model, effort):
        self.calls.append((user_id, model, effort))
        if model is None:
            self.pairs.pop(user_id, None)
        else:
            self.pairs[user_id] = (model, effort)


class Lanes:
    """sessions' `SessionService`, as `LaneModelsPort` sees it — the real
    `resolve()` over a dict, so the menu sees the order it will see in
    production (explicit pair, then the owner's default, then the deploy one)."""

    def __init__(self, cfg: Config, *, rows=("tg:private:1",), defaults=None):
        self.rows = set(rows)
        self.pairs: dict[str, tuple] = {}
        self.calls: list[tuple] = []
        self._cfg = cfg
        self._defaults = defaults

    async def effective(self, session_key, owner_id):
        if session_key not in self.rows:
            raise NotFound("no such thread")
        owner_default = (None, None)
        if self._defaults is not None:
            owner_default = self._defaults.pairs.get(owner_id, (None, None))
        return resolve(self.pairs.get(session_key), None, owner_default,
                       shared=lane_is_shared(session_key),
                       deploy_model=self._cfg.model, offered_ids=self._cfg.models)

    async def set_model(self, session_key, model, effort):
        if session_key not in self.rows:
            raise NotFound("no such thread")
        self.calls.append((session_key, model, effort))
        if model is None:
            self.pairs.pop(session_key, None)
        else:
            self.pairs[session_key] = (model, effort)


class Runner:
    def __init__(self):
        self.turns, self.resets = [], []

    async def run_turn(self, key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        self.turns.append((key, text))
        return "reply"

    async def reset(self, key):
        self.resets.append(key)


class Commands:
    async def handle_secret(self, rest, event, *, delete_message=None):
        return "secret answer"

    async def handle_approve(self, rest, event, *, session_key):
        return "approve answer"


class Menu:
    """`ModelMenuPort` for the router cases — the router only cares that it was
    called with a scope and that the answer is passed through."""

    def __init__(self, reply=None):
        self.opened: list[tuple] = []
        self._reply = reply

    async def open(self, event, scope="t"):
        self.opened.append((event.text, scope))
        return self._reply


def ev(**kw):
    base = dict(chat_type="private", chat_id=1, user_id=1, text="/model",
                mentioned=False, message_id=5)
    base.update(kw)
    return MessageEvent(**base)


def view(**kw):
    base = dict(chat_type="private", chat_id=1, thread_id=None, message_id=5,
                date=int(NOW))
    base.update(kw)
    return MessageView(**base)


def service(cfg=None, *, acl=None, lanes=None, defaults=None):
    cfg = cfg or Config()
    defaults = Defaults() if defaults is None else defaults
    lanes = lanes or Lanes(cfg, defaults=defaults)
    return ModelMenuService(cfg, acl=acl or Acl(), lanes=lanes, defaults=defaults,
                            now=lambda: NOW)


def labels(menu):
    return [button.text for row in menu.rows for button in row]


def datas(menu):
    return [button.data for row in menu.rows for button in row]


# --- /model, through the router ---------------------------------------------

async def test_model_opens_the_lane_scope_and_the_router_answers_nothing_itself():
    """The keyboard is a `sendMessage` of its own, so the router's contract for
    "handled, say nothing more" is a None reply."""
    menu, runner = Menu(), Runner()
    reply = await MessageRouter(Config(), runner=runner, acl=Acl(),
                                commands=Commands(), model_menu=menu
                                ).handle(ev(text="/model"))
    assert reply is None
    assert menu.opened == [("/model", "t")]
    assert runner.turns == []           # /model never reaches the CLI (AC #15)


async def test_model_default_opens_the_personal_scope():
    menu = Menu()
    await MessageRouter(Config(), runner=Runner(), acl=Acl(), commands=Commands(),
                        model_menu=menu).handle(ev(text="/model default"))
    assert menu.opened == [("/model default", "d")]


async def test_model_at_the_bot_is_still_the_command():
    menu = Menu()
    await MessageRouter(Config(), runner=Runner(), acl=Acl(), commands=Commands(),
                        model_menu=menu).handle(ev(text="/model@hermesbot default"))
    assert menu.opened == [("/model@hermesbot default", "d")]


async def test_effort_points_at_model_and_never_reaches_the_runner():
    runner = Runner()
    reply = await MessageRouter(Config(), runner=runner, acl=Acl(),
                                commands=Commands(), model_menu=Menu()
                                ).handle(ev(text="/effort high"))
    assert "/model" in reply
    assert runner.turns == []


async def test_models_plural_is_not_the_command_and_still_runs_a_turn():
    runner, menu = Runner(), Menu()
    await MessageRouter(Config(), runner=runner, acl=Acl(), commands=Commands(),
                        model_menu=menu).handle(ev(text="/models please"))
    assert runner.turns == [("tg:private:1", "/models please")]
    assert menu.opened == []


async def test_model_is_still_intercepted_when_no_menu_is_wired():
    """Falling through would hand `/model` to the CLI, which runs it as its own
    local command — the one thing AC #15 forbids."""
    runner = Runner()
    reply = await MessageRouter(Config(), runner=runner, acl=Acl(),
                                commands=Commands()).handle(ev(text="/model"))
    assert "isn't configured" in reply
    assert runner.turns == []


async def test_a_denied_sender_gets_the_refusal_and_opens_nothing():
    menu = Menu()
    reply = await MessageRouter(Config(), runner=Runner(), acl=Acl("deny"),
                                commands=Commands(), model_menu=menu
                                ).handle(ev(text="/model"))
    assert "allowlist" in reply
    assert menu.opened == []


async def test_an_unaddressed_group_message_opens_nothing():
    menu = Menu()
    reply = await MessageRouter(Config(group_require_mention=True), runner=Runner(),
                                acl=Acl(), commands=Commands(), model_menu=menu
                                ).handle(ev(chat_type="supergroup", chat_id=-5,
                                            text="/model"))
    assert reply is None
    assert menu.opened == []


# --- opening the menu -------------------------------------------------------

async def test_the_menu_names_the_effective_pair_and_prices_every_model():
    result = await service().open(ev())
    assert result.reply == ""
    assert result.menu.text.startswith("Model for this chat: Sonnet 5 · Auto (high)")
    assert "✓ Sonnet 5 · $2/$10" in labels(result.menu)
    assert "Opus 5.5 · $4/$20" in labels(result.menu)


async def test_the_menu_offers_auto_every_effort_and_clear():
    result = await service().open(ev())
    assert "✓ Auto (high)" in labels(result.menu)
    assert [e.capitalize() for e in EFFORTS] == [
        text for text in labels(result.menu) if text.lower() in EFFORTS]
    assert f"{MODEL_CALLBACK_PREFIX}:t:x:-" in datas(result.menu)


async def test_a_lane_with_no_thread_row_says_send_a_message_first():
    cfg = Config()
    result = await service(cfg, lanes=Lanes(cfg, rows=())).open(ev())
    assert result.menu is None
    assert result.reply == "Send a message here first, then use /model."


async def test_a_non_owner_cannot_open_the_lane_menu_in_a_group():
    cfg = Config()
    lanes = Lanes(cfg, rows=("tg:supergroup:-5:1",))
    result = await service(cfg, acl=Acl(owner=False), lanes=lanes).open(
        ev(chat_type="supergroup", chat_id=-5))
    assert result.reply == "Only the owner can change the model here."


async def test_a_non_owner_may_still_open_their_own_default_anywhere():
    cfg = Config()
    lanes = Lanes(cfg, rows=("tg:supergroup:-5:1",))
    result = await service(cfg, acl=Acl(owner=False), lanes=lanes).open(
        ev(chat_type="supergroup", chat_id=-5), "d")
    assert result.menu is not None
    assert "not set (new chats use Sonnet 5)" in result.menu.text


async def test_a_private_chats_topic_is_its_users_own_lane():
    """`lane_is_shared` is false for `tg:private:…:t9`, so the owner gate does not
    apply — a non-owner runs their own topics."""
    cfg = Config()
    lanes = Lanes(cfg, rows=("tg:private:1:t9",))
    result = await service(cfg, acl=Acl(owner=False), lanes=lanes).open(
        ev(thread_id=9))
    assert result.menu is not None


async def test_the_default_menu_carries_the_copy_onto_this_chat_button():
    result = await service().open(ev(), "d")
    assert "Also use it in this chat" in labels(result.menu)
    assert f"{MODEL_CALLBACK_PREFIX}:a:x:-" in datas(result.menu)


# --- taps -------------------------------------------------------------------

async def test_a_model_tap_pins_the_lane_and_promises_the_cache_rebuild():
    cfg = Config()
    lanes = Lanes(cfg)
    result = await service(cfg, lanes=lanes).on_tap("mdl:t:m:claude-opus-5-5", 1,
                                                    view())
    assert lanes.calls == [("tg:private:1", "claude-opus-5-5", None)]
    assert result.answer == ("Opus 5.5 · Auto (medium) — the next reply rebuilds "
                             "this model's cache.")
    assert "✓ Opus 5.5 · $4/$20" in labels(result.menu)
    assert result.menu.text.startswith("Model for this chat: Opus 5.5 · Auto (medium)")


async def test_an_effort_tap_pins_the_model_it_applies_to():
    """There is no "effort without a model": leaving the lane unpinned would let
    a later default change move it mid-conversation."""
    cfg = Config()
    lanes = Lanes(cfg)
    result = await service(cfg, lanes=lanes).on_tap("mdl:t:e:max", 1, view())
    assert lanes.calls == [("tg:private:1", "claude-sonnet-5", "max")]
    assert "✓ Max" in labels(result.menu)


async def test_auto_drops_the_effort_and_keeps_the_model():
    cfg = Config()
    lanes = Lanes(cfg)
    lanes.pairs["tg:private:1"] = ("claude-opus-5-5", "max")
    svc = service(cfg, lanes=lanes)
    await svc.on_tap("mdl:t:e:auto", 1, view())
    assert lanes.calls == [("tg:private:1", "claude-opus-5-5", None)]


async def test_a_model_tap_keeps_an_effort_the_new_model_takes():
    cfg = Config()
    lanes = Lanes(cfg)
    lanes.pairs["tg:private:1"] = ("claude-sonnet-5", "low")
    await service(cfg, lanes=lanes).on_tap("mdl:t:m:claude-fable-5-1", 1, view())
    assert lanes.calls == [("tg:private:1", "claude-fable-5-1", "low")]


async def test_clear_drops_the_explicit_pair_and_says_the_lane_follows_the_default():
    cfg = Config()
    lanes = Lanes(cfg)
    lanes.pairs["tg:private:1"] = ("claude-opus-5-5", "high")
    result = await service(cfg, lanes=lanes).on_tap("mdl:t:x:-", 1, view())
    assert lanes.calls == [("tg:private:1", None, None)]
    assert lanes.pairs == {}
    assert "follows your default again" in result.answer


async def test_a_default_tap_writes_the_person_and_leaves_the_lane_alone():
    cfg = Config()
    defaults = Defaults()
    lanes = Lanes(cfg, defaults=defaults)
    result = await service(cfg, lanes=lanes, defaults=defaults).on_tap(
        "mdl:d:m:claude-fable-5-1", 1, view())
    assert defaults.calls == [(1, "claude-fable-5-1", None)]
    assert lanes.calls == []                     # a live session is never moved
    assert "This chat is unchanged." in result.answer


async def test_also_use_it_in_this_chat_copies_the_default_onto_the_lane():
    cfg = Config()
    defaults = Defaults()
    defaults.pairs[1] = ("claude-opus-5-5", "xhigh")
    lanes = Lanes(cfg, defaults=defaults)
    result = await service(cfg, lanes=lanes, defaults=defaults).on_tap(
        "mdl:a:x:-", 1, view())
    assert lanes.calls == [("tg:private:1", "claude-opus-5-5", "xhigh")]
    assert result.answer.startswith("Opus 5.5 · xhigh")
    # The redrawn keyboard is still the default menu the button sat on.
    assert "Also use it in this chat" in labels(result.menu)


async def test_also_use_it_needs_a_default_to_copy():
    cfg = Config()
    lanes = Lanes(cfg)
    result = await service(cfg, lanes=lanes).on_tap("mdl:a:x:-", 1, view())
    assert result.answer == "You have no default model yet — pick one above first."
    assert lanes.calls == []


async def test_clearing_the_default_restores_the_deploy_one_for_new_chats():
    cfg = Config()
    defaults = Defaults()
    defaults.pairs[1] = ("claude-opus-5-5", None)
    lanes = Lanes(cfg, defaults=defaults)
    result = await service(cfg, lanes=lanes, defaults=defaults).on_tap(
        "mdl:d:x:-", 1, view())
    assert defaults.pairs == {}
    assert "deploy default" in result.answer


# --- the ACL and the stale cases -------------------------------------------

async def test_a_non_owner_tap_on_a_group_lane_changes_nothing():
    cfg = Config()
    lanes = Lanes(cfg, rows=("tg:supergroup:-5:1",))
    result = await service(cfg, acl=Acl(owner=False), lanes=lanes).on_tap(
        "mdl:t:m:claude-opus-5-5", 1, view(chat_type="supergroup", chat_id=-5))
    assert result.answer == "Only the owner can change the model here."
    assert result.menu is None
    assert lanes.calls == []


async def test_a_non_owner_tap_on_apply_in_a_group_changes_nothing():
    cfg = Config()
    defaults = Defaults()
    defaults.pairs[1] = ("claude-opus-5-5", None)
    lanes = Lanes(cfg, rows=("tg:supergroup:-5:1",), defaults=defaults)
    result = await service(cfg, acl=Acl(owner=False), lanes=lanes,
                           defaults=defaults).on_tap(
        "mdl:a:x:-", 1, view(chat_type="supergroup", chat_id=-5))
    assert result.answer == "Only the owner can change the model here."
    assert lanes.calls == []


async def test_a_denied_tapper_is_refused_before_anything_is_read():
    cfg = Config()
    lanes = Lanes(cfg)
    result = await service(cfg, acl=Acl("deny"), lanes=lanes).on_tap(
        "mdl:t:m:claude-opus-5-5", 1, view())
    assert "allowlist" in result.answer
    assert lanes.calls == []


async def test_a_tap_on_a_message_older_than_the_ttl_changes_nothing():
    cfg = Config()
    lanes = Lanes(cfg)
    stale = view(date=int(NOW - MODEL_MENU_TTL_SECONDS - 1))
    result = await service(cfg, lanes=lanes).on_tap("mdl:t:m:claude-opus-5-5", 1,
                                                    stale)
    assert result.answer == "That model menu has expired — send /model again."
    assert lanes.calls == []


async def test_a_message_telegram_no_longer_returns_changes_nothing():
    cfg = Config()
    lanes = Lanes(cfg)
    for gone in (None, view(date=0)):
        result = await service(cfg, lanes=lanes).on_tap("mdl:t:x:-", 1, gone)
        assert "expired" in result.answer
    assert lanes.calls == []


async def test_every_unparseable_model_callback_is_the_same_expired_answer():
    cfg = Config()
    lanes = Lanes(cfg)
    svc = service(cfg, lanes=lanes)
    for data in ("", None, "mdl", "mdl:t:m", "mdl:z:m:claude-sonnet-5",
                 "mdl:t:q:claude-sonnet-5", "mdl:t:m:claude-sonnet-5:extra",
                 "q:abc:0"):
        assert "expired" in (await svc.on_tap(data, 1, view())).answer
    assert lanes.calls == []


async def test_a_button_naming_a_model_that_left_the_offer_is_refused():
    """A keyboard that outlived a `models:` change. `validate` is the same guard
    the HTTP route uses, so the two channels cannot disagree."""
    cfg = Config()
    lanes = Lanes(cfg)
    result = await service(cfg, lanes=lanes).on_tap("mdl:t:m:claude-opus-5", 1,
                                                    view())
    assert result.answer == "That choice is no longer offered here — send /model again."
    assert lanes.calls == []


async def test_a_tap_on_a_lane_whose_thread_row_vanished_says_send_a_message_first():
    cfg = Config()
    result = await service(cfg, lanes=Lanes(cfg, rows=())).on_tap(
        "mdl:t:m:claude-opus-5-5", 1, view())
    assert result.answer == "Send a message here first, then use /model."


# --- the wire ---------------------------------------------------------------

def test_every_frozen_callback_shape_fits_telegrams_64_bytes():
    shapes = [f"{MODEL_CALLBACK_PREFIX}:{scope}:x:-" for scope in ("t", "d", "a")]
    for scope in ("t", "d"):
        shapes += [f"{MODEL_CALLBACK_PREFIX}:{scope}:m:{info.id}" for info in CATALOG]
        shapes += [f"{MODEL_CALLBACK_PREFIX}:{scope}:e:{effort}"
                   for effort in (*EFFORTS, "auto")]
    assert shapes
    for shape in shapes:
        assert len(shape.encode()) <= 64, shape


def test_the_dispatcher_can_tell_a_model_button_from_an_ask_button():
    assert is_model_callback("mdl:t:x:-")
    assert not is_model_callback("q:deadbeef:0")
    assert not is_model_callback(None)
    assert not is_model_callback("")


async def test_ask_callbacks_are_untouched_by_the_model_menu():
    """`handle_callback` is the ask path and knows nothing about `mdl:` — which is
    why the dispatcher routes the prefix before it, broker or no broker."""
    class Broker:
        def __init__(self):
            self.calls = []

        async def resolve(self, qid, token, user_id):
            self.calls.append((qid, token, user_id))
            return True

    broker = Broker()
    assert "no longer active" in await handle_callback("mdl:t:x:-", 1, broker)
    assert broker.calls == []
    assert "Got it" in await handle_callback("q:ab:0", 1, broker)


# --- the aiogram half -------------------------------------------------------

class Chat:
    def __init__(self, type="private", id=1, is_forum=False):
        self.type, self.id, self.is_forum = type, id, is_forum


class Msg:
    def __init__(self, *, chat=None, message_id=5, date=int(NOW),
                 message_thread_id=None, is_topic_message=None):
        self.chat = chat or Chat()
        self.message_id = message_id
        self.date = date
        self.message_thread_id = message_thread_id
        self.is_topic_message = is_topic_message


class User:
    def __init__(self, id=1):
        self.id = id


class Cb:
    def __init__(self, data, message, user_id=1):
        self.data = data
        self.message = message
        self.from_user = User(user_id)


class Api:
    def __init__(self):
        self.calls: list[tuple] = []

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": 5}


def test_the_view_reads_a_thread_id_through_thread_lane_not_raw():
    """A supergroup reply chain also carries `message_thread_id`; treating it as a
    lane would fork an empty session — the reason `thread_lane` exists."""
    reply_chain = Msg(chat=Chat(type="supergroup", id=-5), message_thread_id=77,
                      is_topic_message=False)
    assert message_view(reply_chain).thread_id is None
    topic = Msg(chat=Chat(type="supergroup", id=-5), message_thread_id=77,
                is_topic_message=True)
    assert message_view(topic).thread_id == 77


def test_an_inaccessible_message_reads_as_a_dateless_view():
    assert message_view(None) is None
    assert message_view(Msg(date=0)).date == 0


async def test_open_sends_the_keyboard_into_the_topic_it_was_asked_in():
    cfg = Config()
    api = Api()
    lanes = Lanes(cfg, rows=("tg:private:1:t9",))
    menu = TelegramModelMenu(api, service(cfg, lanes=lanes))
    assert await menu.open(ev(thread_id=9)) is None
    method, payload = api.calls[0]
    assert method == "sendMessage"
    assert payload["message_thread_id"] == 9
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] \
        == "mdl:t:m:claude-sonnet-5"


async def test_open_passes_a_plain_refusal_straight_back_to_the_router():
    cfg = Config()
    api = Api()
    menu = TelegramModelMenu(api, service(cfg, lanes=Lanes(cfg, rows=())))
    assert await menu.open(ev()) == "Send a message here first, then use /model."
    assert api.calls == []


async def test_a_tap_redraws_the_message_it_sat_on():
    cfg = Config()
    api = Api()
    lanes = Lanes(cfg)
    menu = TelegramModelMenu(api, service(cfg, lanes=lanes))
    answer = await menu.on_callback(Cb("mdl:t:m:claude-opus-5-5", Msg()))
    assert "rebuilds this model's cache" in answer
    method, payload = api.calls[0]
    assert method == "editMessageText"
    assert payload["message_id"] == 5
    assert "Opus 5.5" in payload["text"]


async def test_a_redraw_that_telegram_refuses_never_loses_the_answer():
    class Angry(Api):
        async def __call__(self, method, payload):
            raise RuntimeError("message is not modified")

    cfg = Config()
    lanes = Lanes(cfg)
    menu = TelegramModelMenu(Angry(), service(cfg, lanes=lanes))
    answer = await menu.on_callback(Cb("mdl:t:m:claude-opus-5-5", Msg()))
    assert "rebuilds this model's cache" in answer
    assert lanes.calls == [("tg:private:1", "claude-opus-5-5", None)]


async def test_a_port_that_falls_over_still_answers_the_button():
    class Dead:
        async def effective(self, session_key, owner_id):
            raise RuntimeError("the database is down")

        async def set_model(self, session_key, model, effort):
            raise RuntimeError("the database is down")

    cfg = Config()
    menu = TelegramModelMenu(Api(), service(cfg, lanes=Dead()))
    answer = await menu.on_callback(Cb("mdl:t:m:claude-opus-5-5", Msg()))
    assert "Something went wrong" in answer

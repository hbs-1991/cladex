"""`/model` in Telegram: what the menu says, and what one tap means.

All of it, except the Bot API calls and the aiogram reading — the dispatcher is
untested by construction, so nothing that decides anything lives there. The
counterpart is `infrastructure/model_menu.py`, which builds the `MessageView`
this module takes (it is the layer allowed to know what `message_thread_id`
means), sends the keyboard and edits it afterwards.

The pair a lane runs is lane state, not sender state: the same `threads` row the
web picker writes, so a switch here shows up in an open browser tab and a switch
there shows up the next time `/model` is sent. What the buttons can say is frozen
in `domain/constants.py` — 64 bytes of `callback_data` and no more.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from claude_hermes.core.config import Config
from claude_hermes.core.exceptions import NotFound
from claude_hermes.core.model_catalog import (
    Choice,
    ModelInfo,
    lookup,
    offered,
    validate,
)
from claude_hermes.core.session_keys import build_session_key, is_group_lane, lane_owner

from ...domain.constants import (
    ACTION_CLEAR,
    ACTION_EFFORT,
    ACTION_MODEL,
    AUTO_EFFORT,
    MODEL_CALLBACK_PREFIX,
    MODEL_MENU_TTL_SECONDS,
    SCOPE_APPLY,
    SCOPE_DEFAULT,
    SCOPE_THREAD,
)
from ...domain.messages import (
    _MODEL_APPLIED,
    _MODEL_APPLY_BUTTON,
    _MODEL_AUTO_BUTTON,
    _MODEL_AUTO_PLAIN,
    _MODEL_CLEAR_BUTTON,
    _MODEL_CLEARED,
    _MODEL_DEFAULT_CLEAR_BUTTON,
    _MODEL_DEFAULT_CLEARED,
    _MODEL_DEFAULT_HEAD,
    _MODEL_DEFAULT_SET,
    _MODEL_DEFAULT_UNSET,
    _MODEL_GONE,
    _MODEL_HEAD,
    _MODEL_NO_DEFAULT,
    _MODEL_NO_THREAD,
    _MODEL_NOT_OWNER,
    _MODEL_OFF,
    _MODEL_SET,
    _MODEL_STALE,
    _REFUSAL,
)
from ...domain.models import MenuButton, MessageEvent, MessageView, ModelMenu
from ..ports import AclPort, LaneModelsPort, ModelDefaultsPort

log = structlog.get_logger(__name__)

# The pair `resolve()` works in, spelled once: (model id | None, effort | None).
Pair = tuple[str | None, str | None]

_TICK = "✓ "
_EFFORTS_PER_ROW = 3


def is_model_callback(data: str | None) -> bool:
    """Is this button press the model menu's rather than `ask_user`'s?

    The dispatcher's only decision, and it is here so it can be tested: `mdl:`
    must be routed BEFORE the `broker is None` early return, or a deployment with
    no ask broker wired would answer every model button with "that question is no
    longer active".
    """
    return (data or "").startswith(f"{MODEL_CALLBACK_PREFIX}:")


def _parse(data: str | None) -> tuple[str, str, str] | None:
    """`mdl:<scope>:<action>:<payload>` -> its three fields, or None.

    Everything unparseable answers like an expired menu, for the same reason
    `handle_callback` folds its three stale cases into one: a button from an old
    deploy, a typo'd prefix and a truncated payload are not worth distinguishing
    to the person who pressed it, and one branch cannot drift from three.
    """
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[0] != MODEL_CALLBACK_PREFIX:
        return None
    _, scope, action, payload = parts
    if scope not in (SCOPE_THREAD, SCOPE_DEFAULT, SCOPE_APPLY):
        return None
    if action not in (ACTION_MODEL, ACTION_EFFORT, ACTION_CLEAR):
        return None
    return scope, action, payload


@dataclass(frozen=True)
class MenuOpen:
    """What `/model` produced: either a keyboard to send, or a plain sentence
    explaining why there is none."""

    reply: str = ""
    menu: ModelMenu | None = None


@dataclass(frozen=True)
class MenuTap:
    """What one press produced: the popup answer — never empty, or the button
    spins forever — and the keyboard to redraw, if anything moved."""

    answer: str
    menu: ModelMenu | None = None


class ModelMenuService:
    """`/model`, `/model default`, and every `mdl:` button.

    Ports only: the ACL says who may change what, `LaneModelsPort` owns the lane's
    pair and `ModelDefaultsPort` the person's. The offered list is read once from
    the config, because `offered()` warns about a fork window that cannot hold an
    offered model and that warning belongs at boot, not on every tap.
    """

    def __init__(self, cfg: Config, *, acl: AclPort, lanes: LaneModelsPort,
                 defaults: ModelDefaultsPort | None = None,
                 now: Callable[[], float] = time.time):
        self._cfg = cfg
        self._acl = acl
        self._lanes = lanes
        self._defaults = defaults
        self._now = now
        self._offered = offered(cfg.models, cfg.model, cfg.review_model or cfg.model)
        self._offered_ids = [info.id for info in self._offered]

    # --- /model ------------------------------------------------------------

    async def open(self, event: MessageEvent, scope: str = SCOPE_THREAD) -> MenuOpen:
        """`/model` (`scope="t"`) or `/model default` (`scope="d"`).

        The caller has already run the gate, so the sender is admitted; what is
        left is the room rule — a group or forum topic belongs to the deploy
        owner, and only they may move ITS model. A personal default is nobody
        else's business, so `/model default` is open to every admitted user, in
        any chat.
        """
        key = self._lane_key(event.chat_type, event.chat_id, event.user_id,
                             event.thread_id)
        if scope == SCOPE_DEFAULT and self._defaults is None:
            return MenuOpen(reply=_MODEL_OFF)
        if scope != SCOPE_DEFAULT and not await self._may_set_lane(key, event.user_id):
            return MenuOpen(reply=_MODEL_NOT_OWNER)
        try:
            choice, default = await self._gather(key, event.user_id)
        except NotFound:
            return MenuOpen(reply=_MODEL_NO_THREAD)
        if choice is None:
            return MenuOpen(reply=_MODEL_NO_THREAD)
        return MenuOpen(menu=self._render(scope, choice, default))

    # --- a button press ----------------------------------------------------

    async def on_tap(self, data: str | None, tapper_id: int,
                     message_view: MessageView | None) -> MenuTap:
        """One `mdl:` press. Always answers; changes at most one pair.

        The lane comes from the MESSAGE the button sits on, not from whatever the
        tapper's last chat was — with the tapper's own id, because group lanes are
        keyed per sender (`routing.py`), so a stranger tapping in a group can only
        ever reach their own lane, and the owner check below gates even that.
        """
        parsed = _parse(data)
        if parsed is None or self._expired(message_view):
            return MenuTap(answer=_MODEL_STALE)
        scope, action, payload = parsed
        view = message_view
        if await self._acl.check(tapper_id, view.chat_type) == "deny":
            return MenuTap(answer=_REFUSAL)

        key = self._lane_key(view.chat_type, view.chat_id, tapper_id, view.thread_id)
        if scope != SCOPE_DEFAULT and not await self._may_set_lane(key, tapper_id):
            return MenuTap(answer=_MODEL_NOT_OWNER)
        try:
            choice, default = await self._gather(key, tapper_id)
            if choice is None:
                return MenuTap(answer=_MODEL_NO_THREAD)
            answer = await self._apply(scope, action, payload, key, tapper_id,
                                       choice, default)
            if answer is not None:          # refused before anything was written
                return MenuTap(answer=answer)
            choice, default = await self._gather(key, tapper_id)
        except NotFound:
            # The lane has no `threads` row — `/model` was pressed on a chat that
            # has never run a turn. Nothing is written and nothing is invented.
            return MenuTap(answer=_MODEL_NO_THREAD)
        except ValueError:
            # `validate` refused: a keyboard from before `models:` changed.
            return MenuTap(answer=_MODEL_GONE)

        redraw = SCOPE_DEFAULT if scope == SCOPE_APPLY else scope
        return MenuTap(answer=self._confirmation(scope, action, choice, default),
                       menu=self._render(redraw, choice, default))

    # --- the writes --------------------------------------------------------

    async def _apply(self, scope: str, action: str, payload: str, key: str,
                     tapper_id: int, choice: Choice, default: Pair) -> str | None:
        """Write the one pair this press names. Returns a popup answer when the
        press is refused before any write, None when it landed."""
        if scope == SCOPE_APPLY:
            # "Also use it in this chat": the default becomes this lane's EXPLICIT
            # pair. Not a copy that drifts — a changed default never moves a live
            # session, so the only way to move this one is to pin it here.
            model, effort = default
            if not model:
                return _MODEL_NO_DEFAULT
            validate(model, effort, self._offered_ids)
            await self._lanes.set_model(key, model, effort)
            return None

        current: Pair = default if scope == SCOPE_DEFAULT else (choice.model,
                                                               choice.effort)
        model, effort = self._next_pair(action, payload, current, choice)
        validate(model, effort, self._offered_ids)
        if scope == SCOPE_DEFAULT:
            if self._defaults is None:
                return _MODEL_OFF
            await self._defaults.set_default(tapper_id, model, effort)
        else:
            await self._lanes.set_model(key, model, effort)
        return None

    def _next_pair(self, action: str, payload: str, current: Pair,
                   choice: Choice) -> Pair:
        """The pair one press produces from the pair that is there now."""
        model, effort = current
        if action == ACTION_CLEAR:
            return None, None
        if action == ACTION_MODEL:
            # Keep the effort only if the new model takes it; the CLI silently
            # drops one it does not, and a menu that kept showing it would lie.
            kept = effort if effort in lookup(payload).efforts else None
            return payload, kept
        # An effort on its own PINS the model it applies to (Locked Decisions):
        # there is no "effort without a model", and leaving the lane on a model it
        # never chose would make the next default change move it mid-conversation.
        return model or choice.model, None if payload == AUTO_EFFORT else payload

    def _confirmation(self, scope: str, action: str, choice: Choice,
                      default: Pair) -> str:
        if scope == SCOPE_APPLY:
            return _MODEL_APPLIED.format(pair=self._pair_label(choice.model,
                                                               choice.effort))
        if scope == SCOPE_DEFAULT:
            if action == ACTION_CLEAR or not default[0]:
                return _MODEL_DEFAULT_CLEARED
            return _MODEL_DEFAULT_SET.format(
                pair=self._pair_label(default[0], default[1]))
        if action == ACTION_CLEAR:
            return _MODEL_CLEARED
        return _MODEL_SET.format(pair=self._pair_label(choice.model, choice.effort))

    # --- gathering ---------------------------------------------------------

    def _lane_key(self, chat_type: str, chat_id: int, user_id: int,
                  thread_id: int | None) -> str:
        return build_session_key(chat_type, chat_id, user_id=user_id,
                                 thread_id=thread_id)

    async def _may_set_lane(self, key: str, user_id: int) -> bool:
        """Who may move THIS lane's model. A room (group, supergroup, forum topic)
        is the deploy owner's lane whoever is talking in it, so only they; a
        private chat — including its topics — is its user's own."""
        if not is_group_lane(key):
            return True
        return bool(await self._acl.is_owner(user_id))

    async def _gather(self, key: str, user_id: int) -> tuple[Choice | None, Pair]:
        """The lane's effective pair and the tapper's own default, in one place.

        `owner_id` is the LANE's owner: the chat's user for a private lane, the
        deploy owner for a room. Telegram does not know the deploy owner's id (it
        asks the ACL a yes/no question instead), so the tapper stands in for it —
        harmless, because a room is a shared lane and `resolve()` never consults a
        personal default there.
        """
        owner_id = lane_owner(key, "telegram", user_id, user_id)
        choice = await self._lanes.effective(key, owner_id)
        default: Pair = (None, None)
        if self._defaults is not None:
            default = tuple(await self._defaults.default_of(user_id))  # type: ignore[assignment]
        return choice, default

    def _expired(self, view: MessageView | None) -> bool:
        """A menu Telegram no longer returns, or one nobody has looked at for ten
        minutes. Both are the same answer: its header names a pair that may have
        moved since, so acting on its buttons would switch a lane against a
        promise the chat no longer makes."""
        if view is None or not view.date:
            return True
        return (self._now() - view.date) > MODEL_MENU_TTL_SECONDS

    # --- rendering ---------------------------------------------------------

    def _render(self, scope: str, choice: Choice, default: Pair) -> ModelMenu:
        """The keyboard for one scope, with a ✓ on what is chosen now."""
        if scope == SCOPE_DEFAULT:
            chosen_id, chosen_effort = default
            info = lookup(chosen_id or self._cfg.model)
            pair = (self._pair_label(chosen_id, chosen_effort) if chosen_id
                    else _MODEL_DEFAULT_UNSET.format(label=info.label))
            text = _MODEL_DEFAULT_HEAD.format(pair=pair)
        else:
            chosen_id, chosen_effort = choice.model, choice.effort
            info = lookup(chosen_id)
            text = _MODEL_HEAD.format(
                pair=self._pair_label(chosen_id, chosen_effort))

        rows: list[tuple[MenuButton, ...]] = []
        for candidate in self._offered:
            tick = _TICK if chosen_id and lookup(chosen_id).id == candidate.id else ""
            rows.append((MenuButton(f"{tick}{candidate.label}{_price(candidate)}",
                                    f"{MODEL_CALLBACK_PREFIX}:{scope}:{ACTION_MODEL}:"
                                    f"{candidate.id}"),))
        rows.extend(self._effort_rows(scope, info, chosen_effort))
        rows.append((MenuButton(
            _MODEL_DEFAULT_CLEAR_BUTTON if scope == SCOPE_DEFAULT
            else _MODEL_CLEAR_BUTTON,
            f"{MODEL_CALLBACK_PREFIX}:{scope}:{ACTION_CLEAR}:-"),))
        if scope == SCOPE_DEFAULT:
            rows.append((MenuButton(
                _MODEL_APPLY_BUTTON,
                f"{MODEL_CALLBACK_PREFIX}:{SCOPE_APPLY}:{ACTION_CLEAR}:-"),))
        return ModelMenu(text=text, rows=tuple(rows))

    def _effort_rows(self, scope: str, info: ModelInfo,
                     chosen: str | None) -> list[tuple[MenuButton, ...]]:
        """"Auto" plus the efforts THIS model takes. A model the catalog does not
        know has none, and then there is no effort row at all — better an absent
        row than buttons the CLI would silently drop."""
        if not info.efforts:
            return []
        buttons = [MenuButton(
            ("" if chosen else _TICK) + _auto_label(info),
            f"{MODEL_CALLBACK_PREFIX}:{scope}:{ACTION_EFFORT}:{AUTO_EFFORT}")]
        buttons += [
            MenuButton((_TICK if chosen == effort else "") + effort.capitalize(),
                       f"{MODEL_CALLBACK_PREFIX}:{scope}:{ACTION_EFFORT}:{effort}")
            for effort in info.efforts
        ]
        return [tuple(buttons[i:i + _EFFORTS_PER_ROW])
                for i in range(0, len(buttons), _EFFORTS_PER_ROW)]

    def _pair_label(self, model_id: str | None, effort: str | None) -> str:
        info = lookup(model_id or self._cfg.model)
        return f"{info.label} · {effort or _auto_label(info)}"


def _auto_label(info: ModelInfo) -> str:
    """"Auto (high)" — `effort=None` means the CLI picks, and the catalog knows
    what it picks. "Auto" alone for a model the catalog has never met."""
    if not info.default_effort:
        return _MODEL_AUTO_PLAIN
    return _MODEL_AUTO_BUTTON.format(effort=info.default_effort)


def _price(info: ModelInfo) -> str:
    """" · $4/$20" per 1M tokens, in and out — the one number that decides a
    switch. Silent for a model whose price the catalog does not know."""
    if not info.price_in and not info.price_out:
        return ""
    return f" · ${info.price_in:g}/${info.price_out:g}"

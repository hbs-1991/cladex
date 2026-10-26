"""What each Claude model IS, and the one rule for which model a lane runs.

`config.yaml` decides which models are OFFERED (`models:`); this module holds the
FACTS about them — window, prices, the efforts the model takes, and the spelling the
CLI needs — and the pure functions every module shares: `resolve()` picks a lane's
(model, effort), `validate()` checks a write, `cli_id()` is the only way a model id
reaches the CLI. Standard library only: it sits in `core`, every module imports it,
and import-linter follows import chains.

Every fact below was read out of the pinned CLI's own model catalog and proven live by
`scripts/model_matrix.py` under production auth (the CLI version is in
`pyproject.toml`). Re-run the matrix on every SDK/CLI bump and update this table from
its output; a model the matrix has not proven does not belong in the default
`models:` list. Last run: SDK 0.2.158 / CLI 2.1.280, 2026-09-23 — all four answered
as themselves with a fresh config dir, with no consent prompt and no usage-credit
wording, at every effort level; each gave a 1M window unsuffixed, so no `cli_id`
carries `[1m]` on this CLI (CLI 2.1.218 needed it for `claude-opus-5`).

Ids are the API's (`claude-opus-5-5`): that is what the database stores, the wire
carries and events name. `cli_id` is what the CLI is handed and may differ — a
`[1m]` suffix is how some CLI versions give a model its full window.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from claude_hermes.core.session_keys import is_group_lane

log = logging.getLogger(__name__)

EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
_SUFFIX = "[1m]"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    cli_id: str
    label: str
    window: int                    # tokens; 0 = unknown, and never blocks a choice
    price_in: float = 0.0          # USD per 1M input tokens; 0 = unknown
    price_out: float = 0.0         # USD per 1M output tokens
    cache_write: float = 1.25      # multiple of price_in a cold re-read costs
    efforts: tuple[str, ...] = ()
    default_effort: str | None = None   # what the CLI does when no effort is sent
    billing: str = "standard"      # "standard" | "usage_credits"
    aliases: tuple[str, ...] = field(default=())   # other spellings the API answered with


CATALOG: tuple[ModelInfo, ...] = (
    ModelInfo(id="claude-sonnet-5", cli_id="claude-sonnet-5", label="Sonnet 5",
              window=1_000_000, price_in=2.0, price_out=10.0,
              efforts=EFFORTS, default_effort="high"),
    ModelInfo(id="claude-opus-5", cli_id="claude-opus-5", label="Opus 5",
              window=1_000_000, price_in=5.0, price_out=25.0,
              efforts=EFFORTS, default_effort="high"),
    ModelInfo(id="claude-opus-5-5", cli_id="claude-opus-5-5", label="Opus 5.5",
              window=1_000_000, price_in=4.0, price_out=20.0,
              efforts=EFFORTS, default_effort="medium"),
    ModelInfo(id="claude-fable-5-1", cli_id="claude-fable-5-1", label="Fable 5.1",
              window=1_000_000, price_in=10.0, price_out=50.0,
              efforts=EFFORTS, default_effort="high"),
)
_BY_SPELLING = {spelling: m for m in CATALOG
                for spelling in (m.id, m.cli_id, *m.aliases)}


def normalize(model_id: str) -> str:
    """`claude-opus-5[1m]` -> `claude-opus-5`: the CLI's suffix is not part of the id."""
    model_id = (model_id or "").strip()
    return model_id[:-len(_SUFFIX)] if model_id.endswith(_SUFFIX) else model_id


def lookup(model_id: str) -> ModelInfo:
    """The catalog entry for any spelling — id, `cli_id`, an alias, with or without
    `[1m]`. An id the catalog does not know still resolves, to an entry that knows
    nothing: no window (so nothing is ever disabled for it), no price, no efforts.
    Every comparison of two model ids in the codebase goes through here."""
    raw = (model_id or "").strip()
    known = _BY_SPELLING.get(raw) or _BY_SPELLING.get(normalize(raw))
    if known is not None:
        return known
    return ModelInfo(id=normalize(raw), cli_id=raw, label=normalize(raw), window=0)


def cli_id(model_id: str) -> str:
    """The only way a model id reaches the CLI."""
    return lookup(model_id).cli_id


def offered(models, deploy_model: str, fork_model: str = "") -> list[ModelInfo]:
    """The configured list in order, the deploy default first if it is missing,
    duplicates (any spelling) dropped. Warns when an offered model holds more than
    the model the review fork resumes lanes on: that fork would fail on the
    longest lanes, silently."""
    configured = [str(m) for m in (models or [])]
    if deploy_model and lookup(deploy_model).id not in {lookup(m).id for m in configured}:
        configured.insert(0, deploy_model)
    out: list[ModelInfo] = []
    seen: set[str] = set()
    for model in configured:
        info = lookup(model)
        if info.id and info.id not in seen:
            seen.add(info.id)
            out.append(info)
    fork = lookup(fork_model or deploy_model)
    for info in out:
        if fork.window and info.window > fork.window:
            log.warning("offered model %s holds %d tokens but the review fork runs %s "
                        "with %d", info.id, info.window, fork.id, fork.window)
    return out


def lane_is_shared(session_key: str) -> bool:
    """A room (a Telegram group or forum topic) or a routine's lane: nobody's
    personal default applies there, and only the deploy owner changes its model.
    A private chat's topics are that chat's user's own, like the chat."""
    return is_group_lane(session_key) or session_key.startswith("cron:")


@dataclass(frozen=True)
class Choice:
    model: str                 # catalog id
    cli_id: str                # what the CLI is handed
    effort: str | None         # None = send no effort flag


Pair = tuple[str | None, str | None]


def _usable(pair: Pair | None, offered_ids: set[str]) -> Choice | None:
    if not pair or not pair[0]:
        return None
    info = lookup(pair[0])
    if info.id not in offered_ids:
        return None
    effort = pair[1] if pair[1] in info.efforts else None
    return Choice(info.id, info.cli_id, effort)


def resolve(thread: Pair | None, session: Pair | None, owner_default: Pair | None, *,
            shared: bool, deploy_model: str, offered_ids) -> Choice:
    """The lane's (model, effort) for the next turn, in the one order there is:

    1. the lane's explicit choice (`threads.model/effort`);
    2. what its LIVE session runs (`sessions.model/effort`) — a changed default
       waits for the next session, because a live one's cached prefix is
       model-scoped (P1);
    3. the lane owner's default — never on a shared lane;
    4. the deploy default, with no effort.

    A pair whose model is no longer offered is skipped; an effort the model does
    not take becomes None. The caller decides what "live" means (the session
    router's own expiry rule) and passes None for a session that is not.
    """
    ids = {lookup(m).id for m in (offered_ids or ())}
    ids.add(lookup(deploy_model).id)
    for pair in (thread, session, None if shared else owner_default):
        choice = _usable(pair, ids)
        if choice is not None:
            return choice
    info = lookup(deploy_model)
    return Choice(info.id, info.cli_id, None)


def validate(model: str | None, effort: str | None, offered_ids) -> None:
    """Refuse a write the resolver would silently ignore. `model=None` is always
    fine: it clears a choice. Raises `ValueError("bad_model" | "bad_effort")`."""
    if model is None:
        return
    info = lookup(model)
    if info.id not in {lookup(m).id for m in (offered_ids or ())}:
        raise ValueError("bad_model")
    if effort is not None and effort not in info.efforts:
        raise ValueError("bad_effort")


_MODEL_COMMAND = re.compile(r"^\s*/(model|effort)(\s|@|$)")


def is_model_command(text: str | None) -> bool:
    """`/model x` or `/effort x` sent as a turn: the CLI runs both as its own
    local commands in non-interactive mode and would switch the session's model
    with nothing recorded anywhere. Every path refuses them; `/models` is fine."""
    return bool(_MODEL_COMMAND.match(text or ""))

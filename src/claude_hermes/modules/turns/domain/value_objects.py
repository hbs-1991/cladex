"""The values a turn carries: its context, the side-channel flags a tool raises,
and the two functions that describe attachments to the model and to the search
index.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Distinct from None (which means "timed out") and unproducible as an option
# label, so the ask_user tool can tell "the user declined" from "the user never
# answered".
CANCELLED = "\x00cancelled"


@dataclass
class TurnFlags:
    """Mutable side-channel from a tool back to the runner. A tool cannot reset the
    session itself: the runner writes the session row back *after* the turn, so a
    mid-turn delete would simply be re-inserted. It raises the flag; the runner acts
    on it once the turn is over."""
    reset_lane: bool = False
    asks_used: int = 0


@dataclass(frozen=True)
class TurnContext:
    session_key: str
    chat_id: int
    thread_id: int | None = None
    user_id: int = 0
    flags: TurnFlags = field(default_factory=TurnFlags)
    # Turn identity and the event sink, set by the runner. `emit(type, payload)`
    # publishes into the TurnBus for this turn; None in forks that have no bus.
    turn_id: str = ""
    emit: Any = None
    # Wave 4a: WHOSE tree this turn runs in — a `core.workspace.Workspace` for the
    # lane's owner (D6). Typed `Any` and never imported: a domain layer may not
    # reach `structlog`, which `core.workspace` uses, and import-linter follows
    # chains. `None` means the caller resolved nothing and the executor falls back
    # to the deploy owner's tree.
    workspace: Any = None
    # Whether that owner IS the deploy owner. An owner lane (their private and web
    # lanes, every group lane, their `cron:` lanes) gets the whole toolset and the
    # learning observer without asking anybody; every other lane gets `Bash`/
    # `Agent`/`Task` only with `shell_enabled` and the observer only with
    # `learning_enabled` below. (`skill_admin` is not shell-gated: it exists on
    # every private/web lane and on no group lane — a property of the lane's shape,
    # decided by the executor.) Defaults to True so a fork that builds a bare
    # context keeps the pre-4a reach.
    is_owner_lane: bool = True
    # Wave 4a (D7): the lane's owner id — `lane_owner(session_key, channel,
    # user_id, deploy_owner)`, decided once by the runner and carried, never
    # re-derived. It is what every row this turn writes is stamped with
    # (`messages.owner_id`, `turn_costs.owner_id`, `files.owner_id`, a routine's
    # `jobs.owner_id`) and what `session_search` and `cost_report` scope their
    # reads to, so the turn cannot read or write across the boundary its own
    # workspace draws. Distinct from `user_id`, which is the SENDER: in a group
    # lane a stranger's message writes rows owned by the deploy owner. `0` is a
    # fork with no lane and means "unscoped", which is the pre-4a behaviour.
    owner_id: int = 0
    # Which model this turn asked for, resolved ONCE by `TurnService` out of
    # `LaneModels.effective` before the first event and carried from here on —
    # the same rule as `owner_id`, for the same reason: a second lookup is a
    # second thing that can disagree, and `turn.start` has already named it.
    #
    # `model` is the catalog id (`claude-opus-5`) — what the database stores, the
    # wire carries and events name. `cli_model` is the spelling the CLI is handed
    # (`core.model_catalog.cli_id`), which on some CLI versions carries a `[1m]`
    # suffix the id never has. `effort` is `None` for "send no effort flag",
    # which is not the same as the model's own default.
    #
    # All three unset is a fork or a direct caller that resolved nothing; the
    # executor then falls back to `TurnSettings.model`, the deploy default, which
    # is what every lane ran before this feature.
    model: str = ""
    cli_model: str = ""
    effort: str | None = None
    # The lane owner's per-user grants (0009), resolved ONCE per turn by
    # `TurnService` through `LaneGrants` — only for a non-owner lane; an owner lane
    # needs neither, because `is_owner_lane` already gives it everything.
    #
    # Both default to **False**, and that is the fail-closed side on purpose: a
    # fork or a direct caller that builds a bare context either leaves
    # `is_owner_lane=True` (and `builtin_tools`/`denied_tools` hand it the full
    # toolset through that) or says `is_owner_lane=False` — and then a True default
    # could only ever WIDEN a non-owner context nobody resolved a grant for.
    shell_enabled: bool = False
    learning_enabled: bool = False

    @property
    def has_shell(self) -> bool:
        """Does this turn get `Bash`/`Agent`/`Task`? The ONE value the tool policy
        is built from, the session is stamped with and the next turn compares
        against — computed here so nothing re-derives it a second way."""
        return self.is_owner_lane or self.shell_enabled


@dataclass(frozen=True)
class ComposedTools:
    """What the extensions module contributes to one turn's SDK options. A plain
    shape so `turns` never imports the extensions module to name it."""
    tool_names: list[str] = field(default_factory=list)
    mcp_servers: dict = field(default_factory=dict)
    plugins: list = field(default_factory=list)


def _compose_text(user_text: str, attachments) -> str:
    """The one description of the attachments, used for BOTH the model's text
    block and the messages_fts row. One function on purpose: if the searchable
    history and what the model actually saw were built separately they would
    drift, and the drift would be invisible until someone searched for a file
    the agent had already read."""
    if not attachments:
        return user_text
    lines = [f"[attachment: {a.filename} — saved at {a.path}]" for a in attachments]
    return "\n".join([user_text, *lines])


def _attachment_wire(a) -> dict:
    """The user.message attachment record. Web uploads carry a file_id (set by the
    turns router); Telegram attachments have none and are shown by name."""
    return {
        "file_id": a.file_id,
        "name": a.filename,
        "media_type": a.media_type,
        "size": a.path.stat().st_size if a.path.exists() else 0,
        "url": f"/api/files/{a.file_id}" if a.file_id else None,
    }


compose_text = _compose_text
attachment_wire = _attachment_wire

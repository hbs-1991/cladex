"""The hooks that enforce the wiki's rules on any agent turn or fork.

Moved verbatim from `hooks.py` in Wave 2. They are infrastructure
because they speak the SDK's hook envelope (`hookSpecificOutput`, `permissionDecision`),
not because they are incidental — `raw_guard_factory` is the ONLY thing making
`wiki/raw/` immutable, so it travels with every fork that can write.

`wiki_hooks()` is the provider the composition root uses: it hands back SDK
`HookMatcher`s grouped by event, and the caller merges them with whatever else it
installs (the runner adds its own `Bash` guard and the per-lane path guard, the
review fork its skills write guard). Merging, not replacing — every list here is
additive per event name.

Every factory takes `wiki_root` as an argument and holds no global, which is what
made Wave 4a's per-user wiki a wiring change rather than a rewrite:
`WikiHooksProvider` now passes the LANE OWNER's root (from
`TurnContext.workspace`), so the `raw/` guard, the page-touch recorder and the
Stop gate all watch the tree the turn is actually writing in.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from claude_agent_sdk import HookMatcher

from ..application.ports import WikiIndexerPort, WikiIndexPort

log = structlog.get_logger(__name__)

_INDEX = "index.md"
_LOG = "log.md"

# The flush runs while the SDK waits to return the turn's reply. A swallowed
# exception is not the failure mode to fear here; a hung connection holding the
# answer is, so the whole call is bounded.
FLUSH_TIMEOUT_SECONDS = 5.0

# Workspace roots already warned about. A lane that HAS a workspace but was given
# no owner indexes nothing — that is a call site that forgot the kwarg, and it
# would otherwise index nothing forever in silence. Once per process, because the
# alternative is a line per turn.
_UNINDEXED_REPORTED: set[str] = set()

_INGEST_HINT = (
    "You just fetched an external source. If it is worth keeping, load the `wiki-ingest` "
    "skill and file it: the text under wiki/raw/, a page under wiki/topics/, links into the "
    "pages it touches, then the index and the log. If it was throwaway, ignore this."
)


@dataclass
class WikiTurnState:
    """What this turn did to the wiki. One instance per turn; the hooks share it."""

    pages: set[str] = field(default_factory=set)
    index_written: bool = False
    log_written: bool = False
    fetch_hinted: bool = False


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _target(input_data: dict, base: Path | None = None) -> Path | None:
    """The file a Write/Edit names, resolved. `base` is the lane's cwd (its
    `agent_home`): the wiki rules say "write pages under ./wiki/", so a relative
    `wiki/topics/x.md` must land where the CLI lands it, not where this worker
    process happens to run — otherwise the page is never recorded as touched and
    never flushed to the index until the next lint."""
    raw = str((input_data.get("tool_input") or {}).get("file_path", ""))
    if not raw:
        return None
    try:
        path = Path(raw)
        if base is not None and not path.is_absolute():
            path = base / path
        return path.resolve()
    except (OSError, ValueError):
        return None


def raw_guard_factory(wiki_root: Path, *, cwd: Path | None = None):
    """PreToolUse(Write|Edit): wiki/raw/ is immutable.

    A source that can be quietly amended is not a source — and lint's ability to
    check a claim against its origin depends on the origin being untouched.
    """
    raw_root = (wiki_root / "raw").resolve()
    base = Path(cwd).resolve() if cwd is not None else None

    async def guard(input_data: dict, tool_use_id, context) -> dict:
        path = _target(input_data, base)
        if path is None or not path.is_relative_to(raw_root):
            return {}
        if input_data.get("tool_name") == "Edit":
            return _deny(
                "wiki/raw/ is immutable: never edit a source. Write a page about it instead."
            )
        if path.exists():
            return _deny(
                f"wiki/raw/{path.name} already exists and sources are immutable. "
                "Use a new filename, or write a page instead."
            )
        return {}

    return guard


def page_touch_factory(state: WikiTurnState, wiki_root: Path, *, cwd: Path | None = None):
    """PostToolUse(Write|Edit): remember what this turn did to the wiki."""
    root = wiki_root.resolve()
    base = Path(cwd).resolve() if cwd is not None else None

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        path = _target(input_data, base)
        if path is None or not path.is_relative_to(root):
            return {}
        rel = path.relative_to(root).as_posix()
        if rel == _INDEX:
            state.index_written = True
        elif rel == _LOG:
            state.log_written = True
        elif not rel.startswith("raw/"):
            state.pages.add(rel)
        return {}

    return hook


def stop_gate_factory(state: WikiTurnState):
    """Stop: a page changed without the index and the log is a wiki that lies."""

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        if not state.pages:
            return {}
        missing = [
            name
            for name, done in (("wiki/index.md", state.index_written),
                               ("wiki/log.md", state.log_written))
            if not done
        ]
        if not missing:
            return {}
        if input_data.get("stop_hook_active"):
            # Already nudged once this turn. Blocking again would trap the turn;
            # let it finish and leave the drift to the lint pass.
            log.warning(
                "wiki drift: pages %s written without %s",
                sorted(state.pages), ", ".join(missing),
            )
            return {}
        pages = ", ".join(sorted(state.pages))
        return {
            "decision": "block",
            "reason": (
                f"You changed wiki pages ({pages}) but did not update "
                f"{' and '.join(missing)}. Do that now — one line per page in the index, "
                "one line in the log — then finish."
            ),
        }

    return hook


def _say_unindexed_once(workspace: Any) -> None:
    key = str(getattr(workspace, "root", "")) or "<no workspace>"
    if key in _UNINDEXED_REPORTED:
        return
    _UNINDEXED_REPORTED.add(key)
    log.warning("wiki index: a turn touched pages with no lane owner; nothing indexed",
                root=key)


def index_flush_factory(state: WikiTurnState, indexer: WikiIndexerPort,
                        owner_id: int, workspace: Any):
    """Stop: re-index the pages this turn changed, or give up quietly.

    Installed after `stop_gate_factory` on the same event. The gate gets its
    chance to block first; flushing is idempotent, so nothing here depends on
    whether the SDK runs later hooks after a `block` — and the code must not
    claim either way.
    """

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        if not state.pages:
            return {}
        if owner_id <= 0:
            if workspace is not None:
                _say_unindexed_once(workspace)
            return {}
        try:
            await asyncio.wait_for(
                indexer.touch(owner_id, workspace, set(state.pages)),
                FLUSH_TIMEOUT_SECONDS)
        except Exception:
            # `asyncio.TimeoutError` is an `Exception` since 3.11 and is the case
            # this exists for. Either way the turn is finished and the next lint
            # or boot converges the table.
            log.exception("wiki index flush failed", owner_id=owner_id)
        return {}

    return hook


def webfetch_hint_factory(state: WikiTurnState):
    """PostToolUse(WebFetch): point at the ingest skill. Once per turn, not per fetch."""

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        if state.fetch_hinted:
            return {}
        state.fetch_hinted = True
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": _INGEST_HINT,
            }
        }

    return hook


def index_refresh_factory(wiki: WikiIndexPort, seen: dict[str, float], session_key: str):
    """UserPromptSubmit: the index in the prompt is a snapshot; keep it honest.

    In an eternal thread the system prompt is frozen, so a page written by a
    background pass would otherwise stay invisible. Fires only when the index
    actually moved — an unchanged wiki costs zero tokens.
    """

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        mtime = wiki.index_mtime()
        if mtime <= seen.get(session_key, 0.0):
            return {}
        seen[session_key] = mtime
        index = wiki.index()
        if not index:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "The wiki changed since this session started. Current wiki/index.md:\n\n"
                    + index
                ),
            }
        }

    return hook


def wiki_hooks(
    wiki_root: Path,
    state: WikiTurnState,
    *,
    index: WikiIndexPort | None = None,
    seen: dict[str, float] | None = None,
    session_key: str = "",
    webfetch_hint: bool = False,
    indexer: WikiIndexerPort | None = None,
    owner_id: int = 0,
    workspace: Any = None,
) -> dict[str, list[HookMatcher]]:
    """Memory's contribution to a fork's hook table, grouped by SDK event name.

    The optional halves match what the call sites actually install today: the
    foreground turn takes the WebFetch ingest hint and the index refresh (it has a
    session to refresh for); the review and lint forks take neither.

    `indexer` is the third: a composition that wired no index gets the hook table
    it has always got, byte for byte. With one, the Stop list gains the flush
    AFTER the gate — the pages a turn wrote have to be searchable in the next
    turn, or `memory_search` answers with a note the model has already replaced.
    """
    hooks: dict[str, list[HookMatcher]] = {
        "PreToolUse": [
            # `cwd=wiki_root.parent`: the lane's `agent_home`, which is what
            # `base_options` sets as the CLI's cwd, so a relative `wiki/x.md`
            # resolves where the CLI writes it (advisor B-8, same as the guards).
            HookMatcher(matcher="Write|Edit",
                        hooks=[raw_guard_factory(wiki_root, cwd=wiki_root.parent)]),
        ],
        "PostToolUse": [
            HookMatcher(matcher="Write|Edit",
                        hooks=[page_touch_factory(state, wiki_root, cwd=wiki_root.parent)]),
        ],
        "Stop": [HookMatcher(hooks=[stop_gate_factory(state)])],
    }
    if indexer is not None:
        hooks["Stop"][0].hooks.append(
            index_flush_factory(state, indexer, owner_id, workspace))
    if webfetch_hint:
        hooks["PostToolUse"].append(
            HookMatcher(matcher="WebFetch", hooks=[webfetch_hint_factory(state)])
        )
    if index is not None and seen is not None:
        hooks["UserPromptSubmit"] = [
            HookMatcher(hooks=[index_refresh_factory(index, seen, session_key)])
        ]
    return hooks

"""The two detached agent forks learning starts, over `claude_agent_sdk`.

The option tables move verbatim from `learning/review.py` and
`learning/curator.py`: which tools exist, which guards are installed, whether the
fork resumes a session. They are the security boundary of both passes, so they
live in one file that the application layer only ever sees as `ForkRunner`.

Memory's wiki hooks arrive as `WikiGuardsPort` — `raw/` immutability and the
"update the index before you stop" gate are memory's rules, installed here.

Since Wave 4b every fork runs in ITS workspace, confined like a turn (D4): the
lane owner's `cwd` and `CLAUDE_CONFIG_DIR`, the same `path_guard_factory` the
foreground turn installs, and `Bash`/`Agent`/`Task` removed by `disallowed_tools`
— the belt 4a learned is the one that means "cannot" under `bypassPermissions`.
`workspace=None` keeps the deploy owner's tree, which is what every pre-4b caller
means.
"""
from __future__ import annotations

from pathlib import Path

import structlog
from claude_agent_sdk import HookMatcher, query

from claude_hermes.core.config import NO_ADVISOR_ENV, Config
from claude_hermes.core.model_catalog import cli_id
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import (
    PATH_TOOLS,
    WRITE_TOOLS,
    path_guard_factory,
    readonly_guard_factory,
    write_guard_factory,
)
from claude_hermes.integrations.claude_sdk.options import base_options, workspace_env
from claude_hermes.modules.learning.application.ports import WikiGuardsPort
from claude_hermes.modules.learning.domain.constants import (
    CONSOLIDATION_TOOLS,
    REVIEW_BUILTIN_TOOLS,
    REVIEW_TOOLS,
)
from claude_hermes.modules.learning.infrastructure.fork_usage import drain_fork
from claude_hermes.modules.learning.infrastructure.guards import skills_write_guard_factory

log = structlog.get_logger(__name__)

# The tools a background fork must never have, whatever its `tools=` list says.
# `tools=` already omits them, but under `bypassPermissions` only
# `disallowed_tools` is the CLI's `--disallowedTools` — the control that takes a
# tool away rather than the list that auto-approves it (4a's live smoke watched a
# lane run `Bash` with `Bash` absent from `allowed_tools`).
FORK_DISALLOWED_TOOLS = ["Bash", "Agent", "Task"]


def fork_roots(workspace: Workspace) -> list[Path]:
    """What a fork's file tools may reach — the SAME list a foreground turn gets
    (4a D6), for one rule. `plugins_dir` is in it for that reason only (no fork
    loads a plugin); the read-only catalog root is NOT (no fork reads a
    manifest; advisor S-2/S-3).

    A non-owner's fork also reads the deploy owner's `wiki/shared/`, for the same
    reason their turn does: a review that cannot see the household pages will
    happily write a private duplicate of one. The owner needs no entry — their
    shared tree is already inside `agent_home`."""
    roots = [workspace.agent_home, workspace.tmp_dir, workspace.logs_dir,
             workspace.plugins_dir]
    if not getattr(workspace, "is_owner", True):
        roots.append(workspace.shared_wiki_root)
    return roots


def fork_path_guard(workspace: Workspace) -> HookMatcher:
    """The read/write confinement a per-user fork runs under."""
    return HookMatcher(matcher=PATH_TOOLS, hooks=[path_guard_factory(
        fork_roots(workspace), cwd=workspace.agent_home)])


def fork_readonly_fence(workspace: Workspace) -> HookMatcher | None:
    """The shared namespace as a denied write root, or `None` for its owner.

    BOTH spellings, like the foreground turn's: the owner's `wiki/shared/`, which
    `fork_roots` just made readable, and the fork's OWN `wiki/shared/`, because a
    relative `wiki/shared/x.md` resolves into its own tree. The review fork's
    `write_guard_factory([skills_dir, wiki_root], …)` already excludes the first
    — it is not under this user's `wiki_root` — and admits the second, which is
    exactly the silent private copy this fence refuses.
    """
    if getattr(workspace, "is_owner", True):
        return None
    return HookMatcher(matcher=WRITE_TOOLS, hooks=[readonly_guard_factory(
        [workspace.shared_wiki_root, workspace.wiki_root / "shared"],
        cwd=workspace.agent_home)])


def _index_flush(guards: WikiGuardsPort, state: object, owner_id: int,
                 workspace: Workspace) -> object | None:
    """The review fork's Stop-hook flush, or `None` when no index is wired.

    Read off the port with `getattr` because `WikiGuardsPort` grew this method
    after the two forks existed: a composition whose guards predate the wiki
    index is a fork that writes no rows, never a review that dies on an
    `AttributeError` at option-build time.
    """
    flush = getattr(guards, "index_flush", None)
    if flush is None:
        return None
    return flush(state, owner_id, workspace)


class SdkForkRunner:
    """`ForkRunner` over the Claude Agent SDK.

    `home` is the deployment root, and `Workspace.owner(home)` is the tree a
    call without `workspace=` runs in; `base_options(workspace.root)` puts the
    fork in that tree's `agent_home` with its project settings loaded, which is
    what makes a review fork see the same skills and wiki the foreground turn
    saw.
    """

    def __init__(self, cfg: Config, home: Path, wiki_guards: WikiGuardsPort):
        self._cfg = cfg
        self._home = home
        self._guards = wiki_guards

    def _workspace(self, workspace: Workspace | None) -> Workspace:
        return workspace if workspace is not None else Workspace.owner(self._home)

    async def run_review(self, *, prompt: str, system_append: str, resume: str,
                         skills_dir: Path, wiki_root: Path, touched: set[str],
                         workspace: Workspace | None = None,
                         owner_id: int = 0) -> str | None:
        cfg = self._cfg
        ws = self._workspace(workspace)
        state = self._guards.new_turn_state()
        # This fork is the wiki's main rewriter — curation runs out of band
        # (P3) — so the rows for the pages it touched are flushed when it stops.
        # Without it `memory_search` quotes a snippet the fork already rewrote,
        # as memory, until the next lint or boot. `owner_id == 0` means no lane
        # owner was resolved, and memory reads that as "do not index".
        flush = _index_flush(self._guards, state, owner_id, ws)
        fence = fork_readonly_fence(ws)
        options = base_options(
            ws.root,
            # `cli_id` is the one spelling a model reaches the CLI in, here as in
            # the foreground turn. No effort: a fork is background work nobody is
            # waiting on, and an effort flag would be spend with no reader.
            model=cli_id(cfg.review_model or cfg.model),
            max_turns=cfg.review_max_turns,
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": system_append},
            tools=REVIEW_BUILTIN_TOOLS,
            allowed_tools=REVIEW_TOOLS,
            disallowed_tools=list(FORK_DISALLOWED_TOOLS),
            hooks={
                "PreToolUse": [
                    fork_path_guard(ws),
                    HookMatcher(
                        matcher="Write|Edit",
                        # `cwd=` so a relative path resolves where the path
                        # guard above resolves it (B-8).
                        hooks=[write_guard_factory([skills_dir, wiki_root],
                                                   skills_root=skills_dir,
                                                   touched=touched,
                                                   cwd=ws.agent_home),
                               self._guards.raw_guard(wiki_root)],
                    ),
                    *([fence] if fence is not None else []),
                ],
                "PostToolUse": [HookMatcher(
                    matcher="Write|Edit",
                    hooks=[self._guards.page_touch(state, wiki_root)],
                )],
                # The flush comes AFTER the gate, never instead of it: the gate
                # is what makes the fork update `index.md` before it stops, and
                # the flush is what makes the rows match the tree it changed.
                # Flushing twice is idempotent, so whether the SDK runs later
                # hooks after a `block` does not matter and is not claimed here.
                "Stop": [HookMatcher(hooks=[self._guards.stop_gate(state)]
                                     + ([flush] if flush is not None else []))],
            },
            resume=resume,
            fork_session=True,
            # MERGED over `NO_ADVISOR_ENV`, never replacing it. `env` is one dict
            # the SDK lays over `os.environ`, so a replace would drop
            # `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and silently re-enable the
            # advisor on a background fork that has no user to advise. The
            # workspace's `CLAUDE_CONFIG_DIR` is what makes `resume` read THAT
            # user's transcript store rather than the owner's.
            env={**NO_ADVISOR_ENV, **workspace_env(ws)},
        )

        return await drain_fork(log, "review", query(prompt=prompt, options=options))

    async def run_consolidation(self, *, prompt: str, skills_dir: Path,
                                workspace: Workspace | None = None) -> str | None:
        """Fresh context over the library (no resume/fork_session — there is no
        conversation to fork from), no MCP server: this pass touches skills only,
        never the wiki."""
        cfg = self._cfg
        ws = self._workspace(workspace)
        fence = fork_readonly_fence(ws)
        options = base_options(
            ws.root,
            model=cli_id(cfg.curator_model or cfg.review_model or cfg.model),
            max_turns=cfg.curator_max_turns,
            tools=CONSOLIDATION_TOOLS,
            allowed_tools=CONSOLIDATION_TOOLS,
            disallowed_tools=list(FORK_DISALLOWED_TOOLS),
            # No Stop flush: this pass touches skills only, so there is no wiki
            # page whose row could have gone stale. The shared fence is still
            # installed — a curator that "tidies" the owner's shared pages would
            # be editing somebody else's memory.
            hooks={"PreToolUse": [
                fork_path_guard(ws),
                HookMatcher(
                    matcher="Write|Edit",
                    hooks=[skills_write_guard_factory(skills_dir, cwd=ws.agent_home)],
                ),
                *([fence] if fence is not None else []),
            ]},
            # MERGED over `NO_ADVISOR_ENV`, never replacing it (see `run_review`).
            env={**NO_ADVISOR_ENV, **workspace_env(ws)},
        )
        return await drain_fork(log, "curator", query(prompt=prompt, options=options))

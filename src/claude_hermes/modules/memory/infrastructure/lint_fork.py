"""The SDK half of the wiki lint pass: options, guards, and the fork loop.

Split out of `learning/curator.run_wiki_lint` in Wave 2. Everything that touches
`claude_agent_sdk` lives here; the decision to run and the swallow-and-log live in
`application/services/lint.py`.

`query` is kept as a module-level name on purpose: a test that wants a fake fork
patches it here, the same way it patched `curator_mod.query`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import structlog
from claude_agent_sdk import HookMatcher, query

from claude_hermes.core.config import NO_ADVISOR_ENV, Config
from claude_hermes.core.model_catalog import cli_id, lookup
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import (
    PATH_TOOLS,
    WRITE_TOOLS,
    path_guard_factory,
    readonly_guard_factory,
    write_guard_factory,
)
from claude_hermes.integrations.claude_sdk.options import base_options, workspace_env

from ..application.ports import ForkRunner
from ..application.services.lint import WikiLintService
from ..domain.constants import WIKI_LINT_TOOLS
from .turn_hooks import raw_guard_factory

log = structlog.get_logger(__name__)

# The CLI's stand-in when nothing answered, and its own Haiku side call: neither
# is ever "the model that ran" (recorded live, story Dev Notes).
_SYNTHETIC_MODEL = "<synthetic>"

# Removed by `disallowed_tools`, whatever `tools=` says: under `bypassPermissions`
# that is the one control that takes a tool away (4a's live smoke watched a lane
# run `Bash` with `Bash` absent from `allowed_tools`).
_LINT_DISALLOWED_TOOLS = ["Bash", "Agent", "Task"]


class SdkForkRunner:
    """Satisfies `ForkRunner`: drive one fork, keep the last `result` it emitted,
    and say which model answered it.

    The model is logged rather than returned because nobody up the chain acts on
    it: the lint pass runs on the deploy's fork model whatever the lane is on, so
    the only question this answers is the operator's — "did this fork run where I
    think it did, and did it re-read the wiki cold?". The cache share is the
    second half of that: `cache_read / (input + cache_read + cache_creation)` of
    the main-loop calls, which is the figure the NFR-1 target is stated in.
    """

    async def run(self, prompt: str, options: object) -> str | None:
        final: str | None = None
        ran = ""
        totals = {"input_tokens": 0, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}
        async for msg in query(prompt=prompt, options=options):
            model = getattr(msg, "model", None)
            # Only a main-loop `AssistantMessage` carries both a model and a
            # per-step usage; a `ResultMessage`'s usage is the SUM over every call
            # of the fork and would double-count everything below.
            if model and getattr(msg, "parent_tool_use_id", None) is None:
                if model != _SYNTHETIC_MODEL:
                    ran = lookup(model).id
                usage = getattr(msg, "usage", None)
                if isinstance(usage, dict):
                    for key in totals:
                        totals[key] += int(usage.get(key) or 0)
            if getattr(msg, "result", None):
                final = msg.result
        prompt_tokens = sum(totals.values())
        log.info("wiki_lint_fork_ran", model=ran or None,
                 cache_read_share=(round(totals["cache_read_input_tokens"]
                                         / prompt_tokens, 3)
                                   if prompt_tokens else None))
        return final


class WikiLintOptions:
    """Satisfies `LintOptionsFactory`.

    A dry run gets an empty root list, so the guard denies every write: read-only
    by construction, not by asking the model nicely. `raw_guard_factory` is installed
    either way — `wiki/raw/` is immutable even to a pass that may write pages.

    Since Wave 4b the fork runs in `workspace` (D4): that tree's `cwd` and
    `CLAUDE_CONFIG_DIR`, and the SAME `path_guard_factory` a foreground turn
    installs, so a non-owner's lint cannot read the owner's wiki while fixing
    their own. `None` is the deploy owner's tree — the pre-4b meaning.

    The one hole that opens in is the SHARED namespace: a non-owner's lint reads
    the owner's `wiki/shared/` (an extra read root) and must not write it, in
    either spelling — the owner's absolute path, and the `shared/` folder inside
    the user's OWN wiki, which a relative `wiki/shared/x.md` resolves to and which
    the write guard would otherwise wave through as a private page. For the owner
    the two paths are one directory and no fence is installed.
    """

    def __init__(self, cfg: Config, home: Path, wiki_root: Path,
                 workspace: Workspace | None = None):
        self._cfg = cfg
        self._home = home
        self._root = wiki_root
        self._workspace = workspace if workspace is not None else Workspace.owner(home)

    @property
    def workspace(self) -> Workspace:
        return self._workspace

    def build(self, *, dry_run: bool):
        cfg = self._cfg
        ws = self._workspace
        roots = [] if dry_run else [self._root]
        read_roots = [ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir]
        write_hooks = [write_guard_factory(roots, cwd=ws.agent_home),
                       raw_guard_factory(self._root, cwd=ws.agent_home)]
        if not ws.is_owner:
            read_roots.append(ws.shared_wiki_root)
            write_hooks.append(readonly_guard_factory(
                [ws.shared_wiki_root, ws.wiki_root / "shared"], cwd=ws.agent_home))
        return base_options(
            ws.root,
            # `cli_id` is the one spelling a model reaches the CLI in, here as in
            # the foreground turn, and no effort: nobody waits on a lint pass.
            model=cli_id(cfg.curator_model or cfg.review_model or cfg.model),
            max_turns=cfg.curator_max_turns,
            tools=WIKI_LINT_TOOLS,
            allowed_tools=WIKI_LINT_TOOLS,
            disallowed_tools=list(_LINT_DISALLOWED_TOOLS),
            hooks={"PreToolUse": [
                # The read confinement, same roots as a turn (4a D6): the lane's
                # `agent_home`, `tmp/`, `logs/` and the shared plugin clones, plus
                # the shared wiki for a non-owner. No catalog root — no fork reads
                # a manifest.
                HookMatcher(matcher=PATH_TOOLS,
                            hooks=[path_guard_factory(read_roots, cwd=ws.agent_home)]),
                HookMatcher(
                    # `WRITE_TOOLS`, not `Write|Edit`: `tools=` keeps MultiEdit
                    # and NotebookEdit out of this fork, but only a matcher that
                    # names them is a fence if a CLI build offers them anyway.
                    matcher=WRITE_TOOLS,
                    # Every hook here takes `cwd=` so a relative path resolves
                    # where the path guard resolves it (advisor B-8): stacked on
                    # one matcher, two hooks with different cwds would disagree
                    # about one string.
                    hooks=write_hooks,
                ),
            ]},
            # MERGED over `NO_ADVISOR_ENV`, never replacing it. `env` is one dict
            # the SDK lays over `os.environ`, so a replace would drop
            # `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and silently re-enable the
            # advisor on a background fork that has no user to advise.
            env={**NO_ADVISOR_ENV, **workspace_env(ws)},
        )


def build_wiki_lint_service(
    cfg: Config, home: Path, wiki_root: Path, runner: ForkRunner | None = None,
    workspace: Workspace | None = None, *,
    after_lint: Callable[[], Awaitable[None]] | None = None,
) -> WikiLintService:
    """The `WikiLintPort` the curator consumes, assembled — over one scope's tree
    when `workspace` is given, the deploy owner's otherwise.

    `after_lint` is where the composition root hangs the index refresh, so a pass
    that rewrote pages out of band does not leave `memory_search` quoting them.
    """
    return WikiLintService(wiki_root, runner or SdkForkRunner(),
                           WikiLintOptions(cfg, home, wiki_root, workspace),
                           workspace=workspace, after_lint=after_lint)

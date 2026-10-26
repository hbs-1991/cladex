"""The wiki lint pass: orchestration only, no SDK.

`run_wiki_lint` in `learning/curator.py` did three things at once — decide whether
there is anything to lint, build SDK options, drive the fork. The decision stays
here; the SDK half moved behind `ForkRunner` / `LintOptionsFactory` so the
application layer stays framework-free (import-linter enforces it).

Two behaviours are load-bearing and preserved exactly:

* **A dry run is read-only by construction**, not by asking the model nicely: the
  options factory gets `dry_run=True` and hands the write guard an empty root list,
  so every Write/Edit is denied.
* **A failed fork returns None, never raises.** The curator runs unattended; an SDK
  blow-up must not take the pass down with it.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import structlog

from ...domain.prompts import build_wiki_lint_prompt
from ..ports import ForkRunner, LintOptionsFactory

log = structlog.get_logger(__name__)


class WikiLintService:
    """Satisfies `WikiLintPort`.

    `workspace` (Wave 4b) names the tree this pass is over — the options
    factory is what actually confines the fork to it; the service carries it so
    the curator sweep can say whose lint it ran. `None` is the deploy owner's.
    It is also what decides whether the prompt tells the fork that `shared/` is
    somebody else's (duck-typed on `is_owner`; `None` is the owner, so `False`).

    `after_lint` is the derived index's refresh. The fork rewrites pages nobody
    watched it rewrite, so a wet pass leaves the index stale; the composition root
    binds this to `WikiIndexService.reindex`, and the application layer stays
    unaware that there is a database at all.
    """

    def __init__(self, wiki_root: Path, runner: ForkRunner, options: LintOptionsFactory,
                 *, workspace: Any = None,
                 after_lint: Callable[[], Awaitable[None]] | None = None):
        self._root = wiki_root
        self._runner = runner
        self._options = options
        self._workspace = workspace
        self._after_lint = after_lint

    @property
    def workspace(self) -> Any:
        return self._workspace

    async def lint(self, dry_run: bool = False) -> str | None:
        """Health-check the wiki. Fresh context — there is no conversation to fork from."""
        if not self._root.is_dir():
            return None
        shared_readonly = (self._workspace is not None
                           and not getattr(self._workspace, "is_owner", True))
        try:
            result = await self._runner.run(
                build_wiki_lint_prompt(dry_run, shared_readonly=shared_readonly),
                self._options.build(dry_run=dry_run),
            )
        except Exception:
            log.exception("curator: wiki lint fork failed")
            return None
        # A dry run changed no byte, and a fork that returned nothing did not
        # finish — neither is worth a reindex.
        if result is not None and not dry_run and self._after_lint is not None:
            try:
                await self._after_lint()
            except Exception:
                # The index is a cache: a pass that fixed the wiki and could not
                # refresh the cache still fixed the wiki, and the curator has to
                # hear what it did.
                log.exception("curator: wiki reindex after lint failed")
        return result

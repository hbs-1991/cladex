"""The curator: one gated, reporting pass over the skill library and the wiki.

Moved from `learning/curator.py`. Every gate failure returns early with a reason
rather than raising, because the caller is an unattended loop and a reason is what
ends up in the report the operator reads.

Three properties are load-bearing and easy to lose in a refactor:

* **The first run always defers.** A fresh deployment buys the user one full
  interval to pin things before the curator touches anything.
* **Dry runs have their own throttle key.** Sharing `curator_last_run_at` would
  either regenerate an empty report every tick or let a preview push out the next
  real pass.
* **A review in flight blocks the whole pass**, not just consolidation: a curator
  write must not race a review-fork write to the same skill tree. Wave 3a reads
  that from a `ReviewGate` — a lease row — instead of from an in-process task
  handle, so it still holds when the review runs in a worker.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog

from claude_hermes.modules.learning.application.ports import (
    ActivityPort,
    MetaStore,
    ReportWriterPort,
    ReviewGate,
    SkillArchivePort,
    SkillUsagePort,
    SnapshotStorePort,
    WikiLintPort,
)
from claude_hermes.modules.learning.application.services.aging import (
    apply_aging,
    preview_aging,
)
from claude_hermes.modules.learning.domain.constants import (
    DRY_RUN_META_KEY,
    LAST_RUN_META_KEY,
    PAUSED_META_KEY,
    REPORT_PATH_META_KEY,
)
from claude_hermes.modules.learning.domain.usage import parse_aware

log = structlog.get_logger(__name__)


class CuratorService:
    """One curator pass over ONE scope's tree. `gate` is the `ReviewGate` the
    pass defers to while a review is in flight.

    `logs_dir` (Wave 4b, advisor S-1) replaces `home`: the only thing this class
    ever did with `home` was spell `home / "logs"`, and the scope hands the same
    directory to `SkillAdminService` and the snapshots already — one fact, one
    parameter. For the owner it is byte-identical (`CH_HOME/logs`); for anybody
    else it is `users/<id>/logs`, which is where their snapshots and reports
    belong and where their read guard can see them.
    """

    def __init__(
        self, *, cfg, logs_dir: Path, skills_dir: Path, wiki_root: Path,
        usage: SkillUsagePort, meta: MetaStore, activity: ActivityPort,
        snapshots: SnapshotStorePort, archive: SkillArchivePort,
        consolidation, reports: ReportWriterPort,
        wiki_lint: WikiLintPort | None = None,
        gate: ReviewGate | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._cfg = cfg
        self._logs_dir = logs_dir
        self._skills_dir = skills_dir
        self._wiki_root = wiki_root
        self._usage = usage
        self._meta = meta
        self._activity = activity
        self._snapshots = snapshots
        self._archive = archive
        self._consolidation = consolidation
        self._reports = reports
        self._wiki_lint = wiki_lint
        self._gate = gate
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def _idle_hours(self, now: datetime) -> float:
        ts = await self._activity.last_message_ts()
        if not ts:
            return float("inf")  # no messages ever recorded: nothing to be idle from
        return (now - parse_aware(ts)).total_seconds() / 3600

    async def run_pass(self) -> dict:
        cfg = self._cfg
        started = self._now()

        if not cfg.curator_enabled:
            return {"ran": False, "reason": "disabled"}
        if await self._meta.get(PAUSED_META_KEY) == "1":
            return {"ran": False, "reason": "paused"}
        if self._gate is not None and await self._gate.running():
            return {"ran": False, "reason": "review running"}

        last_run_raw = await self._meta.get(LAST_RUN_META_KEY)
        if last_run_raw is None:
            # first-run deferral: buy the user a full interval to pin things
            # before the curator ever touches the library.
            await self._meta.set(LAST_RUN_META_KEY, started.isoformat())
            return {"ran": False, "reason": "first-run deferral"}

        elapsed_hours = (started - parse_aware(last_run_raw)).total_seconds() / 3600
        if elapsed_hours < cfg.curator_interval_hours:
            return {"ran": False, "reason": "interval not elapsed",
                    "elapsed_hours": elapsed_hours}

        if cfg.curator_dry_run:
            # dry-run previews use their own throttle key so a real pass's cadence
            # (LAST_RUN_META_KEY, asserted untouched by dry-run tests) stays clean —
            # without this every 900s tick after the interval elapses once would
            # regenerate an empty report forever.
            last_dry_raw = await self._meta.get(DRY_RUN_META_KEY)
            if last_dry_raw is not None:
                dry_elapsed = (started - parse_aware(last_dry_raw)).total_seconds() / 3600
                if dry_elapsed < cfg.curator_interval_hours:
                    return {"ran": False, "reason": "dry-run interval not elapsed",
                            "elapsed_hours": dry_elapsed}

        idle_hours = await self._idle_hours(started)
        if idle_hours < cfg.curator_min_idle_hours:
            return {"ran": False, "reason": "not idle", "idle_hours": idle_hours}

        snapshot_id = None
        if not cfg.curator_dry_run:
            # a transient disk error must not silently disable the curator forever
            snap = self._snapshots.snapshot(self._skills_dir, self._logs_dir,
                                            reason="pre-curator-run", keep=cfg.backup_keep)
            if snap is not None:
                snapshot_id = snap.name
            else:
                log.warning("curator: pre-run snapshot failed (non-fatal)")

        transitions: list[str] = []
        consolidation = {"consolidated": [], "pruned": [], "summary": None}
        if cfg.curator_dry_run:
            transitions = preview_aging(self._skills_dir, self._usage, cfg, started)
            if cfg.curator_consolidate:
                consolidation = await self._consolidation.run(dry_run=True)
        else:
            transitions = apply_aging(self._skills_dir, self._usage, cfg, started,
                                      archive=self._archive)
            if cfg.curator_consolidate:
                consolidation = await self._consolidation.run()

        wiki_snapshot_id = None
        wiki_lint: str | None = None
        if cfg.wiki_lint_enabled and self._wiki_lint is not None:
            dry = cfg.curator_dry_run or cfg.wiki_lint_dry_run
            if not dry:
                snap = self._snapshots.snapshot(self._wiki_root, self._logs_dir,
                                                reason="pre-wiki-lint",
                                                keep=cfg.backup_keep, kind="wiki")
                if snap is not None:
                    wiki_snapshot_id = snap.name
                else:
                    log.warning("curator: pre-lint wiki snapshot failed (non-fatal)")
            wiki_lint = await self._wiki_lint.lint(dry_run=dry)

        result = {
            "ran": True,
            "started_at": started.isoformat(),
            "duration_seconds": (self._now() - started).total_seconds(),
            "gates": {"elapsed_hours": elapsed_hours, "idle_hours": idle_hours},
            "counts": {
                "transitions": len(transitions),
                "consolidated": len(consolidation["consolidated"]),
                "pruned": len(consolidation["pruned"]),
            },
            "transitions": transitions,
            "consolidations": consolidation["consolidated"],
            "prunings": consolidation["pruned"],
            "llm_summary": consolidation["summary"],
            "snapshot_id": snapshot_id,
            "dry_run": cfg.curator_dry_run,
            "wiki_lint": wiki_lint,
            "wiki_snapshot_id": wiki_snapshot_id,
        }

        try:
            report_path = self._reports.write(result)
            result["report_path"] = str(report_path)
            await self._meta.set(REPORT_PATH_META_KEY, str(report_path))
        except Exception:
            log.exception("curator: report write failed (non-fatal)")
            result["report_path"] = None

        if cfg.curator_dry_run:
            await self._meta.set(DRY_RUN_META_KEY, started.isoformat())
        else:
            # NOT in dry-run: a preview must not push out the next real pass.
            await self._meta.set(LAST_RUN_META_KEY, started.isoformat())

        return result

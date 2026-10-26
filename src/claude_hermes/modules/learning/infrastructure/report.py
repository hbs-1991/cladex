"""The curator's on-disk run report. Moved from `learning/curator.py`.

Two files per run under `<logs_dir>/curator/<ts>/`: `run.json` for a later read
by code, `REPORT.md` for a human who wants to know what the unattended pass did
to their library — and, crucially, how to undo it.

`logs_dir` rather than `home` since Wave 4b (advisor S-1): the pass runs per user
and a non-owner's reports belong under `users/<id>/logs/`, which is the one
`logs/` their read guard admits. For the owner the path is byte-identical.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def _ts_id(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H-%M-%SZ")  # colon-free: Windows-safe dir name


class ReportWriter:
    """`ReportWriterPort` over `<logs_dir>/curator/`."""

    def __init__(self, logs_dir: Path):
        self._logs_dir = logs_dir

    def write(self, result: dict) -> Path:
        return write_report(self._logs_dir, result)


def write_report(logs_dir: Path, result: dict) -> Path:
    report_dir = logs_dir / "curator" / _ts_id(datetime.fromisoformat(result["started_at"]))
    report_dir.mkdir(parents=True, exist_ok=True)
    run_json = report_dir / "run.json"
    run_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    def _bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "(none)"

    if result.get("snapshot_id"):
        recovery = (
            f"Snapshot `{result['snapshot_id']}` was taken before this run.\n"
            "To roll back: `claude_hermes.core.snapshots.rollback(skills_dir, "
            f"logs_dir, \"{result['snapshot_id']}\")`."
        )
    else:
        recovery = "No snapshot was taken this run (dry-run, or the snapshot failed)."

    report = "\n\n".join([
        f"# Curator run {result['started_at']}",
        f"Duration: {result['duration_seconds']:.1f}s  \nDry run: {result.get('dry_run', False)}",
        f"## Transitions\n{_bullets(result['transitions'])}",
        f"## Consolidations\n{_bullets(result['consolidations'])}",
        f"## Prunings\n{_bullets(result['prunings'])}",
        f"## LLM summary\n{result.get('llm_summary') or '(none)'}",
        f"## Wiki lint\n{result.get('wiki_lint') or '(not run)'}",
        f"## Recovery\n{recovery}",
    ]) + "\n"
    (report_dir / "REPORT.md").write_text(report, encoding="utf-8")
    return run_json

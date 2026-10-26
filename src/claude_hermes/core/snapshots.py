"""Tar snapshots of a directory tree, with a manifest and a prune window.

The skills library, the wiki and the owner's browser edits each get their own
store here. `learning` takes them before a curator pass, `extensions` before an
approval, the web skill editor before a save — three modules, one mechanism, so
it lives in core rather than inside `learning` where it started.

Moved verbatim from `learning/backup.py` in Wave 2.
"""
from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

_MANIFEST_NAME = "manifest.json"

# Each kind is its own store with its own prune window. skill_edits snapshots the same
# tree as skills, but from a different hand: the owner saving a SKILL.md in the browser.
# Sharing kind="skills" would let a handful of browser saves prune away the
# 'pre-curator-run' snapshot the curator's rollback instructions point at -- the one
# snapshot nobody can recreate.
_KINDS = {
    "skills": ("skills_backups", "skills.tar.gz"),
    "wiki": ("wiki_backups", "wiki.tar.gz"),
    "skill_edits": ("skill_edit_backups", "skills.tar.gz"),
}


def _backups_root(logs_dir: Path, kind: str = "skills") -> Path:
    return logs_dir / _KINDS[kind][0]


def _archive_name(kind: str) -> str:
    return _KINDS[kind][1]


def _make_id(backups_root: Path) -> str:
    base = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    candidate, n = base, 2
    while (backups_root / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _read_manifests(backups_root: Path) -> list[dict]:
    manifests = []
    if not backups_root.is_dir():
        return manifests
    for d in backups_root.iterdir():
        if not d.is_dir():
            continue
        mpath = d / _MANIFEST_NAME
        try:
            manifests.append(json.loads(mpath.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    manifests.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return manifests


def _prune(backups_root: Path, keep: int, protect: set[str]) -> None:
    manifests = _read_manifests(backups_root)
    keep_ids = {m["id"] for m in manifests[:keep]} | protect
    for m in manifests:
        if m["id"] not in keep_ids:
            shutil.rmtree(backups_root / m["id"], ignore_errors=True)


def snapshot(
    source_dir: Path,
    logs_dir: Path,
    reason: str,
    keep: int = 5,
    _protect: set[str] | None = None,
    kind: str = "skills",
) -> Path | None:
    """Tar source_dir into logs_dir/<kind>_backups/<id>/, prune to newest `keep`.

    Best-effort: returns None (never raises) on any filesystem failure.
    """
    try:
        if not source_dir.is_dir():
            return None
        backups_root = _backups_root(logs_dir, kind)
        backups_root.mkdir(parents=True, exist_ok=True)
        snap_id = _make_id(backups_root)
        snap_dir = backups_root / snap_id
        snap_dir.mkdir(parents=True)
        tar_path = snap_dir / _archive_name(kind)

        files = 0
        with tarfile.open(tar_path, "w:gz") as tf:
            for path in sorted(source_dir.rglob("*")):
                if path.is_file():
                    arcname = path.relative_to(source_dir).as_posix()
                    tf.add(path, arcname=arcname)
                    files += 1

        manifest = {
            "id": snap_id,
            "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "archive": str(tar_path),
            "archive_bytes": tar_path.stat().st_size,
            "files": files,
        }
        (snap_dir / _MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        _prune(backups_root, keep, _protect or set())
        return snap_dir
    except OSError:
        return None


def list_snapshots(logs_dir: Path, kind: str = "skills") -> list[dict]:
    return _read_manifests(_backups_root(logs_dir, kind))


def _check_safe_member(name: str) -> None:
    if PurePosixPath(name).is_absolute():
        raise ValueError(f"unsafe tar member (absolute path): {name}")
    if ".." in PurePosixPath(name).parts:
        raise ValueError(f"unsafe tar member (parent traversal): {name}")


def rollback(
    target_dir: Path, logs_dir: Path, backup_id: str | None = None, keep: int = 5,
    kind: str = "skills",
) -> tuple[bool, str]:
    """Restore target_dir from a snapshot, staging the current tree aside first."""
    snaps = list_snapshots(logs_dir, kind)
    if not snaps:
        return False, "no snapshots available"

    if backup_id is not None:
        target = next((m for m in snaps if m["id"] == backup_id), None)
        if target is None:
            return False, f"backup not found: {backup_id}"
    else:
        target = snaps[0]

    tar_path = Path(target["archive"])
    if not tar_path.is_file():
        return False, f"archive missing: {tar_path}"

    # protect the snapshot we're about to extract from: at the steady keep-limit
    # this pre-rollback snapshot would otherwise push it out of the prune window
    snapshot(target_dir, logs_dir, f"pre-rollback:{target['id']}", keep=keep,
             _protect={target["id"]}, kind=kind)

    staging = Path(tempfile.mkdtemp(dir=str(target_dir.parent), prefix=".rollback-"))
    staged_tree = staging / target_dir.name
    if target_dir.is_dir():
        shutil.move(str(target_dir), str(staged_tree))
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(tar_path, "r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                _check_safe_member(member.name)
            tf.extractall(path=target_dir, filter="data")
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        if staged_tree.is_dir():
            shutil.move(str(staged_tree), str(target_dir))
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"rollback failed: {exc}"

    shutil.rmtree(staging, ignore_errors=True)
    return True, f"rolled back to {target['id']}"

"""One implementation of the skills library: list, read, write, create, archive.

`skill_admin` (chat) and the skills router (browser) both call this, so the two
channels cannot drift on what "stale" or "bundled" means — the
`extensions/manage.py` precedent, for the same reason.

The listing is the union of three sources that can disagree: the live
directories, `.archive/`, and `.usage.json`. A skill missing from any one of them
is still real — a directory the sidecar never saw is a skill the agent wrote
before telemetry existed, a sidecar key with no directory is one somebody moved
by hand — so the union is what gets listed and the directory, not the sidecar,
decides the state.

Mutations here do the file work and the validation only. Snapshots, `bump_patch`
and the session reset belong to the caller: chat and the browser want the same
bytes on disk but not the same bookkeeping.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from claude_hermes.modules.learning.application.ports import (
    SkillArchivePort,
    SkillRepositoryPort,
    SkillUsagePort,
)
from claude_hermes.modules.learning.domain.constants import LIVE_STATES, NAME_RE, SKILL_FILE
from claude_hermes.modules.learning.domain.exceptions import SkillError, StaleEdit
from claude_hermes.modules.learning.domain.models import (
    SkillInfo,
    etag_of,
    validate_content,
)


class SkillLibraryService:
    """`SkillLibraryPort` over one skills directory."""

    def __init__(self, repo: SkillRepositoryPort, usage: SkillUsagePort,
                 archive: SkillArchivePort):
        self._repo = repo
        self._usage = usage
        self._archive = archive

    @property
    def skills_dir(self) -> Path:
        return self._repo.skills_dir

    # ---------------------------------------------------------------- listing
    def list_infos(self) -> list[SkillInfo]:
        entries = self._usage.all()
        live = self._usage.list_skill_dirs()
        archived = set(self._archive.list_archived())
        infos = []
        for name in sorted(set(entries) | live | archived):
            entry = entries.get(name) or {}
            directory, is_archived = self._repo.placement(name, live)
            raw_state = entry.get("state")
            state = "archived" if is_archived else (
                raw_state if raw_state in LIVE_STATES else "active")
            infos.append(self._repo.info(name, directory, entry, state))
        # Active first, then by name: the archived tail is history, and a list that
        # buries a live skill between two archived ones is the one nobody scrolls.
        return sorted(infos, key=lambda i: (i.state == "archived", i.name))

    def list_skills(self) -> list[dict]:
        return [info.to_wire() for info in self.list_infos()]

    def read_skill(self, name: str) -> dict | None:
        """`SkillDetail`: the wire row plus content, etag and the other files."""
        found = self._repo.locate(name)
        if found is None:
            return None
        return self._repo.detail(name, found[0], found[1],
                                 entry=self._usage.get(name) or {})

    # ----------------------------------------------------------------- writes
    def write_skill(self, name: str, content: str, etag: str,
                    before_write: Callable[[], None] | None = None) -> str:
        """Rewrite SKILL.md iff `etag` still matches what is on disk; returns the new one.

        The precondition is the whole point: the background review pass writes these
        same files after most turns, so an unguarded Save would silently discard
        whatever the agent had just learned. It does not serialise the two — a write
        landing mid-pass still wins the last write — it makes the loss visible.

        `before_write` runs in the one window where a snapshot is both earned and still
        useful: after the content and the etag are known good, before the replace. The
        caller cannot open that window itself — snapshotting before the call taxes every
        refused save (five rejected retries would rotate the keep-N store past the last
        state anyone wants back), and snapshotting after has already lost the text.
        """
        validate_content(content)
        found = self._repo.locate(name)
        if found is None:
            raise SkillError("not_found", f"no skill named '{name}'")
        path = found[0] / SKILL_FILE
        current = self._repo.read_bytes(path)
        current_etag = etag_of(current)
        if current_etag != etag:
            raise StaleEdit(current.decode("utf-8", errors="replace"), current_etag)
        if before_write is not None:
            before_write()
        data = content.encode("utf-8")
        self._repo.write_atomic(path, data)
        return etag_of(data)

    def create_skill(self, name: str, content: str, now_iso: str) -> dict:
        """Create `<name>/SKILL.md` and record the provenance. Returns the detail dict."""
        if not NAME_RE.match(name or ""):
            raise SkillError("bad_name",
                             "a skill name is lowercase letters, digits, '-' and '_', "
                             "starting with a letter or digit")
        directory = self._repo.resolve(name)
        # The archive counts as taken: restoring would land on top of the new skill,
        # and restore_skill refuses rather than merging.
        if self._repo.exists(directory) or self._repo.exists(
                self._repo.resolve(name, archived=True)):
            raise SkillError("name_taken", f"'{name}' already exists")
        validate_content(content)
        self._repo.create_dir(directory)
        self._repo.write_atomic(directory / SKILL_FILE, content.encode("utf-8"))
        self._usage.mark_created(name, "user", now_iso)
        return self._repo.detail(name, directory, False,
                                 entry=self._usage.get(name) or {})

    # -------------------------------------------------------------- lifecycle
    def archive_skill(self, name: str, now_iso: str) -> Path:
        return self._archive.archive_skill(name, now_iso)

    def restore_skill(self, name: str, now_iso: str) -> bool:
        return self._archive.restore_skill(name, now_iso)

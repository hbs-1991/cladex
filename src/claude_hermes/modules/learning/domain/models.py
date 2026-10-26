"""The skills library's wire row and the pure functions that build it.

Parsing, hashing and validation only — no filesystem, no sidecar. `yaml` is the
one third-party import a domain module is allowed here: frontmatter IS the
format, and re-implementing it would be the real impurity.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import yaml

from claude_hermes.modules.learning.domain.constants import MAX_CONTENT_BYTES
from claude_hermes.modules.learning.domain.exceptions import SkillError
from claude_hermes.modules.learning.domain.usage import last_activity


@dataclass(frozen=True)
class SkillInfo:
    """The `SkillWire` shape, one per skill. `to_wire` is the only serialiser —
    the router returns it verbatim."""
    name: str
    title: str
    description: str
    broken: bool
    source: str          # bundled | agent | user
    state: str           # active | stale | archived
    pinned: bool
    use_count: int
    patch_count: int
    created_at: str | None
    last_used: str | None
    last_patched: str | None
    files: int
    bytes: int

    def to_wire(self) -> dict:
        return asdict(self)


def etag_of(raw: bytes) -> str:
    """Content-addressed edit token. sha256 of the exact bytes on disk, so a
    write that changed nothing still matches and a whitespace-only edit does not."""
    return hashlib.sha256(raw).hexdigest()


def parse_front_matter(text: str) -> tuple[dict, bool]:
    """The YAML block between the leading `---` fences, and whether it parsed.

    The flag is the point: a skill whose frontmatter is broken must still list —
    with `broken: true` — because hiding it is how one stays broken for weeks.
    """
    if not text.startswith("---"):
        return {}, False
    end = text.find("\n---", 3)
    if end < 0:
        return {}, False
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}, False
    return (data, True) if isinstance(data, dict) else ({}, False)


def last_activity_iso(row: Mapping) -> str:
    """`usage.last_activity` over a wire row — it carries the same three stamps.

    A row the sidecar never saw has none of them; say so rather than inventing a
    date from a file mtime, which records the last `git clone`, not the last use.
    """
    try:
        return last_activity(row).isoformat()
    except (ValueError, TypeError):
        return "—"


def validate_content(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise SkillError("too_large",
                         f"SKILL.md must stay under {MAX_CONTENT_BYTES // 1024} KiB")
    meta, parsed = parse_front_matter(content)
    desc = meta.get("description")
    if not parsed or not isinstance(desc, str) or not desc.strip():
        raise SkillError(
            "bad_frontmatter",
            "SKILL.md needs a --- frontmatter block with a non-empty description; "
            "without it the skill never routes.")

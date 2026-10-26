"""Parsing one wiki page: frontmatter, wikilinks, the node the graph draws.

Pure functions over text — no filesystem walk, no HTTP, no service. Moved from
`web/wiki.py` in Wave 2; `read_page` still takes the `Path` it reads
because a page is a file and the node carries the file's own stem as its fallback
title.

`index.md` and `log.md` are bookkeeping and never become nodes. Any *other*
root-level `.md` (a legacy page from before the namespaces existed) is kept, with
`ns == ""` — losing a page because it sits one directory too high is the failure
mode this parser exists to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# The bookkeeping files, by their path relative to the wiki root.
BOOKKEEPING = frozenset({"index.md", "log.md"})
# `[[ns/page]]`, `[[ns/page|Alias]]`, `[[ns/page#anchor]]`. Nested brackets are not a
# wikilink, so the character class refuses them rather than swallowing half a line.
WIKILINK = re.compile(r"\[\[([^\[\]\n]+)\]\]")


def link_target(raw: str) -> str:
    """`ns/page|Alias` -> `ns/page`. The alias is display text and the anchor is a
    heading inside the page; neither changes which node the edge lands on."""
    target = raw.split("|", 1)[0].split("#", 1)[0].strip().replace("\\", "/").strip("/")
    return target[:-3] if target.lower().endswith(".md") else target


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Frontmatter block and body, tolerantly.

    A page whose frontmatter does not parse still has to appear in the graph — the
    agent writing malformed YAML is the moment you most want to see the page — so
    every failure here is an empty dict, never an exception.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            try:
                meta = yaml.safe_load("\n".join(lines[1:i]))
            except yaml.YAMLError:
                meta = None
            return (meta if isinstance(meta, dict) else {}), "\n".join(lines[i + 1:])
    return {}, text                      # an unterminated block is not frontmatter


def _text(value: object) -> str:
    """A scalar as a string. `updated: 2026-07-28` comes back from yaml as a
    `date`, so `str()` — not a cast that would raise on it."""
    return "" if value is None or isinstance(value, (list, dict)) else str(value).strip()


def _tags(value: object) -> list[str]:
    if isinstance(value, list):
        return [_text(v) for v in value if _text(v)]
    return [p.strip() for p in _text(value).split(",") if p.strip()]


def read_page(path: str, file: Path, index_text: str) -> dict:
    """One node. `title` falls back to the filename because a page with no title is
    still a page, and the graph has to label it with something the owner can read."""
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        text = ""
    meta, body = split_frontmatter(text)
    links: list[str] = []
    for match in WIKILINK.findall(body):
        target = link_target(match)
        if target and target not in links:
            links.append(target)
    return {
        "path": path,
        # The hub is the FIRST segment, so `topics/ai/foo` hangs off `topics` — the
        # graph is two rings deep on purpose and a third would not fit a phone.
        "ns": path.split("/")[0] if "/" in path else "",
        "title": _text(meta.get("title")) or file.stem,
        "summary": _text(meta.get("summary")),
        "tags": _tags(meta.get("tags")),
        "updated": _text(meta.get("updated")),
        "links": links,
        "in_index": f"{path}.md" in index_text,
    }

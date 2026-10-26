"""The pure half of the plugin surface: which bundled servers would RUN here, and the
text the owner's approval token is computed over."""
from __future__ import annotations

import pytest

from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.domain.plugin_surface import (_classify,
                                                                   render_surface,
                                                                   surface_token)

PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}


@pytest.mark.parametrize("spec", [
    {"command": "bun", "args": ["run", "x"]},
    {"args": ["-y", "whatever"]},                 # args without a command is still an argv
    {"type": "http", "command": "npx"},           # a declared transport does not undo argv
    "not-a-mapping",                              # unreadable: the honest answer is yes
])
def test_an_entry_that_could_start_a_process_is_local(spec):
    """`local` is the whole gate on the install path (400 local_server). An argv arriving
    this way never passed the catalog's npx/uvx/python allowlist, which exists for it."""
    assert _classify("s", spec).local is True


@pytest.mark.parametrize("spec", [
    {"type": "http", "url": "https://mcp.example.com/mcp"},
    {"url": "http://localhost:3000"},
    {"type": "http", "url": ""},                  # how the knowledge-work marketplace
    {"type": "sse", "url": "${SOME_URL}"},        # ships gmail / google calendar
])
def test_an_entry_with_no_argv_is_remote(spec):
    """Refusing these refused three real plugins that spawn nothing: the CLI has a URL
    to dial or it has nothing, and either way no process starts in our container."""
    assert _classify("s", spec).local is False


def test_a_local_entry_reports_the_argv_it_would_run():
    s = _classify("s", {"command": "bun", "args": ["run", "evil.ts"]})
    assert s.target == "bun run evil.ts" and s.transport == "stdio"


def test_the_surface_names_every_component_and_the_token_follows_it():
    m = parse(PLUGIN, "plugin")
    parts = {"commands": ["a"], "skills": ["b"], "agents": [], "hooks": True,
             "author": "Anthropic", "plugin_version": "2.1.0"}
    text = render_surface(m, parts, [])
    assert "hooks:       YES" in text and "author:      Anthropic" in text
    assert "version:     2.1.0" in text        # the clone's version, not the manifest's
    assert surface_token(text) == surface_token(render_surface(m, parts, []))
    assert surface_token(text) != surface_token(render_surface(m, {**parts, "hooks": False}, []))


def test_the_surface_strips_control_characters_from_author_written_text():
    """Everything on this screen was written by whoever owns the repository, and an ANSI
    escape can redraw the line above it."""
    m = parse(PLUGIN, "plugin")
    text = render_surface(m, {"author": "Safe\x1b[2Kcommands:    (none)"}, [])
    assert "\x1b" not in text

from __future__ import annotations

import json

from claude_hermes.modules.extensions.domain.exceptions import ComposeError
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.domain.paths import plugin_dir
from claude_hermes.modules.extensions.infrastructure.plugin_reader import (
    components,
    is_present,
)

PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}


def _manifest(root, **over):
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "thing", "version": "2.1.0",
                    "author": {"name": "Anthropic"}, **over}),
        encoding="utf-8",
    )


def _write(path, body="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_a_full_plugin_reports_all_five_surfaces(tmp_path):
    root = tmp_path / "clone"
    _manifest(root)
    _write(root / "commands" / "ship.md")
    _write(root / "commands" / "audit.md")
    _write(root / "skills" / "brand-voice" / "SKILL.md")
    _write(root / "agents" / "reviewer.md")
    _write(root / "hooks" / "hooks.json", "{}")
    _write(root / ".mcp.json", "{}")

    got = components(root)

    assert got == {
        "commands": ["audit", "ship"],            # sorted, not directory order
        "skills": ["brand-voice"],
        "agents": ["reviewer"],
        "hooks": True,
        "mcp": True,
        "author": "Anthropic",
        "plugin_version": "2.1.0",
    }


def test_a_bare_plugin_reports_empty_lists_and_false_flags(tmp_path):
    root = tmp_path / "clone"
    _manifest(root, author="Someone", version="0.1")

    got = components(root)

    assert got["commands"] == [] and got["skills"] == [] and got["agents"] == []
    assert got["hooks"] is False and got["mcp"] is False
    assert got["author"] == "Someone"             # a plain-string author is legal too
    assert got["plugin_version"] == "0.1"


def test_a_missing_plugin_json_reads_as_none(tmp_path):
    root = tmp_path / "clone"
    (root / "commands").mkdir(parents=True)
    assert components(root) is None
    assert components(tmp_path / "not-there") is None


def test_a_malformed_plugin_json_still_lists_the_files(tmp_path):
    """A page load must not die on a plugin whose manifest is junk -- the files on disk
    are the thing worth showing, and the author line is decoration."""
    root = tmp_path / "clone"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")
    _write(root / "commands" / "ship.md")

    got = components(root)

    assert got["commands"] == ["ship"]
    assert got["author"] == "" and got["plugin_version"] == ""


def test_odd_plugin_json_shapes_do_not_raise(tmp_path):
    root = tmp_path / "clone"
    for payload in ('[1, 2, 3]', '{"author": 42, "version": [1]}',
                    '{"author": {"nick": "x"}, "version": null}', '"a string"'):
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text(payload, encoding="utf-8")
        got = components(root)
        assert got is not None
        assert got["author"] == "" and got["plugin_version"] == ""


def test_a_skill_directory_without_a_skill_md_is_not_a_skill(tmp_path):
    root = tmp_path / "clone"
    _manifest(root)
    _write(root / "skills" / "real" / "SKILL.md")
    (root / "skills" / "leftovers").mkdir(parents=True)
    _write(root / "skills" / "loose.md")          # a file, not a skill directory
    _write(root / "commands" / "nested" / "deep.md")   # subdirectories are not commands

    got = components(root)

    assert got["skills"] == ["real"]
    assert got["commands"] == []


def test_each_list_is_capped(tmp_path):
    root = tmp_path / "clone"
    _manifest(root)
    for i in range(120):
        _write(root / "commands" / f"c{i:03d}.md")

    got = components(root)

    assert len(got["commands"]) == 100
    assert got["commands"][0] == "c000"           # the cap takes the first 100 in order


def test_is_present_follows_the_manifest_to_the_clone(tmp_path):
    m = parse(PLUGIN, "plugin")
    assert is_present(tmp_path, m) is False       # nothing cloned yet

    _manifest(plugin_dir(tmp_path, "thing"))
    assert is_present(tmp_path, m) is True


def test_is_present_honours_subdir(tmp_path):
    m = parse({**PLUGIN, "subdir": "packages/thing"}, "plugin")
    clone = plugin_dir(tmp_path, "thing")
    _manifest(clone)                              # the repo root, not the plugin
    assert is_present(tmp_path, m) is False

    _manifest(clone / "packages" / "thing")
    assert is_present(tmp_path, m) is True


def test_is_present_is_false_when_the_root_cannot_be_resolved(tmp_path, monkeypatch):
    m = parse(PLUGIN, "plugin")
    _manifest(plugin_dir(tmp_path, "thing"))

    def boom(home, manifest):
        raise ComposeError("subdir resolves outside the plugin directory")

    monkeypatch.setattr(
        "claude_hermes.modules.extensions.infrastructure.plugin_reader.plugin_root", boom)
    assert is_present(tmp_path, m) is False

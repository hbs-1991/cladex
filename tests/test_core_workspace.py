"""`Workspace` is the one description of the CH_HOME layout. Every path it names was
spelled out by hand somewhere before; these tests pin each to the directory the code
that owns it actually uses, so a rename cannot pass silently.

Wave 4a adds the second half: a workspace now belongs to ONE user, and the owner's
must still be byte-identical to what it was — `/data/agent_home`, `/data/.claude-cli`
and `/data/logs` moving would strand a live volume and break every `resume`.
"""
import dataclasses
from pathlib import Path

import pytest

from claude_hermes.core.workspace import Workspace, WorkspaceResolver, get_home


def test_workspace_names_every_path_under_home(tmp_path):
    ws = Workspace.owner(tmp_path)
    assert ws.home == tmp_path and ws.root == tmp_path
    assert ws.agent_home == tmp_path / "agent_home"
    assert ws.claude_dir == tmp_path / "agent_home" / ".claude"
    assert ws.skills_dir == tmp_path / "agent_home" / ".claude" / "skills"
    assert ws.agents_dir == tmp_path / "agent_home" / ".claude" / "agents"
    assert ws.logs_dir == tmp_path / "logs"
    assert ws.wiki_root == tmp_path / "agent_home" / "wiki"
    assert ws.user_catalog == tmp_path / "user-catalog"
    assert ws.drafts_dir == tmp_path / "catalog-drafts"
    assert ws.pending_approvals_dir == tmp_path / "pending-approvals"
    assert ws.secrets_path == tmp_path / "secrets.env"
    assert ws.inbox_dir == tmp_path / "agent_home" / "inbox"
    assert ws.outbox_dir == tmp_path / "agent_home" / "outbox"
    # New in 4a, and for the owner they are exactly where the CLI already looked.
    assert ws.tmp_dir == tmp_path / "tmp"
    assert ws.cli_config_dir == tmp_path / ".claude-cli"
    assert ws.plugins_dir == tmp_path / "plugins"


def test_a_user_root_moves_the_per_user_paths_and_nothing_else(tmp_path):
    """The split that makes two users possible: everything a turn writes hangs off
    `root`, everything the DEPLOYMENT owns hangs off `home` (D8)."""
    ws = Workspace.for_user(tmp_path, "users/42")
    root = tmp_path / "users" / "42"
    assert ws.home == tmp_path and ws.root == root
    for attr in ("agent_home", "claude_dir", "skills_dir", "agents_dir", "wiki_root",
                 "inbox_dir", "outbox_dir", "tmp_dir", "cli_config_dir", "logs_dir"):
        assert root in getattr(ws, attr).parents, attr
    # Extensions, their plugin clones, their secrets and the catalog are the
    # owner's decision and the owner's credentials — one copy, under home.
    owner = Workspace.owner(tmp_path)
    for attr in ("user_catalog", "plugins_dir", "secrets_path", "drafts_dir",
                 "pending_approvals_dir"):
        assert getattr(ws, attr) == getattr(owner, attr), attr


def test_the_owner_workspace_dir_is_the_empty_string(tmp_path):
    """That is how the owner's row is written, precisely so this needs no branch
    anywhere else — and so a live volume needs no file migration."""
    assert Workspace.for_user(tmp_path, "") == Workspace.owner(tmp_path)


def test_constructing_a_workspace_touches_no_filesystem(tmp_path):
    """Pure resolution, like home_dir(): a module that only needs to know WHERE
    state lives must not trigger first-boot seeding as a side effect."""
    home = tmp_path / "nothing-here"
    ws = Workspace.owner(home)
    assert ws.skills_dir == home / "agent_home" / ".claude" / "skills"
    assert not home.exists()


def test_resolve_is_get_home_in_this_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    ws = Workspace.resolve()
    assert ws.home == get_home()
    # resolve() seeds, so the tree the runtime depends on is there
    assert ws.skills_dir.is_dir()
    assert ws.agents_dir.is_dir()
    assert ws.logs_dir.is_dir()
    assert ws.pending_approvals_dir.is_dir()
    assert (ws.user_catalog / "mcp").is_dir()
    # New in 4a: the CLI's own config dir and the per-lane TMPDIR.
    assert ws.cli_config_dir.is_dir() and ws.tmp_dir.is_dir()


def test_ensure_seeds_a_user_tree_and_is_idempotent(tmp_path):
    ws = Workspace.for_user(tmp_path, "users/42").ensure()
    assert ws.skills_dir.is_dir() and ws.agents_dir.is_dir()
    assert ws.logs_dir.is_dir() and ws.tmp_dir.is_dir()
    assert ws.cli_config_dir.is_dir() and ws.wiki_root.is_dir()
    # A second user's first message must not be what brings the DEPLOYMENT's
    # directories into existence.
    assert not (tmp_path / "pending-approvals").exists()
    marker = ws.skills_dir / "mine" / "SKILL.md"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("mine", encoding="utf-8")
    ws.ensure()
    assert marker.read_text(encoding="utf-8") == "mine"


def test_ensure_moves_legacy_plugin_clones_out_of_agent_home(tmp_path):
    """D8: a plugin is deploy-wide, so its clone may not live inside the owner's
    `agent_home` — a non-owner turn could then only load it if its read guard
    admitted the owner's whole tree."""
    legacy = tmp_path / "agent_home" / "plugins" / "fatsecret"
    legacy.mkdir(parents=True)
    (legacy / "plugin.json").write_text("{}", encoding="utf-8")
    ws = Workspace.owner(tmp_path).ensure()
    assert not (tmp_path / "agent_home" / "plugins").exists()
    assert (ws.plugins_dir / "fatsecret" / "plugin.json").read_text(
        encoding="utf-8") == "{}"
    # Idempotent, and a second boot must not move anything back or raise.
    ws.ensure()
    assert (ws.plugins_dir / "fatsecret").is_dir()


def test_the_plugins_move_leaves_an_existing_destination_alone(tmp_path):
    """The losing side of the three-process race: agent, worker and scheduler all
    run `ensure()` for the owner at boot."""
    (tmp_path / "agent_home" / "plugins" / "old").mkdir(parents=True)
    (tmp_path / "plugins" / "new").mkdir(parents=True)
    ws = Workspace.owner(tmp_path).ensure()
    assert (ws.plugins_dir / "new").is_dir()
    assert not (ws.plugins_dir / "old").exists()
    assert (tmp_path / "agent_home" / "plugins" / "old").is_dir()


def test_workspace_is_frozen_and_comparable(tmp_path):
    ws = Workspace.owner(tmp_path)
    assert ws == Workspace.owner(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ws.home = Path("/elsewhere")


# --- the resolver -----------------------------------------------------------


def _resolver(home, dirs: dict, owner: int = 1) -> WorkspaceResolver:
    async def workspace_dir_of(user_id: int) -> str:
        if user_id not in dirs:
            raise KeyError(user_id)          # what UnknownUser is, for this fake
        return dirs[user_id]

    async def deploy_owner() -> int:
        return owner

    return WorkspaceResolver(home, workspace_dir_of=workspace_dir_of,
                             deploy_owner=deploy_owner)


async def test_for_user_seeds_once_and_caches(tmp_path):
    seen = []

    async def workspace_dir_of(user_id: int) -> str:
        seen.append(user_id)
        return "users/42"

    async def deploy_owner() -> int:
        return 1

    resolver = WorkspaceResolver(tmp_path, workspace_dir_of=workspace_dir_of,
                                 deploy_owner=deploy_owner)
    first = await resolver.for_user(42)
    second = await resolver.for_user(42)
    assert first is second and seen == [42]
    assert first.root == tmp_path / "users" / "42" and first.skills_dir.is_dir()


async def test_for_lane_applies_the_one_rule_and_reads_no_table(tmp_path):
    """`lane_owner` in `core/session_keys.py`, the same rule `sessions.owner_id` is
    written from and the same rule 0005 spelled in SQL — so a brand-new lane with no
    row resolves exactly like an old one."""
    resolver = _resolver(tmp_path, {1: "", 42: "users/42"}, owner=1)
    home_root = tmp_path
    # A web lane belongs to the id IN THE KEY, not to whoever is asking.
    assert (await resolver.for_lane("web:42:abc", "web", 1)).root == home_root / "users" / "42"
    assert (await resolver.for_lane("tg:private:42", "telegram", 42)).root \
        == home_root / "users" / "42"
    # A group lane and a cron lane are the deploy owner's.
    assert (await resolver.for_lane("tg:supergroup:-100:42", "telegram", 42)).root \
        == home_root
    assert (await resolver.for_lane("cron:7", "cron", 0)).root == home_root
    # …unless the scheduler passed the job's owner through.
    assert (await resolver.for_lane("cron:7", "cron", 42)).root \
        == home_root / "users" / "42"


async def test_an_unknown_user_raises_rather_than_getting_the_owners_tree(tmp_path):
    resolver = _resolver(tmp_path, {1: ""}, owner=1)
    with pytest.raises(KeyError):
        await resolver.for_user(99)


async def test_mirror_root_is_the_rows_owner_agent_home(tmp_path):
    resolver = _resolver(tmp_path, {1: "", 42: "users/42"}, owner=1)
    assert await resolver.mirror_root(42) == tmp_path / "users" / "42" / "agent_home"
    assert await resolver.mirror_root(1) == tmp_path / "agent_home"


async def test_an_unclaimed_deploy_is_not_cached_as_owner_zero(tmp_path):
    """0 is "nobody has claimed this yet", and caching it would leave the process
    resolving every group and cron lane to nobody for the rest of its life."""
    owners = [0, 7]

    async def deploy_owner() -> int:
        return owners.pop(0)

    async def workspace_dir_of(user_id: int) -> str:
        return "" if user_id == 7 else f"users/{user_id}"

    resolver = WorkspaceResolver(tmp_path, workspace_dir_of=workspace_dir_of,
                                 deploy_owner=deploy_owner)
    assert await resolver.owner_id() == 0
    assert await resolver.owner_id() == 7
    assert await resolver.owner_id() == 7          # now cached


# --- the seed manifest (Wave 4b, D8) -------------------------------------------


import json  # noqa: E402
import logging  # noqa: E402
import shutil  # noqa: E402

from claude_hermes.core import workspace as workspace_mod  # noqa: E402
from claude_hermes.core.workspace import (  # noqa: E402
    SeedManifest, seed_agents, seed_settings, seed_skills,
)


def _bundle(tmp_path, monkeypatch, *, skills=None, agents=None, settings='{"a": 1}'):
    """Point the module at a bundle of our own, so a test can CHANGE what ships
    between two boots — the situation the manifest exists for."""
    assets = tmp_path / "assets"
    if assets.exists():
        shutil.rmtree(assets)
    for name, files in (skills or {}).items():
        for rel, body in files.items():
            p = assets / "skills" / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
    (assets / "skills").mkdir(parents=True, exist_ok=True)
    (assets / "agents").mkdir(parents=True, exist_ok=True)
    for name, body in (agents or {}).items():
        (assets / "agents" / f"{name}.md").write_text(body, encoding="utf-8")
    (assets / "bundle").mkdir(parents=True, exist_ok=True)
    (assets / "bundle" / "settings.json").write_text(settings, encoding="utf-8")
    (assets / "bundle" / "persona.md").write_text("persona", encoding="utf-8")
    (assets / "bundle" / "CLAUDE.md").write_text("@.claude/persona.md\n", encoding="utf-8")
    monkeypatch.setattr(workspace_mod, "_BUNDLED_SKILLS_DIR", assets / "skills")
    monkeypatch.setattr(workspace_mod, "_BUNDLED_AGENTS_DIR", assets / "agents")
    monkeypatch.setattr(workspace_mod, "_BUNDLE_DIR", assets / "bundle")
    return assets


def _manifest_on_disk(ws: Workspace) -> dict:
    return json.loads((ws.claude_dir / ".seeded.json").read_text(encoding="utf-8"))


def test_first_boot_records_every_seed_relative_to_agent_home(tmp_path, monkeypatch):
    """Row 1 of the table: absent, no record → copy and record. The keys are the
    paths a later boot will look up, so they must be relative to `agent_home`
    (a tree that is moved or restored elsewhere keeps its manifest)."""
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v1"}}, agents={"r": "ag"})
    ws = Workspace.for_user(tmp_path / "home", "users/7").ensure()
    assert (ws.skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "v1"
    recorded = _manifest_on_disk(ws)
    assert set(recorded) == {".claude/skills/foo/SKILL.md", ".claude/agents/r.md",
                             ".claude/settings.json"}
    assert all(len(v) == 64 for v in recorded.values())


def test_a_deleted_seed_stays_deleted(tmp_path, monkeypatch):
    """Row 2: absent, record present → leave absent. Before 4b every boot put the
    file back, so a user could not remove a bundled skill they never wanted."""
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7").ensure()
    shutil.rmtree(ws.skills_dir / "foo")
    ws.ensure()
    assert not (ws.skills_dir / "foo").exists()


def test_an_archived_seed_is_not_resurrected_on_the_next_boot(tmp_path, monkeypatch):
    """Row 3, and the live defect: the curator archived a bundled skill, the next
    boot copied it straight back in, the curator archived it again a week later.
    A name under `.archive/` is skipped whatever the manifest says — including a
    tree that has no manifest at all (the volume the defect was reproduced on)."""
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    ws = Workspace.resolve()
    seeded = ws.skills_dir / "skill-authoring"
    assert seeded.is_dir()
    (ws.skills_dir / ".archive").mkdir()
    seeded.rename(ws.skills_dir / ".archive" / "skill-authoring")
    ws.ensure()
    assert not seeded.exists()
    assert (ws.skills_dir / ".archive" / "skill-authoring" / "SKILL.md").is_file()
    # …and without a manifest, the legacy path, the same.
    (ws.claude_dir / ".seeded.json").unlink()
    seed_skills(ws.skills_dir)
    assert not seeded.exists()


def test_a_legacy_tree_records_only_its_pristine_seeds(tmp_path, monkeypatch):
    """Row 4: present, no record. A volume from before the manifest existed holds
    seeds we cannot date; the ones that still byte-match the bundle are recorded
    (so they refresh from the NEXT boot on), the rest are the user's and are
    neither recorded nor touched."""
    _bundle(tmp_path, monkeypatch, skills={"same": {"SKILL.md": "v1"},
                                           "mine": {"SKILL.md": "v1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7")
    for name, body in (("same", "v1"), ("mine", "edited")):
        (ws.skills_dir / name).mkdir(parents=True)
        (ws.skills_dir / name / "SKILL.md").write_text(body, encoding="utf-8")
    ws.ensure()
    recorded = _manifest_on_disk(ws)
    assert ".claude/skills/same/SKILL.md" in recorded
    assert ".claude/skills/mine/SKILL.md" not in recorded
    assert (ws.skills_dir / "mine" / "SKILL.md").read_text(encoding="utf-8") == "edited"


def test_an_untouched_seed_refreshes_when_the_bundle_changes(tmp_path, monkeypatch):
    """Row 5: the record matches the file and the bundle moved on → overwrite.
    This is the `_copy_missing` defect's first half: `settings.json` and the
    bundled skills were frozen at whatever shipped the day the tree was made."""
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v1"}}, settings='{"a": 1}')
    ws = Workspace.for_user(tmp_path / "home", "users/7").ensure()
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v2"}}, settings='{"a": 2}')
    ws.ensure()
    assert (ws.skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "v2"
    assert (ws.claude_dir / "settings.json").read_text(encoding="utf-8") == '{"a": 2}'
    # …and the record follows, so the THIRD boot sees it as pristine again.
    assert _manifest_on_disk(ws)[".claude/skills/foo/SKILL.md"] == \
        workspace_mod._sha256(ws.skills_dir / "foo" / "SKILL.md")


def test_a_user_modified_seed_is_left_alone_and_reported_once(tmp_path, monkeypatch,
                                                              caplog):
    """Row 6: the record no longer matches the file → theirs. Logged at INFO once
    per tree per process: `get_home()` calls `ensure()` on every call, and a line
    per call would bury the one that matters."""
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7").ensure()
    (ws.skills_dir / "foo" / "SKILL.md").write_text("patched", encoding="utf-8")
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v2"}})
    with caplog.at_level(logging.INFO):
        ws.ensure()
        ws.ensure()
    assert (ws.skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "patched"
    reports = [r for r in caplog.records if "modified by the user" in r.getMessage()]
    assert len(reports) == 1 and ".claude/skills/foo/SKILL.md" in reports[0].getMessage()


def test_a_bundled_skill_refreshes_file_by_file(tmp_path, monkeypatch):
    """A skill is several files. The user patched `SKILL.md`; the bundle fixed
    `references/x.md` and added `scripts/y.sh`. Their patch stays, the reference
    refreshes, the new file lands — directory-level copying could do none of it."""
    _bundle(tmp_path, monkeypatch,
            skills={"foo": {"SKILL.md": "v1", "references/x.md": "x1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7").ensure()
    (ws.skills_dir / "foo" / "SKILL.md").write_text("patched", encoding="utf-8")
    _bundle(tmp_path, monkeypatch,
            skills={"foo": {"SKILL.md": "v2", "references/x.md": "x2",
                            "scripts/y.sh": "y"}})
    ws.ensure()
    foo = ws.skills_dir / "foo"
    assert (foo / "SKILL.md").read_text(encoding="utf-8") == "patched"
    assert (foo / "references" / "x.md").read_text(encoding="utf-8") == "x2"
    assert (foo / "scripts" / "y.sh").read_text(encoding="utf-8") == "y"


def test_claude_md_is_never_in_the_manifest(tmp_path, monkeypatch):
    """`seed_claude_md` and `_SHIPPED_PERSONA_SHA256` are a finished migration and
    `persona.md` is the delivery channel (B-9); a manifest entry for `CLAUDE.md`
    would make the refresh rule overwrite the agent's own file."""
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7").ensure()
    assert (ws.agent_home / "CLAUDE.md").is_file()
    assert not any(k.endswith("CLAUDE.md") or "persona" in k for k in _manifest_on_disk(ws))


def test_an_unparseable_manifest_reads_as_empty(tmp_path, monkeypatch):
    """The file lives where the agent can write it. Garbage there must cost one
    legacy classification, never a boot that raises."""
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7")
    ws.claude_dir.mkdir(parents=True)
    (ws.claude_dir / ".seeded.json").write_text("{not json", encoding="utf-8")
    assert SeedManifest(ws.agent_home).get(ws.skills_dir / "foo" / "SKILL.md") is None
    ws.ensure()
    assert (ws.skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "v1"
    assert ".claude/skills/foo/SKILL.md" in _manifest_on_disk(ws)
    # A well-formed file of the wrong shape is garbage too.
    (ws.claude_dir / ".seeded.json").write_text("[1, 2]", encoding="utf-8")
    assert SeedManifest(ws.agent_home).get(ws.skills_dir / "foo" / "SKILL.md") is None


def test_two_processes_saving_the_manifest_keep_both_entries(tmp_path):
    """R-11: agent, worker and scheduler all `ensure()` the same tree at boot.
    Each loads the manifest, records what IT seeded, and saves; the second save
    must not drop the first's entry, or the file it recorded reads as
    "user-modified" on every later boot and never refreshes."""
    agent_home = tmp_path / "agent_home"
    (agent_home / ".claude").mkdir(parents=True)
    a, b = SeedManifest(agent_home), SeedManifest(agent_home)
    a.record(agent_home / ".claude" / "skills" / "one" / "SKILL.md", "a" * 64)
    b.record(agent_home / ".claude" / "agents" / "two.md", "b" * 64)
    a.save()
    b.save()
    on_disk = json.loads((agent_home / ".claude" / ".seeded.json").read_text("utf-8"))
    assert on_disk == {".claude/skills/one/SKILL.md": "a" * 64,
                       ".claude/agents/two.md": "b" * 64}
    assert not list((agent_home / ".claude").glob("*.tmp"))


def test_without_a_manifest_the_seeders_never_overwrite(tmp_path):
    """`manifest=None` is the pre-4b path, byte for byte: the legacy callers in
    `tests/test_paths_config.py` and anything else that imports the three seeders
    keep the never-overwrite contract they were written against, and record
    nothing."""
    src = tmp_path / "src"
    (src / "skills" / "foo").mkdir(parents=True)
    (src / "skills" / "foo" / "SKILL.md").write_text("bundled", encoding="utf-8")
    (src / "agents").mkdir()
    (src / "agents" / "r.md").write_text("bundled", encoding="utf-8")
    (src / "settings.json").write_text("bundled", encoding="utf-8")
    ws = Workspace.owner(tmp_path / "home")
    (ws.skills_dir / "foo").mkdir(parents=True)
    ws.agents_dir.mkdir(parents=True)
    for p in (ws.skills_dir / "foo" / "SKILL.md", ws.agents_dir / "r.md",
              ws.claude_dir / "settings.json"):
        p.write_text("mine", encoding="utf-8")
    seed_skills(ws.skills_dir, source=src / "skills")
    seed_agents(ws.agents_dir, source=src / "agents")
    seed_settings(ws.claude_dir, source=src / "settings.json")
    for p in (ws.skills_dir / "foo" / "SKILL.md", ws.agents_dir / "r.md",
              ws.claude_dir / "settings.json"):
        assert p.read_text(encoding="utf-8") == "mine"
    assert not (ws.claude_dir / ".seeded.json").exists()


def test_a_pristine_copy_of_an_older_bundle_refreshes_on_the_first_boot(tmp_path, monkeypatch):
    """Advisor blocker 1 (code pass): the legacy row can only adopt a file that
    matches the CURRENT bundle, so a volume seeded from an older one — every
    live volume, for the four files 4b itself rewrites — would never refresh.
    `_PREVIOUSLY_SHIPPED_SEEDS` names those older versions; a file whose bytes
    are in it is ours, refreshed and recorded on the first boot."""
    import hashlib

    from claude_hermes.core import workspace as ws_mod
    _bundle(tmp_path, monkeypatch, skills={"foo": {"SKILL.md": "v2"}})
    ws = Workspace.for_user(tmp_path / "home", "users/7")
    (ws.skills_dir / "foo").mkdir(parents=True)
    (ws.skills_dir / "foo" / "SKILL.md").write_bytes(b"v1\r\n")       # CRLF, like a synced volume
    monkeypatch.setattr(ws_mod, "_PREVIOUSLY_SHIPPED_SEEDS",
                        frozenset({hashlib.sha256(b"v1\n").hexdigest()}))
    ws.ensure()
    assert (ws.skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "v2"
    assert _manifest_on_disk(ws)[".claude/skills/foo/SKILL.md"] == \
        hashlib.sha256(b"v2").hexdigest()


def test_a_legacy_seed_that_matches_no_shipped_version_is_said_once(tmp_path, monkeypatch,
                                                                     caplog):
    """The other half of the same blocker: a file the manifest cannot classify is
    left alone — and SAID, once per tree, with the remedy — rather than silently
    never refreshing, which is the failure the manifest exists to end."""
    import logging

    _bundle(tmp_path, monkeypatch, skills={"mine": {"SKILL.md": "v1"}})
    ws = Workspace.for_user(tmp_path / "home", "users/8")
    (ws.skills_dir / "mine").mkdir(parents=True)
    (ws.skills_dir / "mine" / "SKILL.md").write_text("edited", encoding="utf-8")
    with caplog.at_level(logging.INFO):
        ws.ensure()
        ws.ensure()
    said = [r for r in caplog.records if "predate the manifest" in r.getMessage()]
    assert len(said) == 1 and ".claude/skills/mine/SKILL.md" in said[0].getMessage()
    assert (ws.skills_dir / "mine" / "SKILL.md").read_text(encoding="utf-8") == "edited"
    assert ".claude/skills/mine/SKILL.md" not in _manifest_on_disk(ws)


def test_the_previously_shipped_set_covers_every_committed_version_of_the_4b_files():
    """The set is frozen by hand; this pins that the CURRENT bundle's bytes of the
    four rewritten files are NOT the only ones in it (an older version must be
    adoptable) and that the current bytes were not accidentally listed as
    'previous' — they refresh through the ordinary rows."""
    from claude_hermes.core import workspace as ws_mod
    assert len(ws_mod._PREVIOUSLY_SHIPPED_SEEDS) >= 8
    for rel in ("gpt-image-2/SKILL.md", "linkedin-post/SKILL.md",
                "linkedin-post/references/visuals.md", "skill-authoring/SKILL.md"):
        current = ws_mod._normalized_sha256((ws_mod._BUNDLED_SKILLS_DIR / rel).read_bytes())
        assert current not in ws_mod._PREVIOUSLY_SHIPPED_SEEDS, rel


# --- the shared wiki namespace ------------------------------------------------


def test_the_shared_wiki_root_hangs_off_home_for_everybody(tmp_path):
    owner = Workspace.owner(tmp_path)
    user = Workspace.for_user(tmp_path, "users/42")
    assert owner.shared_wiki_root == tmp_path / "agent_home" / "wiki" / "shared"
    assert user.shared_wiki_root == owner.shared_wiki_root
    # for the owner it is INSIDE their own wiki, for a user it is not
    assert owner.shared_wiki_root.is_relative_to(owner.wiki_root)
    assert not user.shared_wiki_root.is_relative_to(user.wiki_root)
    assert owner.is_owner and not user.is_owner


def test_the_owners_ensure_seeds_shared_once_and_a_users_does_not(tmp_path):
    user = Workspace.for_user(tmp_path, "users/42").ensure()
    assert not user.shared_wiki_root.exists()
    owner = Workspace.owner(tmp_path).ensure()
    index = owner.shared_wiki_root / "index.md"
    assert index.is_file()
    assert "read by EVERY admitted user" in index.read_text(encoding="utf-8")
    index.write_text("# mine now", encoding="utf-8")
    owner.ensure()
    assert index.read_text(encoding="utf-8") == "# mine now"

"""GET /api/commands: the composer's `/` menu — skills, plugins and the CLI's own
commands merged into one list, deduplicated by name."""
import json

import yaml

from claude_hermes.modules.extensions.domain.paths import plugin_dir

from tests.web_helpers import login, make_client

PLUGIN = {"manifest_version": 1, "name": "tools", "label": "Tools", "description": "d",
          "version": "1.0.0", "repo": "https://github.com/x/tools", "ref": "v1.0.0"}
NOW = "2026-09-10T10:00:00+00:00"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def skill(root, name, description, frontmatter_name=None):
    fm = f"name: {frontmatter_name or name}\ndescription: {description}\n"
    write(root / name / "SKILL.md", f"---\n{fm}---\n\n# {name}\n")


async def setup(tmp_path):
    # The catalog is passed in rather than set on `deps` afterwards: the composition
    # root builds the extensions query once, with the root it was given.
    root = tmp_path / "catalog"
    write(root / "plugins" / "tools.yaml", yaml.safe_dump(PLUGIN))
    write(root / "plugins" / "off.yaml", yaml.safe_dump({**PLUGIN, "name": "off"}))
    # `allowed=[2]`: the tests below log in as user 2, and since Wave 4b (R-8)
    # admission is re-checked per request, so a cookie alone is no longer enough.
    client, deps = await make_client(tmp_path, catalog_root=root, allowed=[2])
    skills = tmp_path / "agent_home" / ".claude" / "skills"
    skill(skills, "wiki-ingest", "File an external source into the wiki as a page.")
    skill(skills, "review", "The skill's own words.")
    skill(skills, "renamed-dir", "Named by its frontmatter.", frontmatter_name="nutrition-log")
    write(skills / "notes.md", "not a skill")
    for name in ("tools", "off"):
        pdir = plugin_dir(tmp_path, name)
        write(pdir / ".claude-plugin" / "plugin.json", json.dumps({"name": name}))
        write(pdir / "commands" / "deploy.md", "---\ndescription: Deploy it\n---\nDo the deploy.\n")
        write(pdir / "commands" / "nested" / "rollback.md", "Roll it back.\n")
        skill(pdir / "skills", "review-pr", "Review a PR")
        # A row per owner: since Wave 5 a plugin's clone is shared but its
        # enablement is personal, and `/api/commands` answers out of the CALLER's
        # rows. The tests below log in as 1 and as 2, so both adopt it.
        for uid in (1, 2):
            await deps.extensions.store.add(name, "plugin", "1.0.0", installed_by=uid,
                                            now=NOW, owner_id=uid)
    for uid in (1, 2):
        await deps.extensions.store.set_state("off", "disabled", owner_id=uid)
    return client, deps


async def test_requires_login(tmp_path):
    client, deps = await make_client(tmp_path)
    r = await client.get("/api/commands")
    assert r.status_code == 401
    login(client, 1)
    r = await client.get("/api/commands")
    assert r.status_code == 200 and r.json() == {"commands": []}


async def test_merges_the_three_sources_and_dedupes_by_name(tmp_path):
    client, deps = await setup(tmp_path)
    # What a finished turn writes to `meta` when the CLI reports its commands —
    # `deps.sdk_commands` is the `MetaSlashCommands` the route reads it back through.
    await deps.sdk_commands.save(["compact", "review", "__remote-workflow", "compact"])
    login(client, 2)                                  # any logged-in user, not just the owner
    r = await client.get("/api/commands")
    assert r.status_code == 200
    commands = r.json()["commands"]
    assert all(set(c) == {"name", "description", "source"} for c in commands)
    assert [c["name"] for c in commands] == [
        "nutrition-log", "review", "wiki-ingest",                # skills, by name
        "tools:deploy", "tools:rollback", "tools:review-pr",   # the enabled plugin: commands by stem, then skills
        "compact",                                                # what the CLI reported
    ]
    by = {c["name"]: c for c in commands}
    assert by["wiki-ingest"] == {"name": "wiki-ingest", "source": "skill",
                                 "description": "File an external source into the wiki as a page."}
    assert by["review"]["source"] == "skill" and by["review"]["description"] == "The skill's own words."
    assert by["tools:deploy"] == {"name": "tools:deploy", "description": "Deploy it", "source": "plugin"}
    assert by["tools:rollback"]["description"] == ""   # a nested file, no frontmatter
    assert by["tools:review-pr"] == {"name": "tools:review-pr", "description": "Review a PR",
                                     "source": "plugin"}
    assert by["compact"] == {"name": "compact", "description": "", "source": "sdk"}
    assert "off:deploy" not in by and "__remote-workflow" not in by


async def test_before_the_first_turn_the_list_is_skills_and_plugins(tmp_path):
    client, deps = await setup(tmp_path)
    assert await deps.sdk_commands.sdk_commands() == []      # nothing reported yet
    login(client, 1)
    sources = {c["source"] for c in (await client.get("/api/commands")).json()["commands"]}
    assert sources == {"skill", "plugin"}


async def test_plugin_manifest_names_what_it_adds_but_cannot_point_outside(tmp_path):
    client, deps = await setup(tmp_path)
    pdir = plugin_dir(tmp_path, "tools")
    write(tmp_path / "outside" / "leak.md", "---\ndescription: leaked\n---\n")
    write(pdir / "extra" / "ship.md", "---\ndescription: Ship it\n---\n")
    write(pdir / ".claude-plugin" / "plugin.json", json.dumps({
        "name": "toolbox",                                    # the CLI's namespace, not the catalog's
        "commands": ["./extra/ship.md", "../../outside"],     # one file in, one escape ignored
        "skills": "./skills/review-pr",                       # a single skill by path
    }))
    login(client, 1)
    names = [c["name"] for c in (await client.get("/api/commands")).json()["commands"]
             if c["source"] == "plugin"]
    assert names == ["toolbox:ship", "toolbox:review-pr"]     # commands/ no longer auto-loaded

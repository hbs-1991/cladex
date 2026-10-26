"""`SlashCommandsService`: the `/` menu, with the plugin half behind a port.

`tests/test_web_commands.py` drives the same code over HTTP with real plugin
clones; this one proves the module no longer needs to know what a plugin IS —
only where one was unpacked.

The route's half is here too (Wave 4c): the list is the CALLER's skills and the
CALLER's plugins, resolved through the `WorkspaceResolver` on the bundle, so a
second user does not read the deploy owner's tree.
"""
import json
from pathlib import Path

import httpx
from fastapi import FastAPI

from claude_hermes.modules.turns.application.commands_service import SlashCommandsService
from claude_hermes.modules.turns.presentation.commands_router import router


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def skill(root, name, description, frontmatter_name=None):
    fm = f"name: {frontmatter_name or name}\ndescription: {description}\n"
    write(root / name / "SKILL.md", f"---\n{fm}---\n\n# {name}\n")


class Sources:
    """A `SlashSourcePort` by shape, recording whose plugins were asked for."""

    def __init__(self, roots):
        self._roots = roots
        self.asked: list[int] = []

    async def plugin_roots(self, *, owner_id: int):
        self.asked.append(owner_id)
        return self._roots


async def test_skills_plugins_then_sdk_deduplicated_by_name(tmp_path):
    skills = tmp_path / "agent_home" / ".claude" / "skills"
    skill(skills, "review", "The skill's own words.")
    skill(skills, "renamed-dir", "Named by its frontmatter.", frontmatter_name="nutrition")
    write(skills / "notes.md", "not a skill")

    plugin = tmp_path / "plugins" / "tools"
    write(plugin / ".claude-plugin" / "plugin.json", json.dumps({"name": "tools"}))
    write(plugin / "commands" / "deploy.md", "---\ndescription: Deploy it\n---\n")

    svc = SlashCommandsService(home=tmp_path, sources=Sources([(plugin, "tools")]))
    out = await svc.collect(["compact", "review", "__plumbing", "compact"])
    assert [c["name"] for c in out] == ["nutrition", "review", "tools:deploy", "compact"]
    by = {c["name"]: c for c in out}
    assert by["review"]["source"] == "skill"          # the skill's description wins
    assert by["review"]["description"] == "The skill's own words."
    assert by["tools:deploy"] == {"name": "tools:deploy", "description": "Deploy it",
                                  "source": "plugin"}
    assert by["compact"] == {"name": "compact", "description": "", "source": "sdk"}


async def test_without_a_source_port_the_list_is_skills_and_the_cli(tmp_path):
    skill(tmp_path / "agent_home" / ".claude" / "skills", "one", "d")
    out = await SlashCommandsService(home=tmp_path).collect(["compact"])
    assert [c["source"] for c in out] == ["skill", "sdk"]


async def test_a_manifest_path_that_escapes_the_plugin_is_ignored(tmp_path):
    plugin = tmp_path / "plugins" / "tools"
    write(tmp_path / "outside" / "leak.md", "---\ndescription: leaked\n---\n")
    write(plugin / "extra" / "ship.md", "---\ndescription: Ship it\n---\n")
    write(plugin / ".claude-plugin" / "plugin.json", json.dumps({
        "name": "toolbox",
        "commands": ["./extra/ship.md", "../../outside"],
    }))
    svc = SlashCommandsService(home=tmp_path, sources=Sources([(plugin, "tools")]))
    assert [c["name"] for c in await svc.collect()] == ["toolbox:ship"]


async def test_no_home_means_no_list_at_all(tmp_path):
    assert await SlashCommandsService(home=None).collect(["compact"]) == [
        {"name": "compact", "description": "", "source": "sdk"}]


class FakeWorkspace:
    def __init__(self, root: Path):
        self.root = root


class FakeWorkspaces:
    """The `WorkspaceResolver`'s one method this route uses: `users/<id>` under home."""

    def __init__(self, home: Path):
        self._home = home

    async def for_user(self, user_id: int) -> FakeWorkspace:
        return FakeWorkspace(self._home / "users" / str(user_id))


class Deps:
    """The four bundle attributes the commands route reads. `workspaces=None` is the
    deployment - and the half of the web tests - that binds no resolver."""

    def __init__(self, *, home, workspaces=None, slash_sources=None, sdk_commands=None):
        self.home = home
        self.workspaces = workspaces
        self.slash_sources = slash_sources
        self.sdk_commands = sdk_commands


def app_for(deps, user_id: int) -> FastAPI:
    """The commands router alone, behind the one thing `CurrentUserMiddleware` puts on
    `request.state` that this route reads."""
    app = FastAPI()

    @app.middleware("http")
    async def _user(request, call_next):
        request.state.user_id = user_id
        return await call_next(request)

    app.include_router(router)
    app.state.deps = deps
    return app


async def get_commands(deps, user_id: int) -> list[dict]:
    transport = httpx.ASGITransport(app=app_for(deps, user_id))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/commands")
        assert r.status_code == 200
        return r.json()["commands"]


async def test_the_command_list_is_the_callers_own(tmp_path):
    """Two trees, two skills; the caller sees theirs and not the deploy owner's."""
    skill(tmp_path / "agent_home" / ".claude" / "skills", "owners", "the owner's")
    skill(tmp_path / "users" / "7" / "agent_home" / ".claude" / "skills", "hers", "hers")

    deps = Deps(home=tmp_path, workspaces=FakeWorkspaces(tmp_path))
    names = {c["name"] for c in await get_commands(deps, 7)}
    assert names == {"hers"}


async def test_the_plugin_half_is_asked_for_by_the_caller(tmp_path):
    """`plugin_roots` is per owner now: the id it is asked with is the requester's,
    not the deploy owner's, or a second user would load somebody else's plugins."""
    home = tmp_path / "users" / "7"
    plugin = tmp_path / "plugins" / "tools"
    write(plugin / ".claude-plugin" / "plugin.json", json.dumps({"name": "tools"}))
    write(plugin / "commands" / "deploy.md", "---\ndescription: Deploy it\n---\n")
    (home / "agent_home").mkdir(parents=True)

    sources = Sources([(plugin, "tools")])
    deps = Deps(home=tmp_path, workspaces=FakeWorkspaces(tmp_path), slash_sources=sources)
    assert [c["name"] for c in await get_commands(deps, 7)] == ["tools:deploy"]
    assert sources.asked == [7]


async def test_without_a_resolver_the_route_falls_back_to_the_deploy_home(tmp_path):
    """Half the web tests build a bundle with no `workspaces`; `deps.home` is still
    the answer there, and it is the only place the owner's tree is legitimate."""
    skill(tmp_path / "agent_home" / ".claude" / "skills", "owners", "the owner's")
    assert [c["name"] for c in await get_commands(Deps(home=tmp_path), 7)] == ["owners"]

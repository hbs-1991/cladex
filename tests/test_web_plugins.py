"""The plugins router: the wire shape, install/enable/disable/remove, and the two
states only disk can tell you about — a clone that vanished, and a row whose manifest
did. `clone_plugin` is faked; the real one is tested in test_install.py."""
import json

import yaml

from claude_hermes.modules.extensions.domain.paths import plugin_dir, plugin_root
from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog
from claude_hermes.modules.extensions.infrastructure import git_installer as install
from tests.web_helpers import json_headers, login, make_client

PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing",
    "description": "Does things. And a second sentence.", "version": "1.0.0",
    "repo": "https://github.com/o/r", "ref": "v1.0.0", "subdir": "marketing",
    "scopes": ["turn", "cron"],
}


def fake_clone(seen: dict, *, fail: str = ""):
    """Stand in for the git fetch: materialise (or refuse to materialise) a plugin."""
    async def clone(home, m, timeout=120.0):
        seen["timeout"] = timeout
        if fail:
            raise install.InstallError(fail)
        root = plugin_root(home, m)
        (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": m.name, "version": "2.1.0", "author": {"name": "Anthropic"}}),
            encoding="utf-8")
        (root / "skills" / "brand").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "brand" / "SKILL.md").write_text("x", encoding="utf-8")
        return root
    return clone


async def setup(tmp_path, monkeypatch, *, owner=1, fail=""):
    root = tmp_path / "catalog"
    (root / "plugins").mkdir(parents=True)
    (root / "plugins" / "thing.yaml").write_text(yaml.safe_dump(PLUGIN), encoding="utf-8")
    monkeypatch.setattr(catalog, "_CATALOG_DIR", root)
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    seen: dict = {}
    monkeypatch.setattr(install, "clone_plugin", fake_clone(seen, fail=fail))
    client, deps = await make_client(tmp_path, owner=owner, allowed=[2])
    return client, deps, seen


async def fetch(client, name="thing"):
    """Phase one on its own: what the owner is shown before anything is registered."""
    return await client.post(f"/api/plugins/{name}/install", headers=json_headers())


async def install_plugin(client, name="thing"):
    """Both phases, for the tests that are about what happens after the confirm."""
    preview = await fetch(client, name)
    assert preview.status_code == 200, preview.text
    return await client.post(f"/api/plugins/{name}/install/confirm",
                             headers=json_headers(),
                             json={"token": preview.json()["token"]})


async def test_an_uninstalled_plugin_lists_with_no_components(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 2)                                  # a plain user may read
    r = await client.get("/api/plugins")
    assert r.status_code == 200
    (p,) = r.json()["plugins"]
    assert p["name"] == "thing" and p["label"] == "Thing" and p["source"] == "catalog"
    assert p["state"] == "available" and p["present"] is False and p["components"] is None
    assert p["repo"] == PLUGIN["repo"] and p["ref"] == "v1.0.0" and p["subdir"] == "marketing"
    assert p["scopes"] == ["turn", "cron"] and p["installed_at"] is None


async def test_install_clones_registers_reads_the_clone_and_resets(tmp_path, monkeypatch):
    client, deps, seen = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await install_plugin(client)
    assert r.status_code == 200
    p = r.json()
    assert p["state"] == "installed" and p["present"] is True
    assert p["components"]["skills"] == ["brand"] and p["components"]["author"] == "Anthropic"
    assert p["components"]["plugin_version"] == "2.1.0" and p["components"]["hooks"] is False
    assert p["installed_at"]
    assert (await deps.extensions.store.get("thing", owner_id=1))["kind"] == "plugin"
    # The prefix changed — the INSTALLER's, and nobody else's.
    assert deps.runner.reset_owneds == [(1, True)]
    # Cloudflare cuts a request at 100s, so the configured 120s is capped below it.
    assert seen["timeout"] == 85.0


async def test_a_failed_clone_leaves_no_row_and_no_reset(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch, fail="fatal: could not read")
    login(client, 1)
    r = await fetch(client)
    assert r.status_code == 502 and r.json()["error"]["code"] == "install_failed"
    assert "fatal: could not read" in r.json()["error"]["message"]
    assert await deps.extensions.store.get("thing", owner_id=1) is None
    assert deps.runner.reset_owneds == []


async def test_disable_then_enable_round_trips_and_resets(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    await install_plugin(client)
    r = await client.post("/api/plugins/thing/disable", headers=json_headers())
    assert r.status_code == 200 and r.json()["state"] == "disabled"
    r = await client.post("/api/plugins/thing/enable", headers=json_headers())
    assert r.status_code == 200 and r.json()["state"] == "installed"
    # install + disable + enable, all three the caller's own lanes
    assert deps.runner.reset_owneds == [(1, True)] * 3
    r = await client.post("/api/plugins/nope/enable", headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_delete_drops_the_clone_the_row_and_resets(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    await install_plugin(client)
    clone = plugin_dir(tmp_path, "thing")
    assert clone.is_dir()
    r = await client.delete("/api/plugins/thing", headers=json_headers())
    assert r.status_code == 204
    assert not clone.exists() and await deps.extensions.store.get("thing", owner_id=1) is None
    # install, then the cascade — one reset per owner whose row pointed at the clone.
    assert deps.runner.reset_owneds == [(1, True), (1, True)]


async def test_touching_the_shared_clone_is_owner_only(tmp_path, monkeypatch):
    """Wave 5 split the gate along what is shared. `git clone` puts code in the
    container and deleting it takes it from everybody, so both stay the owner's.
    Enabling is the caller's row over that shared tree — a 404 here because there
    is no clone to adopt, which is a different refusal from a forbidden one."""
    client, deps, _ = await setup(tmp_path, monkeypatch, owner=9)
    login(client, 2)                                   # allowed, but not the owner
    assert (await client.get("/api/plugins")).status_code == 200
    for path in ("/api/plugins/thing/install", "/api/plugins/thing/install/confirm"):
        r = await client.post(path, headers=json_headers())
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = await client.delete("/api/plugins/thing", headers=json_headers())
    assert r.status_code == 403
    for path in ("/api/plugins/thing/enable", "/api/plugins/thing/disable"):
        r = await client.post(path, headers=json_headers())
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert deps.runner.reset_owneds == [] and deps.runner.reset_alls == 0


async def test_a_row_whose_manifest_vanished_still_lists_and_can_be_removed(tmp_path, monkeypatch):
    """Remove is the only way out of this state, so the row must be visible."""
    client, deps, _ = await setup(tmp_path, monkeypatch)
    await deps.extensions.store.add("ghost", "plugin", "0.9", installed_by=1,
                                    now="2026-09-09T10:00:00+00:00", owner_id=1)
    await deps.extensions.store.set_error("ghost", "no longer in the catalog", owner_id=1)
    login(client, 1)
    rows = {p["name"]: p for p in (await client.get("/api/plugins")).json()["plugins"]}
    assert set(rows) == {"thing", "ghost"}
    assert rows["ghost"]["state"] == "error" and rows["ghost"]["present"] is False
    assert rows["ghost"]["last_error"] == "no longer in the catalog"
    assert rows["ghost"]["components"] is None
    r = await client.delete("/api/plugins/ghost", headers=json_headers())
    assert r.status_code == 204 and await deps.extensions.store.get("ghost", owner_id=1) is None


async def test_an_installed_plugin_whose_clone_vanished_reports_present_false(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    await install_plugin(client)
    install.remove_plugin(tmp_path, "thing")           # a wiped volume, behind our back
    (p,) = (await client.get("/api/plugins")).json()["plugins"]
    assert p["state"] == "installed" and p["present"] is False and p["components"] is None


# --- The two phases -----------------------------------------------------------
#
# A plugin brings its own MCP servers and the CLI loads them without asking this
# catalog, so the whole point of phase one is that the owner reads what was fetched
# while it is still inert.


def with_mcp(servers: dict):
    """A clone that also ships a `.mcp.json`, the way real plugins do."""
    base = fake_clone({})

    async def clone(home, m, timeout=120.0):
        root = await base(home, m, timeout)
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}),
                                        encoding="utf-8")
        return root
    return clone


async def test_the_first_call_fetches_but_registers_nothing(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await fetch(client)
    assert r.status_code == 200
    body = r.json()
    assert body["token"] and body["components"]["skills"] == ["brand"]
    assert "skills:      brand" in body["surface"]
    assert "repo:        https://github.com/o/r" in body["surface"]
    # Fetched, readable — and not installed. `for_scope` reads rows, so this tree is
    # inert: no row, no reset, nothing handed to the SDK.
    assert await deps.extensions.store.get("thing", owner_id=1) is None
    assert deps.runner.reset_owneds == []
    assert plugin_dir(tmp_path, "thing").is_dir()
    (p,) = (await client.get("/api/plugins")).json()["plugins"]
    assert p["state"] == "available"


async def test_the_surface_names_every_server_the_plugin_brings(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "clone_plugin", with_mcp({
        "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        "notion": {"url": "https://mcp.notion.com/mcp"},          # no type: still remote
    }))
    login(client, 1)
    body = (await fetch(client)).json()
    assert [s["name"] for s in body["servers"]] == ["notion", "slack"]
    assert body["servers"][1] == {"name": "slack", "transport": "http",
                                  "target": "https://mcp.slack.com/mcp"}
    assert "https://mcp.notion.com/mcp" in body["surface"]
    assert "loaded by the CLI, not by this catalog" in body["surface"]


async def test_a_confirm_carries_the_token_for_the_bytes_that_were_read(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    token = (await fetch(client)).json()["token"]
    root = plugin_root(tmp_path, catalog.get("thing"))
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    (root / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")   # changed under it
    r = await client.post("/api/plugins/thing/install/confirm",
                          headers=json_headers(), json={"token": token})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_install"
    assert await deps.extensions.store.get("thing", owner_id=1) is None
    assert deps.runner.reset_owneds == []
    # Read it again and the new surface says what changed.
    again = (await fetch(client)).json()
    assert "hooks:       YES" in again["surface"]


async def test_a_confirm_with_nothing_fetched_is_refused(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await client.post("/api/plugins/thing/install/confirm",
                          headers=json_headers(), json={"token": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_fetched"
    assert await deps.extensions.store.get("thing", owner_id=1) is None


async def test_declining_a_fetched_plugin_takes_the_clone_with_it(tmp_path, monkeypatch):
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    await fetch(client)
    clone = plugin_dir(tmp_path, "thing")
    assert clone.is_dir()
    r = await client.delete("/api/plugins/thing", headers=json_headers())
    assert r.status_code == 204 and not clone.exists()
    # Nothing was registered, so nothing needed invalidating.
    assert deps.runner.reset_owneds == []


# --- A bundled server that would run a command --------------------------------


async def test_a_plugin_that_ships_a_local_server_is_refused(tmp_path, monkeypatch):
    """`npx -y whatever` inside `.mcp.json` is an argv that never passed the catalog's
    command allowlist. Real plugins do this — discord and fakechat in the official
    marketplace both run `bun run …` — so it is a rule, not a hypothetical."""
    client, deps, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "clone_plugin", with_mcp({
        "discord": {"command": "bun", "args": ["run", "--cwd", "x", "start"]},
        "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
    }))
    login(client, 1)
    r = await fetch(client)
    assert r.status_code == 400 and r.json()["error"]["code"] == "local_server"
    assert "discord would run: bun run --cwd x start" in r.json()["error"]["message"]
    assert "slack" not in r.json()["error"]["message"]        # only the local ones
    # Refused means gone: no row, no reset, and no tree left behind.
    assert await deps.extensions.store.get("thing", owner_id=1) is None
    assert deps.runner.reset_owneds == []
    assert not plugin_dir(tmp_path, "thing").exists()


async def test_an_unreadable_server_entry_counts_as_local(tmp_path, monkeypatch):
    """Fail closed where it is warranted: an entry that is not even a mapping could say
    anything, and the honest answer to 'I cannot tell' is yes. One that IS readable and
    carries no argv cannot start a process, whatever its transport says."""
    client, deps, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "clone_plugin", with_mcp({
        "weird": {"type": "websocket", "url": "wss://example.com"},
        "junk": "not even a mapping",
    }))
    login(client, 1)
    r = await fetch(client)
    assert r.status_code == 400 and r.json()["error"]["code"] == "local_server"
    assert "junk" in r.json()["error"]["message"]
    assert "weird" not in r.json()["error"]["message"]      # nothing to run, so not local


async def test_a_server_declared_remote_with_no_url_does_not_block_the_install(tmp_path, monkeypatch):
    """The real shape that refused three real plugins.

    `{"type": "http", "url": ""}` is how the knowledge-work marketplace ships `gmail`
    and `google calendar`: the endpoint is the client's to supply. Nothing runs in the
    container, so the install proceeds — and the surface still names them, because the
    owner approves the whole list, not the part we found convenient.
    """
    client, deps, _ = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "clone_plugin", with_mcp({
        "gmail": {"type": "http", "url": ""},
        "google calendar": {"type": "http", "url": ""},
        "slack": {"type": "http", "url": "https://mcp.slack.com/mcp"},
        "later": {"type": "sse", "url": "${TEAM_MCP_URL}"},
    }))
    login(client, 1)
    r = await fetch(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["name"] for s in body["servers"]] == ["gmail", "google calendar", "later", "slack"]
    assert all(s["transport"] in ("http", "sse") for s in body["servers"])
    assert "gmail  http  (no url — not configured)" in body["surface"]
    assert "slack  http  https://mcp.slack.com/mcp" in body["surface"]
    # And it really installs, rather than merely previewing.
    confirm = await client.post("/api/plugins/thing/install/confirm",
                                headers=json_headers(), json={"token": body["token"]})
    assert confirm.status_code == 200 and confirm.json()["state"] == "installed"


async def test_a_name_that_is_a_path_never_reaches_the_rmtree(tmp_path, monkeypatch):
    """DELETE skips `manifest_or_404` on purpose, and its no-row branch calls
    `install.remove_plugin` — an rmtree whose target is built from the URL."""
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    victim = tmp_path / "agent_home" / "plugins"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "keep.txt").write_text("x", encoding="utf-8")
    r = await client.delete("/api/plugins/" + "..", headers=json_headers())
    assert r.status_code in (307, 404)
    r = await client.delete("/api/plugins/" + chr(46) * 2 + chr(92) + "plugins",
                            headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert (victim / "keep.txt").is_file()


async def test_refetching_an_installed_plugin_invalidates_the_prefix_at_once(tmp_path, monkeypatch):
    """Phase one over an installed plugin has already replaced the tree the SDK loads,
    and the confirm may never come — so the reset cannot wait for it."""
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    await install_plugin(client)
    before = len(deps.runner.reset_owneds)
    assert (await fetch(client)).status_code == 200
    # One per owner holding a row for the clone that was just replaced.
    assert deps.runner.reset_owneds[before:] == [(1, True)]


async def test_a_first_fetch_resets_nothing(tmp_path, monkeypatch):
    """No row means nothing loads the tree, so no prefix describes it."""
    client, deps, _ = await setup(tmp_path, monkeypatch)
    login(client, 1)
    assert (await fetch(client)).status_code == 200
    assert deps.runner.reset_owneds == []

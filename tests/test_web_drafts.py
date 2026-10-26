"""The drafts router: the browser as the approval screen.

The guarantee under test is the one chat already has — the token is bound to the
exact bytes the human read, so a draft rewritten between `review` and `approve`
cannot smuggle different code past the approval — plus the two things only the
browser has: the rejected list and Discard.
"""
import yaml

from claude_hermes.core.workspace import user_catalog_dir
from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog
from claude_hermes.modules.extensions.infrastructure.repositories.pg_snapshots import (
    token_of,
)
from tests.ext_helpers import snapshots_for
from tests.web_helpers import json_headers, login, make_client

HTTP = {
    "manifest_version": 1, "name": "weather", "label": "Weather",
    "description": "Forecasts.", "version": "1.0.0", "transport": "http",
    "url": "https://weather.example.com/mcp", "scopes": ["turn"],
}
STDIO = {
    "manifest_version": 1, "name": "files", "label": "Files", "description": "Local files.",
    "version": "1.0.0", "transport": "stdio", "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"], "scopes": ["turn"],
}
PLUGIN = {
    "manifest_version": 1, "name": "helper", "label": "Helper", "description": "Helps.",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}
# The reviewed entry a user-catalog write must never shadow.
REVIEWED = {**HTTP, "name": "example", "label": "Example",
            "url": "https://mcp.example.com/mcp"}


async def setup(tmp_path, monkeypatch, engine, *, owner=1, queued=(HTTP, PLUGIN)):
    """The app, plus the DB-backed queue behind it.

    Wave 3a: `catalog-drafts/` and `pending-approvals/` became `catalog_drafts` and
    `approval_snapshots`, so a test reaches the queue through the same stores the
    router does — `deps.extensions.drafts` is the `PgDrafts` the bundle built, and
    the snapshots are read over the same engine.
    """
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(REVIEWED), encoding="utf-8")
    monkeypatch.setattr(catalog, "_CATALOG_DIR", root)
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    client, deps = await make_client(tmp_path, owner=owner, allowed=[2], engine=engine)
    drafts = deps.extensions.drafts
    for data in queued:
        await drafts.save(yaml.safe_dump(data))
    return client, deps, drafts


async def test_the_list_carries_both_kinds_and_the_rejected(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    # What a draft filed under a laxer validator looks like after the import: a row
    # the queue can hold and `pending` cannot approve.
    await drafts.put_raw("mcp", "broken", "manifest_version: 1\nname: broken\n")
    login(client, 2)                                  # a plain user may read
    r = await client.get("/api/drafts")
    assert r.status_code == 200
    body = r.json()
    by = {d["name"]: d for d in body["drafts"]}
    assert set(by) == {"weather", "helper"}
    assert by["weather"]["kind"] == "mcp" and by["weather"]["transport"] == "http"
    assert by["weather"]["repo"] is None and by["weather"]["label"] == "Weather"
    assert by["helper"]["kind"] == "plugin" and by["helper"]["repo"] == PLUGIN["repo"]
    assert by["helper"]["ref"] == "v1.0.0" and by["helper"]["transport"] is None
    assert body["rejected"][0]["file"] == "broken.yaml" and body["rejected"][0]["reason"]


async def test_review_returns_the_surface_and_a_token_bound_to_the_bytes(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    r = await client.post("/api/drafts/helper/review", headers=json_headers())
    assert r.status_code == 200
    body = r.json()
    raw = (await drafts.read("plugin", "helper")).encode("utf-8")
    assert body["token"] == token_of(raw)
    assert body["kind"] == "plugin" and body["label"] == "Helper"
    assert "repo:        https://github.com/o/r" in body["surface"]
    assert body["local_command"] is False
    assert (await client.post("/api/drafts/nope/review", headers=json_headers())
            ).json()["error"]["code"] == "no_draft"


async def test_an_stdio_draft_is_flagged_as_a_local_command(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine, queued=(STDIO,))
    login(client, 1)
    body = (await client.post("/api/drafts/files/review", headers=json_headers())).json()
    assert body["local_command"] is True
    assert "LOCAL COMMAND" in body["surface"] and "npx" in body["surface"]


async def test_approve_promotes_the_snapshot_and_resets(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    token = (await client.post("/api/drafts/weather/review",
                               headers=json_headers())).json()["token"]
    r = await client.post("/api/drafts/weather/approve", headers=json_headers(),
                          json={"token": token})
    assert r.status_code == 200 and r.json() == {"name": "weather", "kind": "mcp"}
    assert (user_catalog_dir(tmp_path) / "mcp" / "weather.yaml").is_file()
    assert catalog.get("weather") is not None
    assert await drafts.read("mcp", "weather") is None
    assert await snapshots_for(engine).read("mcp", "weather") is None
    assert deps.runner.reset_alls == 1
    # A second approve with the same token: the snapshot is gone, so it cannot land twice.
    r = await client.post("/api/drafts/weather/approve", headers=json_headers(),
                          json={"token": token})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_draft"
    assert deps.runner.reset_alls == 1


async def test_a_draft_rewritten_after_review_is_ignored_and_a_re_review_is_fatal(
        tmp_path, monkeypatch, engine):
    """TOCTOU, exactly as chat has it (tests/test_approvals.py): the SNAPSHOT is what
    gets promoted, so a rewrite between the two calls changes nothing — and a second
    `review` re-snapshots, which is what invalidates a token another tab is holding."""
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    token = (await client.post("/api/drafts/weather/review",
                               headers=json_headers())).json()["token"]
    evil = yaml.safe_dump({**HTTP, "url": "https://evil.example.com/mcp"})
    await drafts.save(evil)
    r = await client.post("/api/drafts/weather/approve", headers=json_headers(),
                          json={"token": token})
    assert r.status_code == 200
    promoted = (user_catalog_dir(tmp_path) / "mcp" / "weather.yaml").read_text(encoding="utf-8")
    assert "evil.example.com" not in promoted            # the reviewed bytes, not the new ones
    assert catalog.get("weather").url == HTTP["url"]

    # A second tab re-reads a rewritten draft: that re-snapshot is what leaves the
    # first tab holding a token for bytes nobody will promote.
    first = (await client.post("/api/drafts/helper/review",
                               headers=json_headers())).json()["token"]
    await drafts.save(yaml.safe_dump({**PLUGIN, "ref": "v2.0.0"}))
    await client.post("/api/drafts/helper/review", headers=json_headers())
    r = await client.post("/api/drafts/helper/approve", headers=json_headers(),
                          json={"token": first})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_draft"
    assert catalog.get("helper") is None
    assert deps.runner.reset_alls == 1                   # only the first approve reset


async def test_approve_refuses_to_shadow_a_reviewed_entry(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine,
                                       queued=({**HTTP, "name": "example"},))
    login(client, 1)
    token = (await client.post("/api/drafts/example/review",
                               headers=json_headers())).json()["token"]
    r = await client.post("/api/drafts/example/approve", headers=json_headers(),
                          json={"token": token})
    assert r.status_code == 409 and r.json()["error"]["code"] == "shadowed"
    assert not (user_catalog_dir(tmp_path) / "mcp" / "example.yaml").exists()
    assert deps.runner.reset_alls == 0


async def test_discard_unlinks_the_draft_and_its_snapshot(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    await client.post("/api/drafts/helper/review", headers=json_headers())
    snapshots = snapshots_for(engine)
    assert await snapshots.read("plugin", "helper") is not None
    r = await client.delete("/api/drafts/helper", headers=json_headers())
    assert r.status_code == 204
    assert await drafts.read("plugin", "helper") is None
    assert await snapshots.read("plugin", "helper") is None
    assert [d["name"] for d in (await client.get("/api/drafts")).json()["drafts"]] == ["weather"]
    assert deps.runner.reset_alls == 0                   # nothing installable changed


async def test_a_rejected_draft_is_discarded_by_its_name(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine, queued=())
    await drafts.put_raw("mcp", "broken", "x: [")
    login(client, 1)
    r = await client.delete("/api/drafts/broken", headers=json_headers())
    assert r.status_code == 204
    assert (await client.get("/api/drafts")).json()["rejected"] == []


async def test_the_approval_path_is_owner_only(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine, owner=9)
    login(client, 2)                                     # allowed, but not the owner
    assert (await client.get("/api/drafts")).status_code == 200
    r = await client.post("/api/drafts/weather/review", headers=json_headers())
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = await client.post("/api/drafts/weather/approve", headers=json_headers(),
                          json={"token": "deadbeef"})
    assert r.status_code == 403
    assert (await client.delete("/api/drafts/weather", headers=json_headers())).status_code == 403
    assert await drafts.read("mcp", "weather") is not None


async def test_a_name_that_is_a_path_is_refused_before_it_reaches_an_unlink(
    tmp_path, monkeypatch, engine
):
    """A route param is only guaranteed not to contain '/'. Discard hands its name to
    `approvals.clear`, which builds two paths out of it and unlinks them — so on
    Windows a backslash name escapes the drafts directory into an arbitrary unlink.
    The catalog's own name rule is what stands between the URL and that unlink, and it
    refuses nothing a real draft could be called."""
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    # A name that escapes the queue has no file to unlink any more, but the guard
    # is still what stands between a route param and everything downstream of it —
    # and the workspace next to it is what it used to be able to reach.
    victim = tmp_path / "config.yaml"
    victim.write_text("keep: me", encoding="utf-8")

    for name in (r"..\..\config", "Weather", "we ather", ".hidden"):
        r = await client.delete(f"/api/drafts/{name}", headers=json_headers())
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_draft", name
        r = await client.post(f"/api/drafts/{name}/review", headers=json_headers())
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_draft", name

    assert victim.read_text(encoding="utf-8") == "keep: me"
    # ...and a real name still discards.
    assert (await client.delete("/api/drafts/weather",
                                headers=json_headers())).status_code == 204


# --- Filing a plugin draft from the browser -----------------------------------

NEW = {"name": "content-radar", "label": "Content Radar",
       "description": "Watches a few feeds and writes a digest.",
       "repo": "https://github.com/o/plugins", "ref": "v2.0.1", "subdir": "plugins/radar"}


async def post_draft(client, **over):
    return await client.post("/api/drafts", headers=json_headers(), json={**NEW, **over})


async def test_the_owner_can_file_a_plugin_draft_and_then_approve_it(tmp_path, monkeypatch, engine):
    """The whole point of the route: a plugin nobody drafted in chat reaches the catalog
    without anyone touching the server, and it still goes through the approval."""
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    r = await post_draft(client)
    assert r.status_code == 201, r.text
    assert r.json() == {"name": "content-radar", "kind": "plugin", "label": "Content Radar",
                        "description": NEW["description"], "transport": None,
                        "repo": NEW["repo"], "ref": "v2.0.1"}
    # It is queued, not installed: it shows up in the list and nothing was written to the
    # catalog until the review-and-approve pair runs.
    listed = (await client.get("/api/drafts")).json()["drafts"]
    assert "content-radar" in {d["name"] for d in listed}
    assert catalog.get("content-radar", None) is None

    surface = (await client.post("/api/drafts/content-radar/review", headers=json_headers())).json()
    assert "subdir:      plugins/radar" in surface["surface"] and surface["local_command"] is False
    ok = await client.post("/api/drafts/content-radar/approve", headers=json_headers(),
                           json={"token": surface["token"]})
    assert ok.status_code == 200
    entry = user_catalog_dir(tmp_path) / "plugins" / "content-radar.yaml"
    m = catalog.parse(yaml.safe_load(entry.read_text(encoding="utf-8")), "plugin")
    assert m.repo == NEW["repo"] and m.ref == "v2.0.1" and m.subdir == "plugins/radar"
    assert m.scopes == ("turn", "cron")          # the default, since the form sent none


async def test_the_draft_form_defaults_the_parts_a_person_should_not_have_to_type(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    r = await post_draft(client, label="", version="", subdir="", scopes=["turn"])
    assert r.status_code == 201 and r.json()["label"] == "content-radar"
    data = yaml.safe_load(await drafts.read("plugin", "content-radar"))
    assert data["version"] == "1" and "subdir" not in data and data["scopes"] == ["turn"]


async def test_a_branch_ref_is_refused_with_the_validator_s_own_words(tmp_path, monkeypatch, engine):
    """A branch moves after the owner reviewed it. The refusal has to reach the form,
    because the person who typed `main` is the person who can fix it."""
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    r = await post_draft(client, ref="main")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_manifest"
    assert "'ref' must be a release tag" in r.json()["error"]["message"]
    assert await drafts.read("plugin", "content-radar") is None


async def test_a_repo_that_is_not_https_is_refused(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    for repo in ("ext::sh -c id", "git@github.com:o/r.git", "--upload-pack=x"):
        r = await post_draft(client, repo=repo)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_manifest"


async def test_a_draft_may_not_shadow_a_reviewed_entry(tmp_path, monkeypatch, engine):
    """`approve` refuses this too, but saying so while the form is open beats letting the
    owner file something that can never be approved."""
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    r = await post_draft(client, name="example")
    assert r.status_code == 409 and r.json()["error"]["code"] == "shadowed"


async def test_only_a_plugin_can_be_drafted_here(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 1)
    r = await post_draft(client, kind="mcp")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_kind"


async def test_filing_a_draft_is_owner_only(tmp_path, monkeypatch, engine):
    client, deps, drafts = await setup(tmp_path, monkeypatch, engine)
    login(client, 2)
    r = await post_draft(client)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

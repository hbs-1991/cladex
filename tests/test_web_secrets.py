"""The secrets router: the stored names beside the declared ones, set and forget,
and the owner gate on every path including the read."""
import yaml

from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog
from tests.web_helpers import json_headers, login, make_client

HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "Example API key",
              "header": "Authorization", "format": "Bearer ${EXAMPLE_API_KEY}"}],
}
NOTION = {
    "manifest_version": 1, "name": "notion", "label": "Notion", "description": "d",
    "version": "1", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn", "cron"],
    "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth", "header": "Authorization",
              "format": "Bearer ${NOTION_MCP_TOKEN}"}],
}


async def setup(tmp_path, monkeypatch, *, owner=1):
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")
    (root / "mcp" / "notion.yaml").write_text(yaml.safe_dump(NOTION), encoding="utf-8")
    monkeypatch.setattr(catalog, "_CATALOG_DIR", root)
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    return await make_client(tmp_path, owner=owner, allowed=[2])


async def test_the_list_carries_declared_names_and_never_a_value(tmp_path, monkeypatch):
    """A declared-but-unset secret is a row, so the list is what the deployment
    needs — and an unset *oauth* name is not, because it is obtained by signing in."""
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await client.get("/api/secrets")
    assert r.status_code == 200
    assert r.json()["secrets"] == [
        {"name": "EXAMPLE_API_KEY", "set": False, "updated_at": None, "used_by": ["Example"]}]

    await deps.extensions.secrets.put("EXAMPLE_API_KEY", "abc", owner_id=1)
    await deps.extensions.secrets.put("NOTION_MCP_TOKEN", "at-1", owner_id=1)
    await deps.extensions.secrets.put("OPENAI_API_KEY", "sk-zzz", owner_id=1)
    r = await client.get("/api/secrets")
    rows = {s["name"]: s for s in r.json()["secrets"]}
    assert [s["name"] for s in r.json()["secrets"]] == sorted(rows)      # alphabetical
    assert rows["EXAMPLE_API_KEY"]["set"] and rows["EXAMPLE_API_KEY"]["used_by"] == ["Example"]
    assert rows["EXAMPLE_API_KEY"]["updated_at"]
    # A token that arrived by signing in is listed once it exists, and says whose it is.
    assert rows["NOTION_MCP_TOKEN"]["used_by"] == ["Notion"]
    # One nothing declares: the whole point of this door.
    assert rows["OPENAI_API_KEY"]["used_by"] == [] and rows["OPENAI_API_KEY"]["set"]
    assert "abc" not in r.text and "sk-zzz" not in r.text and "at-1" not in r.text


async def test_put_and_delete_round_trip(tmp_path, monkeypatch):
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await client.put("/api/secrets/OPENAI_API_KEY", headers=json_headers(),
                         json={"value": "sk-1"})
    assert r.status_code == 204
    assert (await deps.extensions.secrets.load(owner_id=1))["OPENAI_API_KEY"] == "sk-1"
    r = await client.put("/api/secrets/OPENAI_API_KEY", headers=json_headers(),
                         json={"value": "sk-2"})                          # overwrite
    assert r.status_code == 204
    assert (await deps.extensions.secrets.load(owner_id=1))["OPENAI_API_KEY"] == "sk-2"

    r = await client.delete("/api/secrets/OPENAI_API_KEY", headers=json_headers())
    assert r.status_code == 204
    assert "OPENAI_API_KEY" not in await deps.extensions.secrets.load(owner_id=1)
    # Absent is fine, and a declared name deleted stays in the list as unset.
    assert (await client.delete("/api/secrets/OPENAI_API_KEY", headers=json_headers())).status_code == 204
    await deps.extensions.secrets.put("EXAMPLE_API_KEY", "abc", owner_id=1)
    await client.delete("/api/secrets/EXAMPLE_API_KEY", headers=json_headers())
    r = await client.get("/api/secrets")
    assert r.json()["secrets"] == [
        {"name": "EXAMPLE_API_KEY", "set": False, "updated_at": None, "used_by": ["Example"]}]


async def test_a_bad_name_or_value_is_refused(tmp_path, monkeypatch):
    """The domain's rule, the same one `/secret` applies: an environment variable
    name, and a value a process environment can carry."""
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    for name in ("lower_case", "1LEADING", "WITH-DASH"):
        r = await client.put(f"/api/secrets/{name}", headers=json_headers(), json={"value": "x"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", name
    r = await client.put("/api/secrets/GOOD_NAME", headers=json_headers(),
                         json={"value": "one\ntwo"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = await client.put("/api/secrets/GOOD_NAME", headers=json_headers(), json={"value": ""})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = await client.delete("/api/secrets/lower_case", headers=json_headers())
    assert r.status_code == 400
    assert await deps.extensions.secrets.load(owner_id=1) == {}


async def test_every_path_is_owner_only(tmp_path, monkeypatch):
    """Including the read: a list of what credentials a deployment holds is not
    something a plain user may see."""
    client, deps = await setup(tmp_path, monkeypatch)
    await deps.extensions.secrets.put("OPENAI_API_KEY", "sk-1", owner_id=1)
    login(client, 2)
    r = await client.get("/api/secrets")
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = await client.put("/api/secrets/OPENAI_API_KEY", headers=json_headers(), json={"value": "x"})
    assert r.status_code == 403
    r = await client.delete("/api/secrets/OPENAI_API_KEY", headers=json_headers())
    assert r.status_code == 403
    assert (await deps.extensions.secrets.load(owner_id=1))["OPENAI_API_KEY"] == "sk-1"

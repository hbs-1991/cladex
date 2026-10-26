"""Contract tests for the ports this module declares.

Each one is written against a fake so it states what the port promises, not what the
current adapter happens to do — which is the point of the port. Wave 3a made that
swap: `SecretStore` is `PgSecrets` over `extension_secrets` and `PendingStateStore`
has a Redis implementation next to the in-memory one, and the promises below are
unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claude_hermes.modules.extensions.application.services.compose_service import (
    ComposeService)
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.domain.oauth import NeedsReauth
from claude_hermes.modules.extensions.domain.probe import ProbeResult
from claude_hermes.modules.extensions.infrastructure.file_catalog_writer import (
    FileCatalogWriter)
from claude_hermes.modules.extensions.infrastructure.mcp_prober import SdkServerProber
from claude_hermes.modules.extensions.infrastructure.pending_states import PendingStates

from conftest import ensure_users
from tests.ext_helpers import secrets_for

HTTP = {
    "manifest_version": 1, "name": "srv", "label": "Srv", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn", "cron"],
}
OAUTH = {**HTTP, "name": "notion", "url": "https://mcp.notion.com/mcp",
         "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth",
                   "header": "Authorization", "format": "Bearer ${NOTION_MCP_TOKEN}"}]}


class FakeCatalog:
    def __init__(self, entries):
        self._entries = entries

    def load_all(self, root=None):
        return dict(self._entries)

    def get(self, name, root=None):
        return self._entries.get(name)

    def is_repo_entry(self, name):
        return False


class FakeStore:
    def __init__(self, rows):
        self.rows = {r["name"]: r for r in rows}
        self.errors: list[tuple[str, str | None]] = []

    async def enabled(self, *, owner_id: int):
        return [dict(r) for r in self.rows.values()]

    async def set_error(self, name, error, *, owner_id: int):
        self.errors.append((name, error))
        self.rows[name]["last_error"] = error


def _row(name, kind="mcp", last_error=None):
    return {"name": name, "kind": kind, "state": "enabled", "catalog_version": "1",
            "installed_at": "now", "last_error": last_error, "tool_count": None,
            "tool_names": None}


# --- SecretStore ------------------------------------------------------------------

async def test_secret_store_round_trips_and_forgets(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    assert await s.load(owner_id=1) == {}
    await s.put("A_TOKEN", "v1", owner_id=1)
    assert (await s.load(owner_id=1))["A_TOKEN"] == "v1"
    await s.put("A_TOKEN", "v2", owner_id=1)     # upsert, not append
    assert (await s.load(owner_id=1))["A_TOKEN"] == "v2"
    await s.forget("A_TOKEN", owner_id=1)
    assert "A_TOKEN" not in await s.load(owner_id=1)
    await s.forget("A_TOKEN", owner_id=1)        # absent is fine


async def test_secret_store_refuses_a_value_that_would_corrupt_the_store(engine):
    from claude_hermes.modules.extensions.domain.exceptions import SecretNameError
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    with pytest.raises(SecretNameError):
        await s.put("not a name", "v", owner_id=1)
    with pytest.raises(SecretNameError):
        await s.put("A_TOKEN", "line1\nline2", owner_id=1)


# --- CatalogWriter ----------------------------------------------------------------

def test_catalog_writer_promotes_the_exact_bytes_it_was_given(tmp_path):
    """The approval is bound to bytes, so the writer must not re-serialise them."""
    from claude_hermes.core.workspace import user_catalog_dir
    m = parse(HTTP, "mcp")
    raw = b"# a comment the parser drops\nname: srv\n"
    FileCatalogWriter(tmp_path).promote(m, raw)
    assert (user_catalog_dir(tmp_path) / "mcp" / "srv.yaml").read_bytes() == raw


# --- PendingStateStore ------------------------------------------------------------

async def test_pending_state_is_single_use_owner_bound_and_expiring():
    p = PendingStates()
    state = await p.issue("notion", owner_id=7, verifier="v", now=0.0)
    assert await p.take(state, 9, 1.0) is None    # someone else's callback consumes nothing
    got = await p.take(state, 7, 1.0)
    assert got is not None and got.verifier == "v"
    assert await p.take(state, 7, 1.0) is None    # single use

    stale = await p.issue("notion", owner_id=7, verifier="v", now=0.0)
    assert await p.take(stale, 7, 10_000.0) is None     # past the TTL


# --- ServerProber -----------------------------------------------------------------

async def test_server_prober_is_callable_with_the_port_signature(monkeypatch):
    """`WebDeps.probe` is a bare async function in the tests and an object in
    production; both have to answer the same call."""
    from claude_hermes.modules.extensions.infrastructure import mcp_prober

    async def fake(home, name, spec, timeout=60.0):
        return ProbeResult(status="connected", tools=["t"])

    monkeypatch.setattr(mcp_prober, "probe", fake)
    out = await SdkServerProber()(Path("."), "srv", {"type": "http"}, timeout=1.0)
    assert out.status == "connected" and out.tools == ["t"]


# --- ScopeComposer (what the runner and the cron pass consume) ---------------------

async def test_compose_only_judges_the_scope_it_evaluated():
    """A cron pass must not clear an error the turn recorded about a turn-only server
    it never looked at."""
    turn_only = parse({**HTTP, "name": "turnly", "scopes": ["turn"]}, "mcp")
    svc = ComposeService(
        Path("."), catalog=FakeCatalog({"turnly": turn_only}),
        secrets=_Secrets({}), store=FakeStore([_row("turnly", last_error="boom")]),
        tokens=None)
    out = await svc.for_scope("cron", owner_id=1)
    assert out.mcp_servers == {} and out.errors == {}
    assert svc._store.errors == []                # nothing persisted for this scope


async def test_compose_records_needs_reauth_verbatim():
    """The connectors tab derives the `needs_reauth` state from this exact string."""
    m = parse(OAUTH, "mcp")

    class Refuser:
        async def ensure_fresh(self, home, tokens, manifest, *, owner_id, now=None):
            raise NeedsReauth("invalid_grant")

    store = FakeStore([_row("notion")])
    out = await ComposeService(Path("."), catalog=FakeCatalog({"notion": m}),
                               secrets=_Secrets({}), store=store, tokens=None,
                               oauth=Refuser()).for_scope("turn", owner_id=1)
    assert out.errors["notion"] == "needs_reauth"
    assert ("notion", "needs_reauth") in store.errors


async def test_compose_re_reads_secrets_after_a_refresh():
    """Read-your-writes: the refresh mirrors a new access token into the store, and the
    spec must carry that one and not the value loaded at the top of the pass."""
    m = parse(OAUTH, "mcp")
    secrets = _Secrets({"NOTION_MCP_TOKEN": "stale"})

    class Refresher:
        async def ensure_fresh(self, home, tokens, manifest, *, owner_id, now=None):
            await secrets.put("NOTION_MCP_TOKEN", "fresh", owner_id=owner_id)

    out = await ComposeService(Path("."), catalog=FakeCatalog({"notion": m}),
                               secrets=secrets, store=FakeStore([_row("notion")]),
                               tokens=None,
                               oauth=Refresher()).for_scope("turn", owner_id=1)
    assert out.mcp_servers["notion"]["headers"] == {"Authorization": "Bearer fresh"}


class _Secrets:
    """The `SecretStore` port by shape only — async since Wave 3a."""

    def __init__(self, values):
        self.values = dict(values)

    async def load(self, *, owner_id: int):
        return dict(self.values)

    async def put(self, name, value, *, owner_id: int):
        self.values[name] = value

    async def forget(self, name, *, owner_id: int):
        self.values.pop(name, None)

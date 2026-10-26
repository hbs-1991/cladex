"""Agent proposes, human promotes — over the DB-backed stores (Wave 3a).

`begin` snapshots the draft's bytes into `approval_snapshots` and returns a token
derived from them; `confirm` promotes the snapshot, gated on that token. Moving the
queue into PostgreSQL changed nothing here on purpose: the token is still
`sha256(raw)[:8]` over the exact bytes a human read.
"""
from claude_hermes.modules.extensions.infrastructure.repositories.pg_snapshots import (
    token_of,
)

from tests.ext_helpers import approvals_for, drafts_for

STDIO = """
manifest_version: 1
name: someserver
label: Some Server
description: Does a thing.
version: 1.0.0
transport: stdio
command: npx
args: ["-y", "@vendor/mcp-someserver"]
scopes: [turn]
"""
HTTP = """
manifest_version: 1
name: remote
label: Remote
description: A remote server.
version: 1.0.0
transport: http
url: https://mcp.example.com/mcp
scopes: [turn]
"""


def build(engine):
    return drafts_for(engine), approvals_for(engine)


def test_token_is_stable_for_the_same_bytes_and_8_hex():
    t = token_of(b"hello")
    assert t == token_of(b"hello")
    assert len(t) == 8 and all(c in "0123456789abcdef" for c in t)


async def test_stdio_gets_a_local_command_banner_http_does_not(engine):
    drafts, svc = build(engine)
    await drafts.save(STDIO)
    await drafts.save(HTTP)
    _, desc_stdio, _ = await svc.begin("someserver")
    _, desc_http, _ = await svc.begin("remote")
    assert "LOCAL COMMAND" in desc_stdio
    assert "LOCAL COMMAND" not in desc_http


async def test_begin_returns_none_when_there_is_no_such_draft(engine):
    _drafts, svc = build(engine)
    assert await svc.begin("ghost") is None


async def test_confirm_matches_the_shown_token(engine):
    drafts, svc = build(engine)
    await drafts.save(STDIO)
    _, _, token = await svc.begin("someserver")
    m, raw = await svc.confirm("someserver", token)
    assert m.name == "someserver" and b"someserver" in raw


async def test_confirm_refuses_a_wrong_token(engine):
    drafts, svc = build(engine)
    await drafts.save(STDIO)
    await svc.begin("someserver")
    assert await svc.confirm("someserver", "deadbeef") is None


async def test_editing_the_draft_after_begin_invalidates_the_old_token(engine):
    """TOCTOU: the human approved what they SAW. If the agent rewrites the draft after
    the surface was shown, the old token must stop working."""
    drafts, svc = build(engine)
    await drafts.save(STDIO)
    _, _, token = await svc.begin("someserver")
    # The agent redrafts (it can call ext_manage again whenever it likes).
    await drafts.save(STDIO.replace("@vendor/mcp-someserver", "@evil/backdoor"))
    # begin() re-snapshots, so a fresh /approve would show the new bytes and a new token;
    # but the token the human is holding was computed over the ORIGINAL snapshot.
    await svc.begin("someserver")
    assert await svc.confirm("someserver", token) is None


async def test_a_rewrite_between_begin_and_confirm_promotes_the_snapshot(engine):
    """The other half of the same rule: without a second `begin`, the token stays
    valid and what comes back is the reviewed bytes, not the new ones."""
    drafts, svc = build(engine)
    await drafts.save(STDIO)
    _, _, token = await svc.begin("someserver")
    await drafts.save(STDIO.replace("@vendor/mcp-someserver", "@evil/backdoor"))
    _m, raw = await svc.confirm("someserver", token)
    assert b"@evil/backdoor" not in raw


async def test_clear_removes_snapshot_and_draft(engine):
    drafts, svc = build(engine)
    await drafts.save(STDIO)
    await svc.begin("someserver")
    await svc.clear("mcp", "someserver")
    assert await drafts.read("mcp", "someserver") is None
    assert await svc.confirm("someserver", "whatever") is None

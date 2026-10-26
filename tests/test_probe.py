import asyncio

import claude_hermes.modules.extensions.infrastructure.mcp_prober as probe_mod
from claude_hermes.modules.extensions.infrastructure.mcp_prober import probe

SPEC = {"type": "stdio", "command": "npx", "args": ["-y", "pkg"], "env": {}}


class FakeClient:
    """Stands in for ClaudeSDKClient: an async context manager with get_mcp_status()."""

    def __init__(self, options=None, status=None, boom=None):
        self.options = options
        self._status = status
        self._boom = boom

    async def __aenter__(self):
        if self._boom:
            raise self._boom
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_mcp_status(self):
        return self._status


async def test_probe_reports_a_connected_server(tmp_path, monkeypatch):
    status = {"mcpServers": [
        {"name": "hermes", "status": "connected", "tools": [{"name": "memory_save"}]},
        {"name": "example", "status": "connected",
         "tools": [{"name": "search"}, {"name": "create"}]},
    ]}
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, status=status))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "connected"
    assert got.tools == ["search", "create"]     # only this server's tools
    assert got.error == ""


async def test_probe_reports_a_failed_server(tmp_path, monkeypatch):
    status = {"mcpServers": [
        {"name": "example", "status": "failed", "error": "connection refused"},
    ]}
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, status=status))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "failed" and "connection refused" in got.error


async def test_probe_reports_a_server_the_cli_never_mentioned(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, status={"mcpServers": []}))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "failed" and got.error


async def test_probe_turns_a_crash_into_a_result(tmp_path, monkeypatch):
    """A probe must never raise into the turn."""
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, boom=RuntimeError("cli gone")))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "failed" and "cli gone" in got.error


async def test_probe_times_out(tmp_path, monkeypatch):
    class Slow(FakeClient):
        async def __aenter__(self):
            await asyncio.sleep(5)
            return self

    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", lambda options: Slow(options))

    got = await probe(tmp_path, "example", SPEC, timeout=0.01)
    assert got.status == "failed" and "timed out" in got.error


class PendingThenConnected(FakeClient):
    """The CLI reports 'pending' until the server finishes its handshake.

    Measured against the real thing: fatsecret read 'pending' at 0.5s and
    'connected' with 4 tools at 1.5s. A probe that samples once sees only the
    first of those.
    """

    def __init__(self, options=None, pending_reads=2):
        super().__init__(options)
        self.remaining = pending_reads
        self.reads = 0

    async def get_mcp_status(self):
        self.reads += 1
        if self.remaining > 0:
            self.remaining -= 1
            return {"mcpServers": [{"name": "example", "status": "pending",
                                    "tools": []}]}
        return {"mcpServers": [{"name": "example", "status": "connected",
                                "tools": [{"name": "search"}, {"name": "create"}]}]}


async def test_probe_waits_out_a_pending_handshake(tmp_path, monkeypatch):
    """'pending' means "not finished", not "failed".

    A stdio server launched through uvx has to clone, resolve and start before it
    can answer — about a second even with a warm cache. Treating the first sample
    as final reports a perfectly good server as broken and installs nothing.
    """
    client = PendingThenConnected(pending_reads=2)
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", lambda options: client)

    got = await probe(tmp_path, "example", SPEC, timeout=5)
    assert got.status == "connected"
    assert got.tools == ["search", "create"]
    assert client.reads >= 3, "probe must re-read status, not sample once"


async def test_probe_reports_the_last_state_when_pending_never_settles(tmp_path,
                                                                      monkeypatch):
    """A server wedged in 'pending' must say so, not just 'timed out' — the
    distinction is what tells you it launched but never completed the handshake."""
    class NeverSettles(FakeClient):
        async def get_mcp_status(self):
            return {"mcpServers": [{"name": "example", "status": "pending"}]}

    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", lambda options: NeverSettles())

    got = await probe(tmp_path, "example", SPEC, timeout=0.3)
    assert got.status == "failed"
    assert "pending" in got.error


async def test_probe_does_not_poll_a_settled_failure(tmp_path, monkeypatch):
    """A server that reports 'failed' is done. Re-reading it just delays the answer."""
    class CountingFailure(FakeClient):
        def __init__(self):
            super().__init__(None)
            self.reads = 0

        async def get_mcp_status(self):
            self.reads += 1
            return {"mcpServers": [{"name": "example", "status": "failed",
                                    "error": "boom"}]}

    client = CountingFailure()
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", lambda options: client)

    got = await probe(tmp_path, "example", SPEC, timeout=5)
    assert got.status == "failed" and "boom" in got.error
    assert client.reads == 1


async def test_probe_asks_for_no_tools_and_no_turns(tmp_path, monkeypatch):
    """The probe is a handshake, not a conversation: it must cost nothing."""
    seen = {}

    def capture(options):
        seen["options"] = options
        return FakeClient(options, status={"mcpServers": [
            {"name": "example", "status": "connected", "tools": []}]})

    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", capture)
    await probe(tmp_path, "example", SPEC)

    assert seen["options"].tools == []
    assert seen["options"].allowed_tools == []
    assert seen["options"].mcp_servers == {"example": SPEC}

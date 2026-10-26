"""The webhook route: who it accepts, what it drops, and how fast it answers.

Off by default is still the property that matters most — a deployment that never
sets `telegram_mode` must not grow a public POST endpoint. Since Wave 4a the route
is live, which adds three questions a live one has to answer: the secret header is
required (the path segment is in every proxy's access log), a redelivered
`update_id` runs once, and a slow update does not hold the response open, because
Telegram retries whatever it did not get a 2xx for in time.

The module `router` is mounted on a bare FastAPI app here, the way the shell mounts
it, with the bundle its three providers read stored on `app.state.deps`. The
mounting decision itself (`webhook_enabled`) is the shell's, and the first two
tests drive it the way `build_web_app` does.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from claude_hermes.core.config import Config
from claude_hermes.modules.telegram.domain.dedup import MemoryUpdateDedup
from claude_hermes.modules.telegram.presentation.webhook import (
    SECRET_HEADER,
    router,
    webhook_enabled,
)

UPDATE = {"update_id": 1}
SECRET = "s3cret"


class FakeGateway:
    def __init__(self, fail=False, gate: asyncio.Event | None = None):
        self.updates = []
        self._fail = fail
        self._gate = gate

    async def handle_update(self, payload):
        if self._gate is not None:
            await self._gate.wait()
        self.updates.append(payload)
        if self._fail:
            raise RuntimeError("bad update")


def deps(mode="webhook", secret=SECRET, gateway=None, dedup=None):
    """A bundle with its OWN dedup by default.

    The route's fallback is process-wide (it has to be, or it would remember
    nothing), so a shared one here would let `update_id=1` in one test drop
    `update_id=1` in the next. The test that exercises the fallback deletes this
    attribute and uses an id nothing else sends.
    """
    return SimpleNamespace(
        cfg=Config(telegram_mode=mode, telegram_webhook_secret=secret),
        telegram=gateway or FakeGateway(),
        update_dedup=dedup or MemoryUpdateDedup(),
    )


def mount(d) -> FastAPI:
    """What the shell does: mount the router iff `webhook_enabled(cfg)`, over a
    bundle the providers read off `app.state.deps`."""
    app = FastAPI()
    app.state.deps = d
    if webhook_enabled(d.cfg):
        app.include_router(router)
    return app


def client(d):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mount(d)),
                             base_url="https://hermes.test")


def headers(secret=SECRET):
    return {SECRET_HEADER: secret}


async def settle():
    """Let the background dispatch task run. The route answers before it starts."""
    for _ in range(10):
        await asyncio.sleep(0)


def test_polling_mode_mounts_no_route():
    assert webhook_enabled(deps(mode="polling").cfg) is False
    assert [r.path for r in mount(deps(mode="polling")).routes
            if r.path.startswith("/telegram")] == []


def test_webhook_mode_without_a_secret_refuses_to_start():
    # Loud at boot rather than a public endpoint anyone can POST to.
    with pytest.raises(RuntimeError):
        mount(deps(secret=""))


async def test_the_path_and_the_header_together_feed_the_gateway():
    gw = FakeGateway()
    async with client(deps(gateway=gw)) as c:
        r = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE, headers=headers())
    assert r.status_code == 200 and r.json() == {"ok": True}
    await settle()
    assert gw.updates == [UPDATE]


async def test_a_wrong_secret_is_rejected_and_feeds_nothing():
    gw = FakeGateway()
    async with client(deps(gateway=gw)) as c:
        r = await c.post("/telegram/webhook/guess", json=UPDATE, headers=headers())
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    await settle()
    assert gw.updates == []


async def test_a_missing_secret_header_is_rejected():
    """The path secret alone is not enough: every reverse proxy in front of this
    writes the request path into its access log, so a log reader would hold a
    complete forgery of Telegram. `set_webhook(secret_token=)` makes the header
    arrive on every genuine delivery, and it reaches no log."""
    gw = FakeGateway()
    async with client(deps(gateway=gw)) as c:
        r = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    await settle()
    assert gw.updates == []


async def test_a_wrong_secret_header_is_rejected():
    gw = FakeGateway()
    async with client(deps(gateway=gw)) as c:
        r = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE,
                         headers=headers("nope"))
    assert r.status_code == 401
    await settle()
    assert gw.updates == []


async def test_a_non_ascii_header_is_rejected_rather_than_raising():
    """`hmac.compare_digest` raises TypeError on a non-ASCII `str`, and the header
    is whatever the caller typed — Starlette hands it over latin-1 decoded, so a
    byte above 0x7f must be a 401, not a 500. Sent as raw bytes because httpx
    refuses to encode a non-ASCII header value itself."""
    gw = FakeGateway()
    async with client(deps(gateway=gw)) as c:
        r = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE,
                         headers={SECRET_HEADER.encode(): b"s\xe9cret"})
    assert r.status_code == 401
    await settle()
    assert gw.updates == []


async def test_a_non_json_body_is_a_400():
    async with client(deps()) as c:
        r = await c.post(f"/telegram/webhook/{SECRET}", content=b"not json",
                         headers={"content-type": "application/json", **headers()})
    assert r.status_code == 400


async def test_a_failing_update_still_answers_200():
    """Telegram retries a non-2xx forever, so an update we cannot handle must not
    become an infinite redelivery loop. Since the dispatch is a background task the
    200 was already out — the failure is a log line and nothing else."""
    gw = FakeGateway(fail=True)
    async with client(deps(gateway=gw)) as c:
        r = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE, headers=headers())
    assert r.status_code == 200
    await settle()
    assert gw.updates == [UPDATE]


async def test_a_repeated_update_id_is_fed_once():
    """The retry Telegram sends when the first delivery timed out. Running it twice
    is a second intake, a second STT call and a second turn against the same
    message — the duplicate the user sees as the bot answering twice."""
    gw = FakeGateway()
    async with client(deps(gateway=gw, dedup=MemoryUpdateDedup())) as c:
        first = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE,
                             headers=headers())
        await settle()
        second = await c.post(f"/telegram/webhook/{SECRET}", json=UPDATE,
                              headers=headers())
    assert first.status_code == 200 and second.status_code == 200
    assert second.json() == {"ok": True}     # a drop looks exactly like an accept
    await settle()
    assert gw.updates == [UPDATE]


async def test_a_different_update_id_is_not_dropped():
    gw = FakeGateway()
    async with client(deps(gateway=gw, dedup=MemoryUpdateDedup())) as c:
        for update_id in (1, 2):
            await c.post(f"/telegram/webhook/{SECRET}", json={"update_id": update_id},
                         headers=headers())
            await settle()
    assert gw.updates == [{"update_id": 1}, {"update_id": 2}]


async def test_a_bundle_without_a_dedup_still_dedups_in_process():
    """`update_dedup` is optional (`provide(..., required=False)`), and the absence
    of a shared one must not mean no dedup at all: the retry that matters most
    lands on the process still busy with the first delivery."""
    gw = FakeGateway()
    d = deps(gateway=gw)
    del d.update_dedup
    update = {"update_id": 987654321}
    async with client(d) as c:
        await c.post(f"/telegram/webhook/{SECRET}", json=update, headers=headers())
        await settle()
        await c.post(f"/telegram/webhook/{SECRET}", json=update, headers=headers())
    await settle()
    assert gw.updates == [update]


async def test_a_slow_update_does_not_delay_the_200():
    """The reason the dispatch is a background task at all: the handler downloads
    the document and transcribes the voice note inline, and Telegram's retry timer
    is shorter than that. The response must not wait for any of it."""
    gate = asyncio.Event()
    gw = FakeGateway(gate=gate)
    async with client(deps(gateway=gw, dedup=MemoryUpdateDedup())) as c:
        r = await asyncio.wait_for(
            c.post(f"/telegram/webhook/{SECRET}", json=UPDATE, headers=headers()),
            timeout=2)
        assert r.status_code == 200
        assert gw.updates == []              # still parked inside handle_update
        gate.set()
        await settle()
    assert gw.updates == [UPDATE]

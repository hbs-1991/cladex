"""The webhook route as the web app mounts it: the providers, and the two secrets.

`tests/modules/telegram/test_telegram_webhook.py` drives the router on a bare FastAPI
app, the two credentials and the dedup in isolation; this one drives it inside the
real shell, where the gateway and the dedup arrive through `Depends` and can
therefore be swapped for one request without a bot, a token or a running dispatcher.

The app is built in webhook mode on purpose: that is the only deployment where the
route is meant to exist, and asking for it here keeps the test honest once the shell
stops mounting the router in polling mode (`webhook.webhook_enabled`).
"""
import asyncio

import httpx
import pytest

from claude_hermes.core.config import Config
from claude_hermes.modules.telegram.domain.dedup import MemoryUpdateDedup
from claude_hermes.modules.telegram.presentation.dependencies import (
    get_gateway,
    get_update_dedup,
)
from claude_hermes.modules.telegram.presentation.webhook import (
    SECRET_HEADER,
    webhook_enabled,
)
from claude_hermes.http.app import build_web_app

from tests.web_helpers import ORIGIN, make_client

UPDATE = {"update_id": 7, "message": {"text": "hi"}}
SECRET = "s3cret"


class FakeGateway:
    def __init__(self):
        self.updates = []

    async def handle_update(self, payload):
        self.updates.append(payload)


async def settle():
    """The route answers before the dispatch task starts; let it run."""
    for _ in range(10):
        await asyncio.sleep(0)


async def webhook_client(tmp_path, gateway, *, secret=SECRET, dedup=None):
    _c, deps = await make_client(
        tmp_path, cfg=Config(telegram_mode="webhook", telegram_webhook_secret=secret))
    app = build_web_app(deps)
    app.dependency_overrides[get_gateway] = lambda: gateway
    # Its own dedup, never the route's process-wide fallback: a shared one would
    # let an `update_id` sent by another test file drop this one's update.
    dedup = dedup or MemoryUpdateDedup()
    app.dependency_overrides[get_update_dedup] = lambda: dedup
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


def headers(secret=SECRET):
    return {SECRET_HEADER: secret}


async def test_a_matching_secret_feeds_the_gateway_the_provider_returned(tmp_path):
    gw = FakeGateway()
    client = await webhook_client(tmp_path, gw)
    r = await client.post(f"/telegram/webhook/{SECRET}", json=UPDATE, headers=headers())
    assert r.status_code == 200 and r.json() == {"ok": True}
    await settle()
    assert gw.updates == [UPDATE]


async def test_a_wrong_secret_is_rejected_and_feeds_nothing(tmp_path):
    """No session cookie is involved — the two secrets are the whole credential, so
    the refusal must not depend on the auth middleware either."""
    gw = FakeGateway()
    client = await webhook_client(tmp_path, gw)
    r = await client.post("/telegram/webhook/guess", json=UPDATE, headers=headers())
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    await settle()
    assert gw.updates == []


async def test_the_secret_header_is_required_through_the_shell_too(tmp_path):
    """The path is in every proxy access log; the header is not. Both are checked,
    and the mounted router must not be the lenient door."""
    gw = FakeGateway()
    client = await webhook_client(tmp_path, gw)
    r = await client.post(f"/telegram/webhook/{SECRET}", json=UPDATE)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    await settle()
    assert gw.updates == []


async def test_the_dedup_the_provider_returns_is_the_one_that_decides(tmp_path):
    """`get_update_dedup` is what makes the drop cross-instance in redis mode, so
    the route must ask the bundle rather than always using its own fallback."""
    gw = FakeGateway()
    dedup = MemoryUpdateDedup()
    client = await webhook_client(tmp_path, gw, dedup=dedup)
    await client.post(f"/telegram/webhook/{SECRET}", json=UPDATE, headers=headers())
    await settle()
    r = await client.post(f"/telegram/webhook/{SECRET}", json=UPDATE, headers=headers())
    assert r.status_code == 200
    await settle()
    assert gw.updates == [UPDATE]
    assert await dedup.seen(UPDATE["update_id"]) is True


async def test_healthz_answers_next_to_the_webhook(tmp_path):
    """Both are outside `/api/`, and a webhook deployment is exactly where a probe
    that needs no cookie earns its keep."""
    client = await webhook_client(tmp_path, FakeGateway())
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_the_shell_is_told_when_to_mount_and_when_to_refuse():
    """`webhook_enabled` is what the mounting decision reads. Polling mode is a plain
    no; webhook mode without a secret is a refusal to boot, not a public endpoint."""
    assert webhook_enabled(Config()) is False
    assert webhook_enabled(Config(telegram_mode="webhook",
                                  telegram_webhook_secret="s3cret")) is True
    with pytest.raises(RuntimeError):
        webhook_enabled(Config(telegram_mode="webhook", telegram_webhook_secret=""))

"""`GET /api/models` and the `/model` guard on `POST …/messages`, on a bare app.

Mounted on a plain `FastAPI()` with the shell's error handler and a two-line
stand-in for the auth middleware: what is pinned here is this router's wire, not
the composition — mounting these routes in `http/app.py` is the barrier's job,
and `tests/test_web_app.py` owns the frozen path list.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from claude_hermes.core.config import Config
from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.turns.presentation import models_router, router as turns_router
from claude_hermes.modules.turns.presentation.dependencies import (
    get_auto_title,
    get_cfg,
    get_files,
    get_owned,
    get_turns,
)

THREAD = {"session_key": "web:1:x", "title": "t", "owner_user_id": 1}


class FakeLauncher:
    """The three methods a route may call. `start` records so a refused text can
    be shown never to have reached the queue."""

    def __init__(self):
        self.started: list[tuple[str, str]] = []

    async def is_busy(self, key):
        return False

    async def cancel(self, key, turn_id=None):
        return True

    async def start(self, session_key, text, *, user_id=0, attachments=None,
                    channel="web"):
        self.started.append((session_key, text))
        return "turn-1"


def make_app(cfg: Config, launcher: FakeLauncher | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(models_router.router)
    app.include_router(turns_router.router)

    async def _app_error(request: Request, exc: ApplicationException):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status)

    app.add_exception_handler(ApplicationException, _app_error)

    @app.middleware("http")
    async def _user(request: Request, call_next):
        # What `CurrentUserMiddleware` leaves behind; `current_user` reads it.
        request.state.user_id, request.state.is_owner = 1, True
        return await call_next(request)

    app.dependency_overrides[get_cfg] = lambda: cfg
    app.dependency_overrides[get_owned] = lambda: (
        lambda request, thread_id: _thread())
    app.dependency_overrides[get_turns] = lambda: (launcher or FakeLauncher())
    app.dependency_overrides[get_files] = lambda: None
    app.dependency_overrides[get_auto_title] = lambda: (lambda key, text: None)
    return app


async def _thread():
    return THREAD


def client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://t")


# --- GET /api/models --------------------------------------------------------


async def test_the_offer_is_the_configured_list_with_what_each_model_is():
    cfg = Config(model="claude-sonnet-5", models=["claude-sonnet-5", "claude-opus-5-5"])
    async with client(make_app(cfg)) as c:
        r = await c.get("/api/models")

    assert r.status_code == 200
    body = r.json()
    assert [m["id"] for m in body["models"]] == ["claude-sonnet-5", "claude-opus-5-5"]
    first = body["models"][0]
    assert set(first) == {"id", "label", "window", "price", "cache_write", "efforts",
                          "default_effort"}
    assert first["label"] == "Sonnet 5" and first["window"] == 1_000_000
    assert set(first["price"]) == {"in", "out"} and first["price"]["in"] > 0
    assert "high" in first["efforts"]
    # No effort on the deployment's own default: the CLI picks the model's, and a
    # lane that never chose must keep getting whatever the CLI picks.
    assert body["deploy_default"] == {"model": "claude-sonnet-5", "effort": None}


async def test_the_deploy_default_is_always_offered_even_when_models_omits_it():
    cfg = Config(model="claude-opus-5", models=["claude-sonnet-5"])
    async with client(make_app(cfg)) as c:
        body = (await c.get("/api/models")).json()
    assert [m["id"] for m in body["models"]] == ["claude-opus-5", "claude-sonnet-5"]


async def test_a_model_the_catalog_does_not_know_is_offered_without_facts():
    """An id nobody has proved still resolves — with no window, so the picker
    never disables it, and no prices to quote."""
    cfg = Config(model="claude-sonnet-5", models=["claude-sonnet-5", "claude-zzz-9"])
    async with client(make_app(cfg)) as c:
        body = (await c.get("/api/models")).json()
    unknown = body["models"][-1]
    assert unknown["id"] == "claude-zzz-9" and unknown["window"] == 0
    assert unknown["efforts"] == [] and unknown["default_effort"] is None


# --- the `/model` guard on POST …/messages ----------------------------------


@pytest.mark.parametrize("text", ["/model", "/model claude-opus-5", " /effort high",
                                  "/model@hermes_bot"])
async def test_a_model_command_is_refused_before_it_becomes_a_turn(text):
    launcher = FakeLauncher()
    async with client(make_app(Config(), launcher)) as c:
        r = await c.post("/api/threads/web:1:x/messages", json={"text": text})

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "model_command"
    assert launcher.started == []


async def test_models_is_an_ordinary_message():
    """The guard is anchored on a word boundary: `/models` is not `/model`."""
    launcher = FakeLauncher()
    async with client(make_app(Config(), launcher)) as c:
        r = await c.post("/api/threads/web:1:x/messages", json={"text": "/models"})

    assert r.status_code == 202
    assert launcher.started == [("web:1:x", "/models")]

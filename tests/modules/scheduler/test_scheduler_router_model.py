"""A routine's (model, effort) over the Routines router.

The router alone on a bare app — no bundle, no middleware, no other nine routers —
because what is pinned here is this router's own contract: which codes a refused
pair comes back with, and that a validated pair reaches the service whole. The
owner gate and the swappable service are pinned in `test_scheduler_router.py`.

`cron_manage` takes no model (its schema is prompt-cache surface), so this is the
only channel that sets one, which is why the validation lives in the router and the
executor's `resolve` is only the second line of defence.
"""
import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import JSONResponse

from claude_hermes.core.config import Config
from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.scheduler.presentation.dependencies import (
    get_cfg,
    get_scheduler,
)
from claude_hermes.modules.scheduler.presentation.router import router

JOB = {"id": 7, "name": "Daily brief", "kind": "interval", "value": "3600",
       "prompt": "summarize my day", "state": "active",
       "next_run_at": "2026-09-23T10:00:00+00:00", "last_run_at": None,
       "last_status": None, "timeout_seconds": None, "created_at": "2026-09-23",
       "deliver_chat_id": 1, "deliver_thread_id": None,
       "origin_session_key": "tg:private:1", "model": None, "effort": None}


class FakeScheduler:
    """A `SchedulerService` by shape only: it records what the router handed it."""

    def __init__(self):
        self.created: list[dict] = []
        self.edits: list[tuple[int, dict]] = []

    async def create_or_raise(self, **kw):
        self.created.append(kw)
        return dict(JOB, model=kw.get("model"), effort=kw.get("effort"),
                    prompt=kw["prompt"])

    async def require(self, job_id, owner_id=None):
        return dict(JOB, id=job_id)

    async def edit(self, job_id, body, now=None, owner_id=None):
        self.edits.append((job_id, body))
        return dict(JOB, id=job_id, **{k: v for k, v in body.items() if k in JOB})


def app_for(scheduler, cfg=None) -> FastAPI:
    """The Routines router behind the two things it reads off a request: who is
    asking (what `CurrentUserMiddleware` leaves on `request.state`) and the error
    shape the app shell renders an `ApplicationException` as."""
    app = FastAPI()

    @app.middleware("http")
    async def _user(request, call_next):
        request.state.user_id = 1
        request.state.is_owner = True
        return await call_next(request)

    async def _application_error(request, exc: ApplicationException):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status)

    app.add_exception_handler(ApplicationException, _application_error)
    app.include_router(router)
    app.dependency_overrides[get_scheduler] = lambda: scheduler
    app.dependency_overrides[get_cfg] = lambda: cfg or Config()
    return app


@pytest.fixture
def client_and_scheduler():
    scheduler = FakeScheduler()
    transport = httpx.ASGITransport(app=app_for(scheduler))
    return httpx.AsyncClient(transport=transport, base_url="http://t"), scheduler


async def test_create_carries_the_pair_to_the_service_and_back_out(
        client_and_scheduler):
    client, scheduler = client_and_scheduler
    r = await client.post("/api/routines", json={
        "name": "Daily brief", "schedule": "every 1h", "prompt": "summarize my day",
        "model": "claude-opus-5-5", "effort": "high"})

    assert r.status_code == 201, r.text
    assert (r.json()["model"], r.json()["effort"]) == ("claude-opus-5-5", "high")
    assert scheduler.created[0]["model"] == "claude-opus-5-5"
    assert scheduler.created[0]["effort"] == "high"


async def test_a_routine_with_no_pair_still_carries_the_keys(client_and_scheduler):
    """The wire addition is additive and always present: `null` is how a routine
    says "the deploy pair", and the form's selects read it."""
    client, scheduler = client_and_scheduler
    r = await client.post("/api/routines", json={
        "name": "x", "schedule": "every 1h", "prompt": "p"})

    assert r.json()["model"] is None and r.json()["effort"] is None
    assert scheduler.created[0]["model"] is None


async def test_a_model_that_is_not_offered_is_bad_model(client_and_scheduler):
    client, scheduler = client_and_scheduler
    r = await client.post("/api/routines", json={
        "name": "x", "schedule": "every 1h", "prompt": "p", "model": "gpt-9"})

    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_model"
    assert scheduler.created == []


async def test_an_effort_the_model_does_not_take_is_bad_effort(client_and_scheduler):
    client, scheduler = client_and_scheduler
    r = await client.post("/api/routines", json={
        "name": "x", "schedule": "every 1h", "prompt": "p",
        "model": "claude-sonnet-5", "effort": "tremendous"})

    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_effort"
    assert scheduler.created == []


async def test_the_offer_is_the_deploys_own_list(client_and_scheduler):
    """A model the catalog knows but this deployment does not offer is refused, and
    `cfg.model` is always offered even when `models:` forgets it."""
    scheduler = FakeScheduler()
    cfg = Config()
    cfg.model, cfg.models = "claude-opus-5", ["claude-sonnet-5"]
    transport = httpx.ASGITransport(app=app_for(scheduler, cfg))
    client = httpx.AsyncClient(transport=transport, base_url="http://t")

    body = {"name": "x", "schedule": "every 1h", "prompt": "p"}
    assert (await client.post("/api/routines",
                              json={**body, "model": "claude-opus-5"})).status_code == 201
    r = await client.post("/api/routines", json={**body, "model": "claude-fable-5-1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_model"


async def test_an_effort_without_a_model_is_stored_as_neither(client_and_scheduler):
    """The pair travels whole: `resolve` drops an effort that has no model, so one
    is never written either."""
    client, scheduler = client_and_scheduler
    await client.post("/api/routines", json={
        "name": "x", "schedule": "every 1h", "prompt": "p", "effort": "high"})

    assert scheduler.created[0]["effort"] is None


async def test_a_model_command_prompt_is_bad_prompt(client_and_scheduler):
    """AC #15: `/model` and `/effort` are the CLI's own local commands in
    non-interactive mode, so such a prompt is refused where the form can say so."""
    client, scheduler = client_and_scheduler
    for prompt in ("/model", "/model claude-opus-5", "  /effort high", "/model@bot"):
        r = await client.post("/api/routines", json={
            "name": "x", "schedule": "every 1h", "prompt": prompt})
        assert r.status_code == 400, prompt
        assert r.json()["error"]["code"] == "bad_prompt"
    assert scheduler.created == []

    r = await client.post("/api/routines", json={
        "name": "x", "schedule": "every 1h", "prompt": "/models, list them"})
    assert r.status_code == 201        # `/models` is a prompt, not a command


async def test_a_patch_sends_the_pair_whole(client_and_scheduler):
    client, scheduler = client_and_scheduler
    r = await client.patch("/api/routines/7",
                           json={"model": "claude-sonnet-5", "effort": "low"})

    assert r.status_code == 200
    assert scheduler.edits == [(7, {"model": "claude-sonnet-5", "effort": "low"})]
    assert (r.json()["model"], r.json()["effort"]) == ("claude-sonnet-5", "low")


async def test_a_patch_that_clears_the_model_clears_the_effort(client_and_scheduler):
    """`model: null` is the form's "back to the deploy pair", and an effort left
    behind would be one nothing applies."""
    client, scheduler = client_and_scheduler
    await client.patch("/api/routines/7", json={"model": None, "effort": "high"})

    assert scheduler.edits == [(7, {"model": None, "effort": None})]


async def test_a_patch_stays_partial_and_still_refuses_a_bad_pair(
        client_and_scheduler):
    client, scheduler = client_and_scheduler
    r = await client.patch("/api/routines/7", json={"name": "Brief"})
    assert r.status_code == 200 and scheduler.edits == [(7, {"name": "Brief"})]

    r = await client.patch("/api/routines/7", json={"model": "gpt-9"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_model"

    r = await client.patch("/api/routines/7", json={"prompt": "/model opus"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_prompt"
    assert len(scheduler.edits) == 1

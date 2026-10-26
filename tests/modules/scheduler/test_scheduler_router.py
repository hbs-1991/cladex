"""The Routines router's seams: the service it asks for, and the owner gate.

`routes(deps)` read `deps.scheduler` out of the closure; `get_scheduler` is a named
callable, so one app can be pointed at another service without rebuilding the bundle.
The second test is the half an override must not reach — `require_owner` is its own
dependency — and the third pins the leniency the PATCH model had to preserve.
"""
import httpx

from claude_hermes.modules.scheduler.presentation.dependencies import get_scheduler
from claude_hermes.http.app import build_web_app

from tests.web_helpers import ORIGIN, json_headers, login, make_client

JOB = {"id": 7, "name": "Daily brief", "kind": "interval", "value": "3600",
       "prompt": "summarize my day", "state": "active",
       "next_run_at": "2026-09-15T10:00:00+00:00", "last_run_at": None,
       "last_status": None, "timeout_seconds": None, "created_at": "2026-09-15",
       "deliver_chat_id": 1, "deliver_thread_id": None,
       "origin_session_key": "tg:private:1"}


class FakeScheduler:
    """A `SchedulerService` by shape only — no repository, no clock, no ticker."""

    def __init__(self):
        self.edits = []
        self.state = []

    # Every read and the edit take the asking user's id since Wave 4a (jobs are
    # owner-scoped); the fake records nothing about it, which keeps the assertions
    # below about routing and the owner gate, as they were.
    async def list_jobs(self, owner_id=None):
        return [JOB]

    async def require(self, job_id, owner_id=None):
        return dict(JOB, id=job_id)

    async def edit(self, job_id, body, now=None, owner_id=None):
        self.edits.append((job_id, body))
        return dict(JOB, id=job_id)

    async def set_state(self, job_id, state):
        self.state.append((job_id, state))


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def test_the_scheduler_is_swappable_without_rebuilding_the_bundle(tmp_path):
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeScheduler()
    app.dependency_overrides[get_scheduler] = lambda: fake
    client = await client_for(app)
    login(client, 1)

    r = await client.get("/api/routines")
    assert r.status_code == 200
    assert [j["id"] for j in r.json()["routines"]] == [7]
    # `{id:int}` still casts, and the handler's parameter is a real int.
    assert (await client.post("/api/routines/9/pause",
                              headers=json_headers())).status_code == 204
    assert fake.state == [(9, "paused")]
    # A non-numeric id never reaches a handler: the app's /api/* catch-all answers.
    assert (await client.get("/api/routines/abc")).status_code == 404


async def test_overriding_the_service_does_not_open_the_owner_gate(tmp_path):
    """Reads stay open to any logged-in user; every mutation is the owner's."""
    _c, deps = await make_client(tmp_path, owner=9, allowed=[2])
    app = build_web_app(deps)
    app.dependency_overrides[get_scheduler] = FakeScheduler
    client = await client_for(app)
    login(client, 2)
    assert (await client.get("/api/routines")).status_code == 200
    for path in ("/api/routines/1/pause", "/api/routines/1/resume", "/api/routines/1/run"):
        r = await client.post(path, headers=json_headers())
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert (await client.delete("/api/routines/1",
                                headers=json_headers())).status_code == 403


async def test_a_patch_stays_partial_through_the_model(tmp_path):
    """`SchedulerService.edit` branches on `"name" in body`, so the model is dumped
    with `exclude_unset=True`: an absent key must stay absent, and an unknown one
    must not arrive."""
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeScheduler()
    app.dependency_overrides[get_scheduler] = lambda: fake
    client = await client_for(app)
    login(client, 1)

    r = await client.patch("/api/routines/7", headers=json_headers(),
                           json={"name": "Brief", "nonsense": 1})
    assert r.status_code == 200
    assert fake.edits == [(7, {"name": "Brief"})]

    await client.patch("/api/routines/7", headers=json_headers(),
                       json={"timeout_seconds": None})
    assert fake.edits[1] == (7, {"timeout_seconds": None})

"""The Routines router: the web mirror of the cron_manage tool's create path."""
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository

from tests.web_helpers import json_headers, login, make_client


async def test_create_list_pause_resume_delete(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    r = await client.post("/api/routines", headers=json_headers(), json={
        "name": "Daily brief", "schedule": "every 1h", "prompt": "summarize my day",
    })
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["name"] == "Daily brief" and job["kind"] == "interval" and job["state"] == "active"
    assert job["thread_id"] == "tg:private:1"           # no topics in tests → flat delivery lane
    rid = job["id"]
    r = await client.get("/api/routines")
    assert [j["id"] for j in r.json()["routines"]] == [rid]
    assert (await client.post(f"/api/routines/{rid}/pause", headers=json_headers())).status_code == 204
    assert (await client.get(f"/api/routines/{rid}")).json()["state"] == "paused"
    assert (await client.post(f"/api/routines/{rid}/resume", headers=json_headers())).status_code == 204
    assert (await client.get(f"/api/routines/{rid}")).json()["state"] == "active"
    r = await client.patch(f"/api/routines/{rid}", headers=json_headers(),
                           json={"name": "Brief", "timeout_seconds": 300})
    assert r.status_code == 200 and r.json()["name"] == "Brief" and r.json()["timeout_seconds"] == 300
    assert (await client.delete(f"/api/routines/{rid}", headers=json_headers())).status_code == 204
    assert (await client.get(f"/api/routines/{rid}")).status_code == 404


async def test_validation_matches_the_tool(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    r = await client.post("/api/routines", headers=json_headers(), json={
        "name": "x", "schedule": "whenever", "prompt": "p"})
    assert r.status_code == 400 and "schedule" in r.json()["error"]["message"].lower()
    r = await client.post("/api/routines", headers=json_headers(), json={
        "name": "x", "schedule": "every 1h", "prompt": "rm -rf / please"})
    assert r.status_code == 400
    r = await client.post("/api/routines", headers=json_headers(), json={
        "name": "x", "schedule": "every 1h", "prompt": "p", "timeout_seconds": 5})
    assert r.status_code == 400 and "timeout" in r.json()["error"]["message"].lower()


async def test_run_now_schedules_the_next_tick(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    login(client, 1)
    rid = (await client.post("/api/routines", headers=json_headers(), json={
        "name": "x", "schedule": "every 1h", "prompt": "p"})).json()["id"]
    r = await client.post(f"/api/routines/{rid}/run", headers=json_headers())
    assert r.status_code == 204
    job = await JobRepository(engine).get(rid)
    from datetime import datetime, timezone
    assert datetime.fromisoformat(job["next_run_at"]) <= datetime.now(timezone.utc)


async def test_non_owner_can_read_but_not_mutate(tmp_path):
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 2)
    assert (await client.get("/api/routines")).status_code == 200
    r = await client.post("/api/routines", headers=json_headers(), json={
        "name": "x", "schedule": "every 1h", "prompt": "p"})
    assert r.status_code == 403


async def test_runs_history(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    login(client, 1)
    rid = (await client.post("/api/routines", headers=json_headers(), json={
        "name": "x", "schedule": "every 1h", "prompt": "p"})).json()["id"]
    await JobRepository(engine).record_run(rid, "2026-07-12T10:00:00+00:00", "ok")
    r = await client.get(f"/api/routines/{rid}/runs")
    assert r.json()["runs"] == [{"ts": "2026-07-12T10:00:00+00:00", "status": "ok"}]


async def test_create_opens_the_delivery_thread(tmp_path):
    """The Routine page mounts the runtime on the job's delivery lane the moment
    the job exists — before the ticker has ever run it — so the lane needs its
    `threads` row from the start, or /events and /messages answer 404."""
    client, deps = await make_client(tmp_path)
    login(client, 1)
    job = (await client.post("/api/routines", headers=json_headers(), json={
        "name": "Daily brief", "schedule": "every 1h", "prompt": "summarize my day",
    })).json()
    assert (await client.get(f"/api/threads/{job['thread_id']}")).status_code == 200
    r = await client.get(f"/api/threads/{job['thread_id']}/events")
    assert r.status_code == 200 and r.json() == {"events": []}

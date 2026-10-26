"""Threads router: visibility rules, listing/paging, create, rename, archive."""
from claude_hermes.modules.sessions.domain.policies import visible
from claude_hermes.modules.sessions.infrastructure.repositories import ThreadRepository
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository

from conftest import ensure_users
from tests.web_helpers import json_headers, login, make_client

NOW = "2026-07-12T10:00:00+00:00"


def test_visibility_rules():
    web_mine = {"session_key": "web:1:a", "channel": "web", "owner_user_id": 1}
    web_other = {"session_key": "web:2:a", "channel": "web", "owner_user_id": 2}
    private_mine = {"session_key": "tg:private:1", "channel": "telegram", "owner_user_id": 1}
    group = {"session_key": "tg:supergroup:-5:1:t9", "channel": "telegram", "owner_user_id": None}
    assert visible(web_mine, 1, False) and not visible(web_other, 1, False)
    assert visible(private_mine, 1, False) and not visible(private_mine, 2, False)
    assert not visible(group, 1, False) and visible(group, 1, True)
    assert not visible(web_other, 1, True)              # even the owner sees only their own web lanes


async def test_list_create_rename_archive(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    threads = ThreadRepository(engine)
    login(client, 1)
    # "talked in" is threads.has_turns now (migration 0002), not an EXISTS over
    # turn_events: the writer that knows a turn started is the one that says so.
    await threads.upsert("tg:private:1", "telegram", 1, NOW, talked=True)
    await TurnEventRepository(engine).append("tg:private:1", "t1", 1, NOW, "turn.start", {})
    # `threads.owner_user_id` references `users(id)` since 0005, so a lane for
    # somebody else needs that somebody to exist.
    await ensure_users(engine, 2)
    await threads.upsert("web:2:zzz", "web", 2, NOW)          # not mine
    r = await client.post("/api/threads", json={}, headers=json_headers())
    assert r.status_code == 201
    tid = r.json()["id"]
    assert tid.startswith("web:1:")
    r = await client.get("/api/threads")
    ids = [t["id"] for t in r.json()["threads"]]
    assert ids == [tid, "tg:private:1"]
    assert r.json()["threads"][0] == {
        "id": tid, "title": None, "channel": "web", "archived": False, "running": False,
        "last_event_at": r.json()["threads"][0]["last_event_at"],
        "created_at": r.json()["threads"][0]["created_at"],
    }
    r = await client.patch(f"/api/threads/{tid}", json={"title": "Plan"}, headers=json_headers())
    assert r.status_code == 200 and r.json()["title"] == "Plan"
    r = await client.delete(f"/api/threads/{tid}", headers=json_headers())
    assert r.status_code == 204
    assert tid not in [t["id"] for t in (await client.get("/api/threads")).json()["threads"]]
    assert tid in [t["id"] for t in (await client.get("/api/threads?archived=1")).json()["threads"]]


async def test_foreign_thread_is_404(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    login(client, 1)
    await ensure_users(engine, 2)
    await ThreadRepository(engine).upsert("web:2:zzz", "web", 2, NOW)
    assert (await client.get("/api/threads/web:2:zzz")).status_code == 404
    r = await client.patch("/api/threads/web:2:zzz", json={"title": "x"}, headers=json_headers())
    assert r.status_code == 404


async def test_paging_uses_last_event_at_cursor(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    threads = ThreadRepository(engine)
    login(client, 1)
    for i in range(3):
        await threads.upsert(f"web:1:{i}", "web", 1, f"2026-07-12T1{i}:00:00+00:00")
    r = await client.get("/api/threads?limit=2")
    body = r.json()
    assert [t["id"] for t in body["threads"]] == ["web:1:2", "web:1:1"]
    assert body["next"] == "2026-07-12T11:00:00+00:00"
    r = await client.get(f"/api/threads?limit=2&before={body['next']}")
    assert [t["id"] for t in r.json()["threads"]] == ["web:1:0"] and r.json()["next"] is None


async def test_a_visible_lane_behind_a_page_of_invisible_ones_is_still_listed(tmp_path, engine):
    """Visibility is applied in Python, so a SQL page can be all-invisible. The
    listing must keep reading rather than answer with an empty page and no cursor."""
    client, deps = await make_client(tmp_path, owner=9, engine=engine)
    threads = ThreadRepository(engine)
    login(client, 1)
    await ensure_users(engine, 1, 2)
    await threads.upsert("web:1:mine", "web", 1, "2026-07-12T00:00:00+00:00")
    for i in range(60):                      # someone else's lanes, all newer
        await threads.upsert(f"web:2:{i}", "web", 2, f"2026-07-12T10:{i:02d}:00+00:00")
    body = (await client.get("/api/threads?limit=50")).json()
    assert [t["id"] for t in body["threads"]] == ["web:1:mine"]
    assert body["next"] is None

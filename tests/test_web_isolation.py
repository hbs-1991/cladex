"""Two users, one deploy: what B can reach of A's, through every route there is.

This is the acceptance line of Wave 4a — "two different users cannot see each
other's threads, files, drafts or workspace files, via API or via the agent's
tools" — for the API half. The workspace half is `tests/modules/turns`.

A is the deploy owner (1) and B is an allowlisted user (2), which is the harder
direction of the two: B is a legitimate, logged-in, allowlisted person, so nothing
here is refused by authentication. Every assertion below is about `owner_id`.

A refusal is a **404**, never a 403, everywhere a resource id is involved: a 403
tells a stranger that a thread, a file or a routine with that id exists. The one
403 in this file is `POST /api/drafts`, where the gate is the owner role and not a
row — there is no id to leak.
"""
import pytest

from claude_hermes.core.exceptions import NotFound
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.modules.sessions.infrastructure.repositories import MessageRepository
from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository
from claude_hermes.modules.turns.infrastructure.tool_handlers.search import (
    make_session_search,
)

from tests.web_helpers import ORIGIN, json_headers, login, make_client

NOW = "2026-07-12T10:00:00+00:00"
A, B = 1, 2


async def two_users(tmp_path, engine):
    """A logged-in as the owner, B logged-in as an allowlisted user, one app.

    Two clients over one ASGI app rather than two apps: a second app would be a
    second set of objects, and an isolation test that passes because the two
    halves never met would prove nothing. The app runs over the test's `engine`,
    so a test that reads or writes a row directly does so through the same pool.
    """
    client, deps = await make_client(tmp_path, owner=A, allowed=[B], engine=engine)
    # A second client over the SAME ASGI transport — httpx keeps one cookie jar
    # per client, and two logged-in identities is the whole point. `_transport` is
    # how the object is reached; `make_client` returns the client, not the app.
    b = type(client)(transport=client._transport, base_url=str(client.base_url))
    login(client, A)
    login(b, B)
    return client, b, deps


async def draft_owner(engine, name: str) -> int | None:
    """`catalog_drafts.owner_id` for one row — the audit column, read directly
    because no port exposes it (nothing in the app branches on it)."""
    from sqlalchemy import select

    from claude_hermes.modules.extensions.domain.models import CatalogDraft
    async with engine.connect() as conn:
        return (await conn.execute(
            select(CatalogDraft.owner_id).where(CatalogDraft.name == name)
        )).scalar_one_or_none()


async def a_thread_with_a_file(client, engine, user_id: int, tid: str | None = None):
    """A lane owned by `user_id`, with one event and one upload in it."""
    if tid is None:
        tid = (await client.post("/api/threads", json={},
                                 headers=json_headers())).json()["id"]
    await TurnEventRepository(engine).append(tid, "t1", 1, NOW, "turn.start",
                                             {"channel": "web", "user_id": user_id})
    up = await client.post("/api/uploads", data={"thread_id": tid},
                           files={"file": ("secret.txt", b"mine", "text/plain")},
                           headers={"Origin": ORIGIN})
    assert up.status_code == 201, up.text
    return tid, up.json()["file_id"]


# --- threads, events, stream -------------------------------------------------


async def test_b_cannot_see_a_thread_of_as_by_any_thread_route(tmp_path, engine):
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, _fid = await a_thread_with_a_file(client, engine, A)
    b_tid = (await b.post("/api/threads", json={}, headers=json_headers())).json()["id"]

    assert [t["id"] for t in (await b.get("/api/threads")).json()["threads"]] == [b_tid]
    for path in (f"/api/threads/{a_tid}", f"/api/threads/{a_tid}/events",
                 f"/api/threads/{a_tid}/stream"):
        r = await b.get(path)
        assert r.status_code == 404, f"{path} answered {r.status_code}"
        assert r.json()["error"]["code"] == "not_found"
    # …and A still sees their own, so the rule is scoping and not an outage.
    assert (await client.get(f"/api/threads/{a_tid}")).status_code == 200
    assert [t["id"] for t in (await client.get("/api/threads")).json()["threads"]] \
        == [a_tid]


async def test_b_cannot_post_a_turn_or_cancel_one_in_as_lane(tmp_path, engine):
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, _ = await a_thread_with_a_file(client, engine, A)
    r = await b.post(f"/api/threads/{a_tid}/messages", json={"text": "hi"},
                     headers=json_headers())
    assert r.status_code == 404
    assert (await b.post(f"/api/threads/{a_tid}/cancel",
                         headers=json_headers())).status_code == 404


async def test_b_cannot_upload_into_as_lane(tmp_path, engine):
    """`LaneGuard` refuses before the form is even looked at — a foreign lane is a
    404 whether or not the body was well formed."""
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, _ = await a_thread_with_a_file(client, engine, A)
    r = await b.post("/api/uploads", data={"thread_id": a_tid},
                     files={"file": ("x.txt", b"x", "text/plain")},
                     headers={"Origin": ORIGIN})
    assert r.status_code == 404


# --- files -------------------------------------------------------------------


async def test_b_cannot_download_as_file_and_the_row_names_its_owner(tmp_path, engine):
    client, b, deps = await two_users(tmp_path, engine)
    _a_tid, fid = await a_thread_with_a_file(client, engine, A)

    assert (await b.get(f"/api/files/{fid}")).status_code == 404
    got = await client.get(f"/api/files/{fid}")
    assert got.status_code == 200 and got.content == b"mine"
    # The row records the LANE's owner, which is what the download route
    # re-resolves the blob's mirror root against.
    assert (await deps.file_store.get(fid))["owner_id"] == A


async def test_an_attachment_of_another_owner_is_refused_by_the_service(tmp_path, engine):
    """The second belt: even inside one lane, a row whose owner is not this lane's
    owner is not pullable into a prompt by id."""
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, fid = await a_thread_with_a_file(client, engine, A)
    from claude_hermes.modules.files.domain.exceptions import UnknownAttachment

    [att] = await deps.file_store.attachments_for(a_tid, [fid], owner_id=A)
    assert att.file_id == fid
    with pytest.raises(UnknownAttachment):
        await deps.file_store.attachments_for(a_tid, [fid], owner_id=B)


# --- routines ----------------------------------------------------------------


async def test_routines_are_scoped_to_whoever_asks(tmp_path, engine):
    """`GET /api/routines` was never owner-gated, so before `jobs.owner_id` it
    listed the whole deploy's routines to anybody who could log in."""
    client, b, deps = await two_users(tmp_path, engine)
    mine = (await client.post("/api/routines",
                              json={"name": "digest", "schedule": "30m",
                                    "prompt": "p"},
                              headers=json_headers()))
    assert mine.status_code == 201, mine.text
    a_job = mine.json()["id"]
    b_job = await JobRepository(engine).create("theirs", "interval", "60", "p", B,
                                               f"tg:private:{B}", NOW, owner_id=B)

    assert [j["id"] for j in (await b.get("/api/routines")).json()["routines"]] \
        == [b_job]
    assert [j["id"] for j in (await client.get("/api/routines")).json()["routines"]] \
        == [a_job]
    for path in (f"/api/routines/{a_job}", f"/api/routines/{a_job}/runs"):
        assert (await b.get(path)).status_code == 404
    # A mutation is owner-gated as well, so B is refused before the id is read.
    assert (await b.post(f"/api/routines/{a_job}/pause",
                         headers=json_headers())).status_code == 403


async def test_the_agent_tool_only_sees_the_lanes_own_routines(tmp_path, engine):
    """`cron_manage` is offered to every lane, so the scoping has to be the turn's
    lane owner and not the deploy."""
    from claude_hermes.modules.scheduler.presentation.tool import handle

    client, _b, deps = await two_users(tmp_path, engine)
    a_job = (await client.post("/api/routines",
                               json={"name": "digest", "schedule": "30m", "prompt": "p"},
                               headers=json_headers())).json()["id"]
    await JobRepository(engine).create("theirs", "interval", "60", "p", B,
                                       f"tg:private:{B}", NOW, owner_id=B)

    b_ctx = TurnContext(session_key=f"web:{B}:x", chat_id=B, user_id=B, owner_id=B)
    listed = await handle(deps.scheduler, {"action": "list"}, b_ctx)
    assert "theirs" in listed and "digest" not in listed
    # …and A's routine cannot be paused by guessing its id from B's lane.
    assert await handle(deps.scheduler, {"action": "pause", "job_id": a_job}, b_ctx) \
        == f"No such job #{a_job}."
    assert (await JobRepository(engine).get(a_job))["state"] == "active"


# --- drafts (deploy-wide by D8, but the proposer is recorded) ----------------


async def test_b_cannot_file_a_draft_and_as_draft_records_who_proposed_it(tmp_path, engine):
    """The queue itself stays deploy-wide on purpose: approving a draft makes
    something runnable in this container, so it is the owner's decision and a
    per-user queue would let one user hide a draft from the person approving it
    (D8). What Wave 4a adds is the audit column."""
    client, b, deps = await two_users(tmp_path, engine)
    body = {"kind": "plugin", "name": "notes", "label": "Notes",
            "description": "d", "repo": "https://github.com/x/y", "ref": "v1.0.0"}
    assert (await b.post("/api/drafts", json=body,
                         headers=json_headers())).status_code == 403
    r = await client.post("/api/drafts", json=body, headers=json_headers())
    assert r.status_code == 201, r.text
    # Deliberately still visible to B: the queue is one queue, and the column is
    # the audit trail of who filed each row.
    assert [d["name"] for d in (await b.get("/api/drafts")).json()["drafts"]] == ["notes"]
    assert await draft_owner(engine, "notes") == A


# --- skills (Wave 4b, D6) ----------------------------------------------------


async def test_b_lists_edits_and_reaches_only_bs_own_skills(tmp_path, engine):
    """The 4a leak: `GET /api/skills` served the OWNER's library to every logged-in
    user. Every skills route is the requesting user's own tree now — B's listing
    is B's, B's edits land in B's tree, and A's skill is a 404 by name from B's
    session, never a 403 that would confirm it exists."""
    client, b, deps = await two_users(tmp_path, engine)
    skill = ("---\nname: greet\ndescription: Greets people warmly.\n---\n\n"
             "Say hello.\n")
    r = await client.post("/api/skills", json={"name": "greet", "content": skill},
                          headers=json_headers())
    assert r.status_code == 201, r.text

    assert (await b.get("/api/skills")).json() == {"skills": []}
    r = await b.get("/api/skills/greet")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert (await b.put("/api/skills/greet", json={"content": skill, "etag": "x"},
                        headers=json_headers())).status_code == 404

    r = await b.post("/api/skills", json={"name": "greet", "content": skill},
                     headers=json_headers())
    assert r.status_code == 201, r.text
    etag = (await b.get("/api/skills/greet")).json()["etag"]
    mine = skill + "\nB's line.\n"
    r = await b.put("/api/skills/greet", json={"content": mine, "etag": etag},
                    headers=json_headers())
    assert r.status_code == 200 and r.json()["content"] == mine
    b_file = tmp_path / "users" / str(B) / "agent_home" / ".claude" / "skills" \
        / "greet" / "SKILL.md"
    assert b_file.read_bytes() == mine.encode()
    owner = await deps.learning.owner()
    assert (owner.skills_dir / "greet" / "SKILL.md").read_bytes() == skill.encode()
    # Each mutation evicted its OWN author's lanes, never everybody's (B-4).
    assert deps.runner.reset_owneds == [(A, True), (B, False), (B, False)]
    assert deps.runner.reset_alls == 0


async def test_a_cookie_whose_user_is_no_longer_admitted_is_a_401(tmp_path, engine):
    """Admission is re-checked per request (R-8). A valid cookie for a user who is
    disabled, or who was never admitted at all, is refused before any handler —
    which matters now that a logged-in user writes prompt text into a tree."""
    from sqlalchemy import text

    client, b, deps = await two_users(tmp_path, engine)
    assert (await b.get("/api/skills")).status_code == 200
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE users SET disabled_at = :now WHERE id = :b"),
                           {"now": NOW, "b": B})
    r = await b.get("/api/skills")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert (await b.post("/api/skills", json={"name": "x", "content": "y"},
                         headers=json_headers())).status_code == 401
    # A cookie that was never backed by an admission: signed, unexpired, refused.
    stranger = type(client)(transport=client._transport, base_url=str(client.base_url))
    login(stranger, 3)
    assert (await stranger.get("/api/skills")).status_code == 401
    # …and A is untouched: the check is per user, not an outage.
    assert (await client.get("/api/skills")).status_code == 200


# --- recall ------------------------------------------------------------------


async def test_recall_scoped_to_a_returns_nothing_of_bs(tmp_path, engine):
    """`session_search` runs inside B's turn with B's owner id, so A's transcript
    is not in the index it searches — and the scoping is in the query, so the
    `limit=8` is eight of the asker's own hits."""
    _client, _b, _deps = await two_users(tmp_path, engine)
    messages = MessageRepository(engine)
    await messages.record(f"web:{A}:x", "user", "the ansible runbook", NOW, owner_id=A)
    await messages.record(f"web:{B}:y", "user", "the ansible sandbox", NOW, owner_id=B)

    a_ctx = TurnContext(session_key=f"web:{A}:x", chat_id=A, user_id=A, owner_id=A)
    b_ctx = TurnContext(session_key=f"web:{B}:y", chat_id=B, user_id=B, owner_id=B)
    a_text = (await make_session_search(messages, a_ctx)({"query": "ansible"})
              )["content"][0]["text"]
    b_text = (await make_session_search(messages, b_ctx)({"query": "ansible"})
              )["content"][0]["text"]
    assert "runbook" in a_text and "sandbox" not in a_text
    assert "sandbox" in b_text and "runbook" not in b_text


async def test_the_lane_guard_is_the_same_object_the_files_routes_use(tmp_path, engine):
    """One rule, one implementation: a second copy of "may this user see this
    lane" is how the thread routes and the file routes drift apart."""
    client, _b, deps = await two_users(tmp_path, engine)
    a_tid, _ = await a_thread_with_a_file(client, engine, A)
    assert await deps.threads.get_visible(a_tid, A, True)
    with pytest.raises(NotFound):
        await deps.threads.get_visible(a_tid, B, False)


async def test_a_message_cannot_pull_an_attachment_stamped_for_another_owner(tmp_path, engine):
    """The belt is live on the request path: the turns router hands the thread's
    owner to `resolve_attachments`, so a row whose `owner_id` is not the lane's is
    a 400 even when its `session_key` matches (advisor risk, code pass)."""
    from sqlalchemy import text
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, fid = await a_thread_with_a_file(client, engine, A)
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE files SET owner_id = :b WHERE id = :fid"),
                           {"b": B, "fid": fid})
    r = await client.post(f"/api/threads/{a_tid}/messages",
                          json={"text": "look", "attachment_ids": [fid]},
                          headers=json_headers())
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


# --- the model a lane runs ---------------------------------------------------


async def test_b_cannot_change_the_model_of_as_lane(tmp_path, engine):
    """A foreign lane is a 404 for the model route too — and A's own still answers,
    so the rule is scoping and not an outage."""
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, _fid = await a_thread_with_a_file(client, engine, A)

    r = await b.put(f"/api/threads/{a_tid}/model", json={"model": "claude-opus-5-5"},
                    headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    r = await client.put(f"/api/threads/{a_tid}/model",
                         json={"model": "claude-opus-5-5", "effort": "high"},
                         headers=json_headers())
    assert r.status_code == 200
    assert r.json() == {"model": "claude-opus-5-5", "effort": "high",
                        "effective_model": "claude-opus-5-5", "effective_effort": "high"}
    # …and B cannot even read what A chose.
    assert (await b.get(f"/api/threads/{a_tid}")).status_code == 404
    assert (await client.get(f"/api/threads/{a_tid}")).json()["model"] == "claude-opus-5-5"


async def test_only_the_owner_changes_the_model_of_a_group_lane(tmp_path, engine):
    """A group lane is the deploy owner's (`visible`'s one belt): an allowlisted
    user who talks in the group must not move the room onto another model."""
    client, b, deps = await two_users(tmp_path, engine)
    key = "tg:supergroup:-100500"
    await deps.threads.touch(key, "telegram", A, NOW, talked=True)

    r = await b.put(f"/api/threads/{key}/model", json={"model": "claude-opus-5-5"},
                    headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = await client.put(f"/api/threads/{key}/model", json={"model": "claude-opus-5-5"},
                         headers=json_headers())
    assert r.status_code == 200


async def test_a_personal_default_is_each_users_own(tmp_path, engine):
    """`PUT /api/me/model-default` is open to every admitted user and reaches only
    their own row — the same offered list, a different preference."""
    client, b, _deps = await two_users(tmp_path, engine)

    r = await b.put("/api/me/model-default",
                    json={"model": "claude-opus-5-5", "effort": "high"},
                    headers=json_headers())
    assert r.status_code == 200
    assert r.json() == {"default_model": "claude-opus-5-5", "default_effort": "high"}

    assert (await b.get("/api/me")).json()["default_model"] == "claude-opus-5-5"
    assert (await client.get("/api/me")).json()["default_model"] is None


async def test_a_users_default_never_moves_somebody_elses_lane(tmp_path, engine):
    """B's default is theirs: A's lane resolves for A's owner, whoever is reading."""
    client, b, deps = await two_users(tmp_path, engine)
    a_tid, _fid = await a_thread_with_a_file(client, engine, A)
    await b.put("/api/me/model-default", json={"model": "claude-opus-5-5"},
                headers=json_headers())
    body = (await client.get(f"/api/threads/{a_tid}")).json()
    assert body["effective_model"] == deps.cfg.model

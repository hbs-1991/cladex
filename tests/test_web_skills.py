"""The skills router: read, create, edit under an etag, pin, archive, restore.

The etag is the interesting one — the background review pass writes these same files
after most turns, so the router must make a lost update loud instead of silent. And
there is no delete route at all (PRD P4): the last test pins that.
"""
from tests.web_helpers import json_headers, login, make_client

SKILL = (
    "---\nname: greet\ndescription: Greets people warmly.\n---\n\n"
    "## How\n\nSay hello, then ask a question.\n"
)
NEW = (
    "---\nname: farewell\ndescription: Says goodbye without dragging it out.\n---\n\n"
    "Say bye.\n"
)


async def setup(tmp_path, *, owner=1):
    """The app, plus the owner's learning scope — the `skills_dir` and `sidecar`
    every route below resolves for the owner's session (`deps.learning.owner()`)."""
    client, deps = await make_client(tmp_path, owner=owner, allowed=[2])
    owner_scope = await deps.learning.owner()
    directory = owner_scope.skills_dir / "greet"
    (directory / "references").mkdir(parents=True)
    # write_bytes, not write_text: on Windows text mode rewrites the line endings,
    # which the library never does - it writes the bytes it was handed.
    (directory / "SKILL.md").write_bytes(SKILL.encode("utf-8"))
    (directory / "references" / "x.md").write_text("abc", encoding="utf-8")
    owner_scope.sidecar.mark_created("greet", "agent", "2026-09-01T00:00:00+00:00")
    return client, deps, owner_scope


async def test_the_list_is_the_requesting_users_library(tmp_path):
    """Any allowed user may list — and what they list is their own tree, which
    for the owner is the one `setup` seeded (Wave 4b, D6)."""
    client, deps, owner = await setup(tmp_path, owner=9)
    login(client, 9)
    r = await client.get("/api/skills")
    assert r.status_code == 200
    (s,) = r.json()["skills"]
    assert s["name"] == "greet" and s["source"] == "agent" and s["state"] == "active"
    assert s["description"] == "Greets people warmly." and s["broken"] is False
    assert s["pinned"] is False and s["files"] == 2 and s["bytes"] > 0


async def test_the_detail_carries_content_an_etag_and_the_other_files(tmp_path):
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    r = await client.get("/api/skills/greet")
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == SKILL and len(body["etag"]) == 64
    assert body["tree"] == [{"path": "references/x.md", "bytes": 3}]
    assert (await client.get("/api/skills/nope")).status_code == 404
    # A traversal never reaches a handler at all — it normalises out of the route —
    # and library._resolve refuses it if one ever does (tests/test_skill_library.py).
    r = await client.get("/api/skills/..%2Fescape")
    assert r.status_code in (400, 404) and "content" not in r.text


async def test_a_save_snapshots_bumps_the_patch_count_and_resets(tmp_path):
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    etag = (await client.get("/api/skills/greet")).json()["etag"]
    body = SKILL + "\nAnd smile.\n"
    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": body, "etag": etag})
    assert r.status_code == 200
    assert r.json()["content"] == body and r.json()["etag"] != etag
    assert r.json()["patch_count"] == 1
    assert (owner.skills_dir / "greet" / "SKILL.md").read_text(encoding="utf-8") == body
    snaps = list((tmp_path / "logs" / "skill_edit_backups").iterdir())
    assert len(snaps) == 1                               # its own store, not skills_backups
    assert not (tmp_path / "logs" / "skills_backups").exists()
    assert deps.runner.reset_owneds == [(1, True)]


async def test_a_stale_etag_is_refused_with_the_current_text(tmp_path):
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    etag = (await client.get("/api/skills/greet")).json()["etag"]
    # The review pass writes the same file while the owner is typing.
    theirs = SKILL + "\nLearned: they prefer 'hi'.\n"
    (owner.skills_dir / "greet" / "SKILL.md").write_bytes(theirs.encode("utf-8"))
    # Valid content, stale etag: library.write_skill validates the body before it
    # compares, so an invalid body would answer bad_frontmatter and never get here.
    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": SKILL + "\nMine.\n", "etag": etag})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "stale_edit" and err["content"] == theirs and err["etag"]
    assert (owner.skills_dir / "greet" / "SKILL.md").read_text(encoding="utf-8") == theirs
    assert deps.runner.reset_owneds == []


async def test_a_refused_save_leaves_no_snapshot_behind(tmp_path):
    """The snapshot store keeps N and prunes the rest, so a snapshot per refusal is
    not merely wasteful: a handful of rejected retries would rotate out the one
    holding the state before the last edit that actually landed."""
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    etag = (await client.get("/api/skills/greet")).json()["etag"]
    store = tmp_path / "logs" / "skill_edit_backups"
    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": SKILL, "etag": "0" * 64})
    assert r.status_code == 409 and not store.exists()               # stale etag
    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": "no frontmatter here\n", "etag": etag})
    assert r.status_code == 400 and not store.exists()               # bad frontmatter
    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": SKILL + "x\n", "etag": etag})
    assert r.status_code == 200 and len(list(store.iterdir())) == 1   # the one that landed


async def test_create_writes_the_skill_and_records_the_owner_as_its_author(tmp_path):
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    r = await client.post("/api/skills", headers=json_headers(),
                          json={"name": "farewell", "content": NEW})
    assert r.status_code == 201
    assert r.json()["name"] == "farewell" and r.json()["source"] == "user"
    assert (owner.skills_dir / "farewell" / "SKILL.md").read_text(encoding="utf-8") == NEW
    assert owner.sidecar.get("farewell")["created_by"] == "user"
    assert deps.runner.reset_owneds == [(1, True)]


async def test_create_refuses_a_duplicate_a_bad_name_a_huge_body_and_no_description(tmp_path):
    client, deps, owner = await setup(tmp_path)
    login(client, 1)

    async def post(**body):
        return await client.post("/api/skills", headers=json_headers(), json=body)

    r = await post(name="greet", content=NEW)
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"
    r = await post(name="../x", content=NEW)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_name"
    r = await post(name="huge", content=NEW + "x" * (70 * 1024))
    assert r.status_code == 413 and r.json()["error"]["code"] == "too_large"
    r = await post(name="bare", content="no frontmatter here\n")
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_frontmatter"
    assert deps.runner.reset_owneds == []
    assert sorted(p.name for p in owner.skills_dir.iterdir() if p.is_dir()) == ["greet"]


async def test_pin_protects_from_archive_and_costs_no_reset(tmp_path):
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    r = await client.post("/api/skills/greet/pin", headers=json_headers())
    assert r.status_code == 200 and r.json()["pinned"] is True
    assert "content" not in r.json()                     # the row shape, not the detail
    assert deps.runner.reset_owneds == []                   # pinning changes no prompt text
    r = await client.post("/api/skills/greet/archive", headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "pinned"
    assert (owner.skills_dir / "greet").is_dir()

    assert (await client.post("/api/skills/greet/unpin", headers=json_headers())
            ).json()["pinned"] is False
    r = await client.post("/api/skills/greet/archive", headers=json_headers())
    assert r.status_code == 200 and r.json()["state"] == "archived"
    assert (owner.skills_dir / ".archive" / "greet" / "SKILL.md").is_file()
    assert not (owner.skills_dir / "greet").exists()
    assert deps.runner.reset_owneds == [(1, True)]
    # The page stays readable while archived, which is what makes Restore reachable.
    assert (await client.get("/api/skills/greet")).json()["content"] == SKILL

    r = await client.post("/api/skills/greet/restore", headers=json_headers())
    assert r.status_code == 200 and r.json()["state"] == "active"
    assert (owner.skills_dir / "greet" / "SKILL.md").is_file()
    assert deps.runner.reset_owneds == [(1, True), (1, True)]
    r = await client.post("/api/skills/greet/restore", headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "restore_failed"


async def test_a_skill_the_sidecar_never_saw_can_still_be_pinned(tmp_path):
    """A bundled skill is seeded onto the volume and gets no sidecar entry until it is
    used — and Pin is the first thing an owner reaches for on one."""
    client, deps, owner = await setup(tmp_path)
    (owner.skills_dir / "loose").mkdir()
    (owner.skills_dir / "loose" / "SKILL.md").write_bytes(SKILL.encode("utf-8"))
    login(client, 1)
    r = await client.post("/api/skills/loose/pin", headers=json_headers())
    assert r.status_code == 200 and r.json()["pinned"] is True
    r = await client.post("/api/skills/loose/archive", headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "pinned"


async def test_no_route_deletes_a_skill(tmp_path):
    """PRD P4: archiving is recoverable, deletion is not. The absence is the feature."""
    client, deps, owner = await setup(tmp_path)
    login(client, 1)
    r = await client.delete("/api/skills/greet", headers=json_headers())
    assert r.status_code == 405
    assert (owner.skills_dir / "greet").is_dir()


async def test_a_user_sees_and_edits_only_their_own_library(tmp_path):
    """Wave 4b, D6: the 4a leak was `GET /api/skills` serving the OWNER's library
    to every logged-in user. Now every route is the requesting user's own tree,
    and the owner's `greet` is a plain 404 from anybody else's session — so the
    scope is the authorization and no mutation needs an owner gate."""
    client, deps, owner = await setup(tmp_path, owner=9)
    login(client, 2)                                     # allowed, but not the owner
    assert (await client.get("/api/skills")).json() == {"skills": []}
    assert (await client.get("/api/skills/greet")).status_code == 404
    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": NEW, "etag": "x"})
    assert r.status_code == 404
    for verb in ("pin", "unpin", "archive", "restore"):
        r = await client.post(f"/api/skills/greet/{verb}", headers=json_headers())
        assert r.status_code == 404
    assert (owner.skills_dir / "greet" / "SKILL.md").read_text(encoding="utf-8") == SKILL

    # Their own library is theirs to write, and the reset it costs is THEIR lanes.
    r = await client.post("/api/skills", headers=json_headers(),
                          json={"name": "farewell", "content": NEW})
    assert r.status_code == 201, r.text
    theirs = tmp_path / "users" / "2" / "agent_home" / ".claude" / "skills"
    assert (theirs / "farewell" / "SKILL.md").read_text(encoding="utf-8") == NEW
    assert not (owner.skills_dir / "farewell").exists()
    assert deps.runner.reset_owneds == [(2, False)]
    assert [s["name"] for s in (await client.get("/api/skills")).json()["skills"]] \
        == ["farewell"]

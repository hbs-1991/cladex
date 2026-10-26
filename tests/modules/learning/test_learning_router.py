"""The skills router's seams: the library, the snapshot store, the cache reset.

`routes(deps)` closed over six bundle attributes at mount time. Each is a named
provider now, so a test swaps the library or redirects the snapshot store for one
app — `app.dependency_overrides[get_library] = ...` — instead of building a second
bundle. Since Wave 4b every provider resolves the REQUESTING user's scope, and
the last test drives two users through the real ones.
"""
import httpx

from claude_hermes.modules.learning.presentation.dependencies import (
    get_backup_keep,
    get_invalidate,
    get_library,
)
from claude_hermes.http.app import build_web_app

from tests.web_helpers import ORIGIN, json_headers, login, make_client

SKILL = (
    "---\nname: greet\ndescription: Greets people warmly.\n---\n\n"
    "## How\n\nSay hello.\n"
)
ROW = {"name": "greet", "source": "agent", "state": "active", "pinned": False,
       "broken": False, "description": "Greets people warmly.", "files": 1,
       "bytes": len(SKILL)}


class FakeLibrary:
    """A `SkillLibraryService` by shape only — no repository, no archive, no disk."""

    def __init__(self):
        self.written = []

    def list_skills(self):
        return [ROW]

    def read_skill(self, name):
        if name != "greet":
            return None
        return dict(ROW, content=SKILL, etag="e" * 64, tree=[])

    def write_skill(self, name, content, etag, before_write=None):
        if before_write is not None:
            before_write()
        self.written.append((name, content, etag))
        return "f" * 64


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def test_the_library_is_swappable_without_rebuilding_the_bundle(tmp_path):
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    app.dependency_overrides[get_library] = FakeLibrary
    client = await client_for(app)
    login(client, 1)

    r = await client.get("/api/skills")
    assert r.status_code == 200 and r.json() == {"skills": [ROW]}
    assert (await client.get("/api/skills/greet")).json()["content"] == SKILL
    assert (await client.get("/api/skills/nope")).status_code == 404


async def test_a_save_still_snapshots_bumps_and_resets_through_the_providers(tmp_path):
    """The three collaborators a save touches beyond the library — the snapshot store,
    the sidecar and the prompt-cache reset — are reached through providers now, and
    the `before_write` window is still inside `write_skill`."""
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeLibrary()
    app.dependency_overrides[get_library] = lambda: fake
    app.dependency_overrides[get_backup_keep] = lambda: 3
    client = await client_for(app)
    login(client, 1)
    owner = await deps.learning.owner()
    owner.skills_dir.mkdir(parents=True, exist_ok=True)

    r = await client.put("/api/skills/greet", headers=json_headers(),
                         json={"content": SKILL, "etag": "e" * 64})
    assert r.status_code == 200
    assert fake.written == [("greet", SKILL, "e" * 64)]
    assert len(list((tmp_path / "logs" / "skill_edit_backups").iterdir())) == 1
    assert owner.sidecar.get("greet")["patch_count"] == 1
    # The owner's reset sweeps the NULL-owner rows too: a lane bound before the
    # deploy was claimed is theirs (B-4).
    assert deps.runner.reset_owneds == [(1, True)]


async def test_a_save_body_is_lenient_and_the_library_still_judges_it(tmp_path):
    """`{content, etag}` are `Any` with defaults: a missing etag reaches the library
    as `""` and comes back as its own refusal, never as a 422."""
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeLibrary()
    app.dependency_overrides[get_library] = lambda: fake
    app.dependency_overrides[get_invalidate] = lambda: _noop
    client = await client_for(app)
    login(client, 1)
    (await deps.learning.owner()).skills_dir.mkdir(parents=True, exist_ok=True)

    r = await client.put("/api/skills/greet", headers=json_headers(), json={})
    assert r.status_code == 200
    assert fake.written == [("greet", "", "")]


async def test_every_provider_resolves_the_requesting_users_scope(tmp_path):
    """Wave 4b, D6: the 4a leak was `GET /api/skills` serving the owner's library
    to every logged-in user. A creates a skill; B lists and does not see it, B
    cannot reach it by name, and B creates and edits their own — through the
    real providers, so the isolation is the wiring's and not a fake's."""
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    b = type(client)(transport=client._transport, base_url=str(client.base_url))
    login(client, 1)
    login(b, 2)

    r = await client.post("/api/skills", headers=json_headers(),
                          json={"name": "greet", "content": SKILL})
    assert r.status_code == 201, r.text
    skills_dir = (await deps.learning.owner()).skills_dir
    assert (skills_dir / "greet" / "SKILL.md").is_file()

    assert (await b.get("/api/skills")).json() == {"skills": []}
    assert (await b.get("/api/skills/greet")).status_code == 404
    for verb in ("pin", "unpin", "archive", "restore"):
        assert (await b.post(f"/api/skills/greet/{verb}",
                             headers=json_headers())).status_code == 404
    r = await b.put("/api/skills/greet", headers=json_headers(),
                    json={"content": SKILL + "\nB was here.\n", "etag": "x"})
    assert r.status_code == 404
    assert (skills_dir / "greet" / "SKILL.md").read_bytes() == SKILL.encode()

    r = await b.post("/api/skills", headers=json_headers(),
                     json={"name": "greet", "content": SKILL})
    assert r.status_code == 201, r.text          # B's own `greet`, in B's tree
    b_skills = tmp_path / "users" / "2" / "agent_home" / ".claude" / "skills"
    assert (b_skills / "greet" / "SKILL.md").is_file()
    etag = (await b.get("/api/skills/greet")).json()["etag"]
    edited = SKILL + "\nB was here.\n"
    r = await b.put("/api/skills/greet", headers=json_headers(),
                    json={"content": edited, "etag": etag})
    assert r.status_code == 200 and r.json()["patch_count"] == 1
    assert (b_skills / "greet" / "SKILL.md").read_bytes() == edited.encode()
    assert (skills_dir / "greet" / "SKILL.md").read_bytes() == SKILL.encode()
    # B's snapshot store is under B's logs, and every reset was B's own lanes.
    assert (tmp_path / "users" / "2" / "logs" / "skill_edit_backups").is_dir()
    assert not (tmp_path / "logs" / "skill_edit_backups").exists()
    assert deps.runner.reset_owneds == [(1, True), (2, False), (2, False)]
    # …and A's listing is still A's alone.
    assert [s["name"] for s in (await client.get("/api/skills")).json()["skills"]] \
        == ["greet"]
    assert (await client.get("/api/skills/greet")).json()["content"] == SKILL


async def _noop():
    pass


async def test_a_cookie_whose_user_has_no_row_is_a_401_not_a_500(tmp_path):
    """Advisor risk 7 (code pass): the resolver raises identity's `UnknownUser`
    (a `LookupError`) rather than guessing the owner's tree; the skills
    providers turn that into the same answer as no session at all."""
    client, deps = await make_client(tmp_path)

    class Gone:
        async def for_user(self, user_id):
            raise LookupError(f"no users row for {user_id}")

    deps.learning = Gone()
    login(client, 1)
    r = await client.get("/api/skills")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

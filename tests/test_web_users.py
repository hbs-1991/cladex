"""The owner's user list: who is admitted, inviting an id that has never been here,
and revoking access — which the middleware must honour on the very next request."""
from tests.web_helpers import json_headers, login, make_client

NEWCOMER = 4242


async def test_the_list_is_rows_plus_the_config_allowlist(tmp_path):
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 1)
    r = await client.get("/api/users")
    assert r.status_code == 200
    rows = r.json()["users"]
    assert [u["user_id"] for u in rows] == [1, 2]          # owner first
    assert rows[0]["role"] == "owner" and rows[0]["source"] == "row"
    assert rows[1]["role"] == "user" and rows[1]["disabled"] is False

    # An allowlisted id nobody has met has no row, and is still admitted by
    # `check` — so a list of only the rows would understate who can get in.
    deps.cfg.allowed_users.append(777)
    rows = (await client.get("/api/users")).json()["users"]
    stranger = next(u for u in rows if u["user_id"] == 777)
    assert stranger == {"user_id": 777, "role": "user", "display_name": "", "username": "",
                        "created_at": "", "last_seen_at": "", "disabled": False,
                        "source": "config", "shell": False, "learning": False,
                        "shell_inherited": True, "learning_inherited": True}


async def test_an_invited_id_can_get_in(tmp_path):
    client, deps = await make_client(tmp_path, owner=1)
    login(client, 1)
    assert await deps.acl.admitted(NEWCOMER) is False
    r = await client.post("/api/users", headers=json_headers(), json={"user_id": NEWCOMER})
    assert r.status_code == 201, r.text
    assert r.json()["user_id"] == NEWCOMER and r.json()["source"] == "row"
    # Admitted everywhere at once: the row is what `check` would have written on
    # their first message, so Telegram and the browser agree without a restart.
    assert await deps.acl.admitted(NEWCOMER) is True
    assert await deps.acl.check(NEWCOMER, "private") == "allow"
    assert (await deps.acl.workspace_dir_of(NEWCOMER)) == f"users/{NEWCOMER}"

    r = await client.post("/api/users", headers=json_headers(), json={"user_id": NEWCOMER})
    assert r.status_code == 409 and r.json()["error"]["code"] == "user_exists"
    for bad in (0, -5, "nope", True, None):
        r = await client.post("/api/users", headers=json_headers(), json={"user_id": bad})
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", bad


async def test_disabling_closes_the_door_on_the_next_request(tmp_path):
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 2)
    assert (await client.get("/api/me")).status_code == 200

    login(client, 1)
    r = await client.post("/api/users/2/disable", headers=json_headers())
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert await deps.acl.check(2, "private") == "deny"
    # Their cookie is still signed, still unexpired, and refused anyway: the
    # middleware asks `admitted` per request, so revoking does not wait for a logout.
    login(client, 2)
    assert (await client.get("/api/me")).status_code == 401

    login(client, 1)
    r = await client.post("/api/users/2/enable", headers=json_headers())
    assert r.status_code == 200 and r.json()["disabled"] is False
    login(client, 2)
    assert (await client.get("/api/me")).status_code == 200


async def test_the_owner_cannot_be_disabled_and_a_stranger_is_a_404(tmp_path):
    client, _deps = await make_client(tmp_path, owner=1)
    login(client, 1)
    r = await client.post("/api/users/1/disable", headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "owner_immutable"
    assert (await client.get("/api/me")).status_code == 200      # still the owner
    r = await client.post("/api/users/98765/disable", headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_disabling_a_config_id_writes_the_row_that_overrules_the_file(tmp_path):
    """An id in `allowed_users` with no row is admitted by the file. The only way
    to overrule the file from the database is to give it the row `check` reads first."""
    client, deps = await make_client(tmp_path, owner=1)
    login(client, 1)
    deps.cfg.allowed_users.append(777)
    assert await deps.acl.admitted(777) is True
    r = await client.post("/api/users/777/disable", headers=json_headers())
    assert r.status_code == 200 and r.json()["disabled"] is True
    assert await deps.acl.admitted(777) is False
    assert await deps.acl.check(777, "private") == "deny"


async def test_every_path_is_owner_only(tmp_path):
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 2)
    assert (await client.get("/api/users")).status_code == 403
    r = await client.post("/api/users", headers=json_headers(), json={"user_id": NEWCOMER})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    for path in ("/api/users/1/disable", "/api/users/2/enable"):
        assert (await client.post(path, headers=json_headers())).status_code == 403
    assert await deps.acl.admitted(NEWCOMER) is False


# --- per-user grants (0009) --------------------------------------------------


def grants_of(member: dict) -> tuple:
    return (member["shell"], member["learning"],
            member["shell_inherited"], member["learning_inherited"])


async def test_the_list_carries_effective_grants_and_says_which_are_inherited(tmp_path):
    """A NULL column shows the CURRENT config default, marked inherited; the owner
    reads true/true and never inherited; a config-only id reads the defaults."""
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 1)
    deps.cfg.allowed_users.append(777)
    rows = {u["user_id"]: u for u in (await client.get("/api/users")).json()["users"]}
    assert grants_of(rows[1]) == (True, True, False, False)
    assert grants_of(rows[2]) == (False, False, True, True)
    assert grants_of(rows[777]) == (False, False, True, True)

    # The defaults are read per request, not copied at boot.
    deps.cfg.users_shell_enabled = True
    deps.cfg.users_learning_enabled = True
    rows = {u["user_id"]: u for u in (await client.get("/api/users")).json()["users"]}
    assert grants_of(rows[1]) == (True, True, False, False)
    assert grants_of(rows[2]) == (True, True, True, True)
    assert grants_of(rows[777]) == (True, True, True, True)


async def test_a_patch_writes_only_the_named_grants_and_the_list_agrees(tmp_path):
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 1)
    r = await client.patch("/api/users/2", headers=json_headers(), json={"shell": True})
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == 2 and r.json()["source"] == "row"
    assert grants_of(r.json()) == (True, False, False, True)
    # Only the shell column was written: learning still follows the default.
    deps.cfg.users_learning_enabled = True
    rows = {u["user_id"]: u for u in (await client.get("/api/users")).json()["users"]}
    assert grants_of(rows[2]) == (True, True, False, True)

    r = await client.patch("/api/users/2", headers=json_headers(),
                           json={"learning": False})
    assert r.status_code == 200 and grants_of(r.json()) == (True, False, False, False)

    r = await client.patch("/api/users/2", headers=json_headers(),
                           json={"shell": False, "learning": True, "junk": 1})
    assert r.status_code == 200 and grants_of(r.json()) == (False, True, False, False)
    rows = {u["user_id"]: u for u in (await client.get("/api/users")).json()["users"]}
    assert grants_of(rows[2]) == (False, True, False, False)
    # The policy every turn asks agrees with the wire.
    grants = await deps.acl.grants(2)
    assert (grants.shell, grants.learning) == (False, True)


async def test_a_patch_must_name_a_grant_as_a_json_boolean(tmp_path):
    client, _deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 1)
    for body in ({}, {"junk": True}, {"shell": None}, {"shell": "true"},
                 {"learning": 1}, {"shell": 0}, {"learning": "false"}, [True]):
        r = await client.patch("/api/users/2", headers=json_headers(), json=body)
        assert r.status_code == 400, (body, r.text)
        assert r.json()["error"]["code"] == "bad_request", body
    rows = {u["user_id"]: u for u in (await client.get("/api/users")).json()["users"]}
    assert grants_of(rows[2]) == (False, False, True, True)       # nothing written
    r = await client.patch("/api/users/nope", headers=json_headers(), json={"shell": True})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


async def test_a_grant_needs_a_row_and_never_creates_one(tmp_path):
    """A config-only id is a 404 until its first message: creating a row here would
    enrol a never-seen id in the curator's sweep."""
    client, deps = await make_client(tmp_path, owner=1)
    login(client, 1)
    deps.cfg.allowed_users.append(777)
    for uid in (777, 98765):
        r = await client.patch(f"/api/users/{uid}", headers=json_headers(),
                               json={"shell": True})
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", uid
        assert await deps.acl.user(uid) is None
    rows = {u["user_id"]: u for u in (await client.get("/api/users")).json()["users"]}
    assert rows[777]["source"] == "config"


async def test_the_owners_grants_are_not_a_switch(tmp_path):
    client, deps = await make_client(tmp_path, owner=1)
    login(client, 1)
    r = await client.patch("/api/users/1", headers=json_headers(), json={"shell": False})
    assert r.status_code == 409 and r.json()["error"]["code"] == "owner_immutable"
    grants = await deps.acl.grants(1)
    assert (grants.shell, grants.learning) == (True, True)


async def test_a_patch_is_owner_only_and_keeps_the_transport_policy(tmp_path):
    client, deps = await make_client(tmp_path, owner=1, allowed=[2])
    login(client, 2)
    r = await client.patch("/api/users/2", headers=json_headers(), json={"shell": True})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert (await deps.acl.grants(2)).shell is False

    login(client, 1)
    # The exact Origin, and JSON: a forged cross-site PATCH must not grant a shell.
    r = await client.patch("/api/users/2", json={"shell": True},
                           headers={"Origin": "https://evil.test",
                                    "Content-Type": "application/json"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    r = await client.patch("/api/users/2", content=b'{"shell": true}',
                           headers={"Origin": json_headers()["Origin"],
                                    "Content-Type": "text/plain"})
    assert r.status_code == 415
    assert (await deps.acl.grants(2)).shell is False

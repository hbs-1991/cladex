"""Identity's pure layer: the session token and the two Telegram proofs."""
import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from claude_hermes.modules.identity.domain.exceptions import LoginError
from claude_hermes.modules.identity.domain.models import CurrentUser, Grants, Role, User
from claude_hermes.modules.identity.domain.session_token import (
    COOKIE, read_session, sign_session,
)
from claude_hermes.modules.identity.domain.telegram_proof import (
    login_key, verify_login, verify_webapp, webapp_key,
)

TOKEN = "123456:ABC-DEF"


def _widget(user_id=42, auth_date=1_700_000_000, key=None, **extra):
    data = {"id": user_id, "first_name": "Ada", "auth_date": auth_date, **extra}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(key or login_key(TOKEN), check.encode(), hashlib.sha256).hexdigest()
    return data


def _init_data(user_id=42, auth_date=1_700_000_000, key=None):
    fields = {"auth_date": str(auth_date),
              "user": json.dumps({"id": user_id, "first_name": "Ada"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    fields["hash"] = hmac.new(key or webapp_key(TOKEN), check.encode(),
                              hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_the_cookie_name_is_frozen():
    assert COOKIE == "cladex_session"


def test_current_user_role():
    assert CurrentUser(7, is_owner=True).role is Role.OWNER
    assert CurrentUser(8).role is Role.USER
    assert Role.USER.value == "user"


def test_session_token_roundtrip_and_expiry():
    tok = sign_session(42, exp=2_000_000_000, secret="s")
    assert read_session(tok, "s", now=1_900_000_000) == 42
    assert read_session(tok, "s", now=2_000_000_001) is None
    assert read_session(tok, "other", now=1_900_000_000) is None
    assert read_session("garbage", "s", now=0) is None


def test_the_two_keys_are_different_on_purpose():
    """Neither proof may be replayable as the other."""
    assert login_key(TOKEN) == hashlib.sha256(TOKEN.encode()).digest()
    assert webapp_key(TOKEN) == hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    with pytest.raises(LoginError):
        verify_webapp(_init_data(key=login_key(TOKEN)), webapp_key(TOKEN), now=1_700_000_100)


def test_widget_and_init_data_verify_and_reject():
    assert verify_login(_widget(), login_key(TOKEN), now=1_700_000_100) == 42
    assert verify_webapp(_init_data(), webapp_key(TOKEN), now=1_700_000_100) == 42
    tampered = _widget()
    tampered["id"] = 43
    with pytest.raises(LoginError):
        verify_login(tampered, login_key(TOKEN), now=1_700_000_100)
    with pytest.raises(LoginError):       # stale
        verify_login(_widget(), login_key(TOKEN), now=1_700_000_000 + 86_401)
    with pytest.raises(LoginError):
        verify_webapp("", webapp_key(TOKEN), now=0)


def test_login_error_is_still_a_value_error():
    """The routes catch `(LoginError, ValueError, ...)`; the base must not change."""
    assert issubclass(LoginError, ValueError)


def test_verify_webapp_user_returns_the_signed_user_object():
    """Wave 4b, D10: the Mini App's `user` JSON carries the same three display
    fields the widget signs, and it sits inside the HMAC-checked string — so
    the name comes back from the SAME call that verified it (R-7). A standalone
    parser would be the function the next caller reaches for alone, and the
    display name would become attacker-supplied."""
    from claude_hermes.modules.identity.domain.telegram_proof import verify_webapp_user

    uid, user = verify_webapp_user(_init_data(), webapp_key(TOKEN), now=1_700_000_100)
    assert uid == 42 and user == {"id": 42, "first_name": "Ada"}
    # Tampering with the user object fails the signature, so no name comes back.
    good = _init_data()
    forged = good.replace("Ada", "Eve")
    with pytest.raises(LoginError):
        verify_webapp_user(forged, webapp_key(TOKEN), now=1_700_000_100)
    # A `user` field that is valid JSON but not an object is malformed, not a crash.
    fields = {"auth_date": "1700000000", "user": "[42]"}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    fields["hash"] = hmac.new(webapp_key(TOKEN), check.encode(),
                              hashlib.sha256).hexdigest()
    with pytest.raises(LoginError):
        verify_webapp_user(urlencode(fields), webapp_key(TOKEN), now=1_700_000_100)


# --- per-user grants (0009) ---------------------------------------------------


def test_grants_of_is_the_one_resolution_rule():
    """Owner row → everything; any other row → its column, else the default; no
    row → the defaults. `None` on a column is "no choice", never False."""
    off, on = Grants(shell=False, learning=False), Grants(shell=True, learning=True)
    owner = User(id=1, role="owner", workspace_dir="")
    # The owner gets both whatever the defaults say — and whatever a stray column
    # on their row says: their lanes already get the full toolset and the observer.
    assert Grants.of(owner, off) == on
    assert Grants.of(User(id=1, role="owner", workspace_dir="", shell_enabled=False,
                          learning_enabled=False), off) == on

    inherits = User(id=2, role="user", workspace_dir="users/2")
    assert Grants.of(inherits, off) == off
    assert Grants.of(inherits, on) == on
    assert Grants.of(inherits, Grants(shell=True, learning=False)) == \
        Grants(shell=True, learning=False)

    # An explicit choice beats the default in either direction, column by column.
    chosen = User(id=3, role="user", workspace_dir="users/3", shell_enabled=True)
    assert Grants.of(chosen, off) == Grants(shell=True, learning=False)
    withdrawn = User(id=4, role="user", workspace_dir="users/4",
                     shell_enabled=False, learning_enabled=False)
    assert Grants.of(withdrawn, on) == off

    # Nobody by that id: the defaults, never the owner's answer.
    assert Grants.of(None, off) == off
    assert Grants.of(None, on) == on


def test_from_row_reads_the_two_grant_columns_and_tolerates_their_absence():
    base = {"id": 5, "role": "user", "workspace_dir": "users/5", "display_name": "",
            "username": "", "created_at": "", "last_seen_at": "", "disabled_at": None}
    plain = User.from_row(base)
    assert (plain.shell_enabled, plain.learning_enabled) == (None, None)
    row = User.from_row({**base, "shell_enabled": True, "learning_enabled": False})
    assert (row.shell_enabled, row.learning_enabled) == (True, False)
    row = User.from_row({**base, "shell_enabled": None, "learning_enabled": None})
    assert (row.shell_enabled, row.learning_enabled) == (None, None)

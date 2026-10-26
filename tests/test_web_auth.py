import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from claude_hermes.modules.identity.domain.exceptions import LoginError
from claude_hermes.modules.identity.domain.session_token import read_session, sign_session
from claude_hermes.modules.identity.domain.telegram_proof import (
    login_key, verify_login, verify_webapp, webapp_key,
)

TOKEN = "123456:ABC-DEF"


def widget_payload(user_id=42, auth_date=1_700_000_000, key=None, **extra):
    data = {"id": user_id, "first_name": "Ada", "auth_date": auth_date, **extra}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    key = key or login_key(TOKEN)
    data["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return data


def init_data(user_id=42, auth_date=1_700_000_000, key=None, **extra):
    """What Telegram.WebApp.initData looks like: a query string, `user` as JSON."""
    fields = {"query_id": "AAHdF6IQAAAAAN0XohDhrOrc", "auth_date": str(auth_date),
              "user": json.dumps({"id": user_id, "first_name": "Ada", "language_code": "en"},
                                 separators=(",", ":")),
              "signature": "sig", **extra}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    key = key or webapp_key(TOKEN)
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_login_key_is_sha256_of_the_token():
    assert login_key(TOKEN) == hashlib.sha256(TOKEN.encode()).digest()


def test_valid_payload_yields_the_user_id():
    p = widget_payload(username="ada", photo_url="https://t.me/i/x.jpg")
    assert verify_login(p, login_key(TOKEN), now=1_700_000_100) == 42


def test_tampered_payload_is_rejected():
    p = widget_payload()
    p["id"] = 43
    with pytest.raises(LoginError):
        verify_login(p, login_key(TOKEN), now=1_700_000_100)


def test_wrong_key_is_rejected():
    p = widget_payload(key=b"other")
    with pytest.raises(LoginError):
        verify_login(p, login_key(TOKEN), now=1_700_000_100)


def test_stale_auth_date_is_rejected():
    p = widget_payload(auth_date=1_700_000_000)
    with pytest.raises(LoginError):
        verify_login(p, login_key(TOKEN), now=1_700_000_000 + 86_401)


def test_missing_fields_are_rejected():
    with pytest.raises(LoginError):
        verify_login({"id": 1}, login_key(TOKEN), now=0)


def test_session_cookie_roundtrip_and_expiry():
    tok = sign_session(42, exp=2_000_000_000, secret="s")
    assert read_session(tok, "s", now=1_900_000_000) == 42
    assert read_session(tok, "s", now=2_000_000_001) is None       # expired
    assert read_session(tok, "other", now=1_900_000_000) is None   # bad signature
    assert read_session("garbage", "s", now=0) is None
    assert read_session(tok.replace("42.", "43.", 1), "s", now=0) is None


def test_webapp_key_is_hmac_under_the_webappdata_constant():
    assert webapp_key(TOKEN) == hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    assert webapp_key(TOKEN) != login_key(TOKEN)   # neither proof replays as the other


def test_valid_init_data_yields_the_user_id():
    assert verify_webapp(init_data(), webapp_key(TOKEN), now=1_700_000_100) == 42


def test_init_data_signed_with_the_widget_key_is_rejected():
    with pytest.raises(LoginError):
        verify_webapp(init_data(key=login_key(TOKEN)), webapp_key(TOKEN), now=1_700_000_100)


def test_tampered_init_data_is_rejected():
    tampered = init_data().replace("%22id%22%3A42", "%22id%22%3A43")
    assert tampered != init_data()
    with pytest.raises(LoginError):
        verify_webapp(tampered, webapp_key(TOKEN), now=1_700_000_100)


def test_stale_init_data_is_rejected():
    with pytest.raises(LoginError):
        verify_webapp(init_data(auth_date=1_700_000_000), webapp_key(TOKEN),
                      now=1_700_000_000 + 86_401)


@pytest.mark.parametrize("bad", ["", None, "auth_date=1", "hash=abc", "user=notjson&auth_date=1&hash=x"])
def test_malformed_init_data_is_rejected(bad):
    with pytest.raises(LoginError):
        verify_webapp(bad, webapp_key(TOKEN), now=0)

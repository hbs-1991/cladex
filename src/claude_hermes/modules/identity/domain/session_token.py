"""The signed session cookie: mint one, read one back. Pure hmac/hashlib.

The token is `<user_id>.<exp>.<hmac>` under the deployment's web secret, so the
server keeps no session table and a restart invalidates nothing. Moved verbatim
from `web/auth.py`.
"""
from __future__ import annotations

import hashlib
import hmac

COOKIE = "cladex_session"


def sign_session(user_id: int, exp: int, secret: str) -> str:
    body = f"{user_id}.{exp}"
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_session(token: str, secret: str, now: int) -> int | None:
    parts = (token or "").split(".")
    if len(parts) != 3:
        return None
    user, exp, sig = parts
    expected = hmac.new(secret.encode(), f"{user}.{exp}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        if int(exp) < now:
            return None
        return int(user)
    except ValueError:
        return None

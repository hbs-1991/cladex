"""Telegram sign-in verification: the Login Widget and the Mini App.

Two ways in, one check each. The Login Widget's data-check string is `key=value`
pairs (all fields but `hash`) sorted by key and joined with newlines,
HMAC-SHA256 under sha256(bot_token). A Mini App's `initData` is the same string
shape over the query-string fields Telegram hands the page, HMAC-SHA256 under
HMAC-SHA256("WebAppData", bot_token) — a different key on purpose, so neither
proof can be replayed as the other. Only the two derived keys ever reach the
web layer; the token itself is popped from the environment at startup (main.py).

Moved verbatim from `web/auth.py`: these are the security checks, not a rewrite.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from urllib.parse import parse_qsl

from claude_hermes.modules.identity.domain.exceptions import LoginError


def login_key(bot_token: str) -> bytes:
    return hashlib.sha256(bot_token.encode()).digest()


def webapp_key(bot_token: str) -> bytes:
    """The Mini App check key: HMAC-SHA256 keyed on the literal "WebAppData"."""
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def _check(fields: dict, given: str, key: bytes) -> None:
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise LoginError("bad signature")


def _fresh(auth_date: int, now: int, max_age: int) -> None:
    if now - auth_date > max_age or auth_date - now > 300:
        raise LoginError("login is stale")


def verify_login(payload: dict, key: bytes, now: int, max_age: int = 86_400) -> int:
    try:
        given = str(payload["hash"])
        user_id = int(payload["id"])
        auth_date = int(payload["auth_date"])
    except (KeyError, TypeError, ValueError) as e:
        raise LoginError("malformed login payload") from e
    _check({k: v for k, v in payload.items() if k != "hash"}, given, key)
    _fresh(auth_date, now, max_age)
    return user_id


def verify_webapp_user(init_data: str, key: bytes, now: int,
                       max_age: int = 86_400) -> tuple[int, Mapping]:
    """`Telegram.WebApp.initData` as the page received it: a query string whose
    `user` field is JSON. Returns the user id it vouches for AND the `user`
    object it was decoded from (`id`, `first_name`, `last_name`, `username`, …).

    Parsing and verification are one function on purpose (Wave 4b, R-7): a
    standalone parser would be the function the next caller reaches for alone,
    and a display name read from an unverified `initData` is attacker-supplied.
    Everything in the returned mapping sits inside the HMAC-checked string."""
    if not isinstance(init_data, str) or not init_data:
        raise LoginError("malformed init data")
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    given = fields.pop("hash", None)
    if given is None:
        raise LoginError("malformed init data")
    _check(fields, given, key)
    try:
        auth_date = int(fields["auth_date"])
        user = json.loads(fields["user"])
        user_id = int(user["id"])
    except (KeyError, TypeError, ValueError) as e:      # JSONDecodeError is a ValueError
        raise LoginError("malformed init data") from e
    if not isinstance(user, dict):
        raise LoginError("malformed init data")
    _fresh(auth_date, now, max_age)
    return user_id, user


def verify_webapp(init_data: str, key: bytes, now: int, max_age: int = 86_400) -> int:
    """The id alone — `verify_webapp_user` for a caller with no use for the name."""
    return verify_webapp_user(init_data, key, now, max_age)[0]

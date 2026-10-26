"""Where a `SecretBox` comes from when nobody handed one over.

Its own module, and not `wiring.py`, because a box cached in the composition file
would be an import cycle for the infrastructure that needs it.

The rule the whole secrets design rests on: the key is read from the environment
exactly once, at startup, before anything can spawn the CLI. `main.build_app`
builds that box (`_secret_box`) and passes it explicitly to `wiring.build(box=)`,
and every store that seals or opens a row — the secrets table, the OAuth refresher
that writes a refreshed token — is built over that one object. `secret_box()` is
what a caller WITHOUT a box gets: a test harness, `wiring.build` called bare.
"""
from __future__ import annotations

import os

import structlog

from claude_hermes.core.crypto import SECRETS_KEY_ENV, SecretBox

log = structlog.get_logger(__name__)

__all__ = ["secret_box"]

_FALLBACK_BOX: SecretBox | None = None


def secret_box() -> SecretBox:
    """The `SecretBox` every store built without an explicit one shares.

    `CH_SECRETS_KEY` when it is set — which is what a deployment does. When it is
    not, ONE generated key, cached for the life of the process, so a test run gets
    a store that round-trips without a key in the environment. Cached rather than
    made per call for exactly that reason: two boxes would not open each other's
    rows, and every `load()` would silently come back empty.

    A deployment must never reach the fallback — a restart would leave every
    stored secret unreadable — which is why it is loud, and why `run()` refuses to
    boot without the variable (`main.require_secrets_key`).
    """
    global _FALLBACK_BOX
    key = os.environ.get(SECRETS_KEY_ENV, "")
    if key:
        return SecretBox(key)
    if _FALLBACK_BOX is None:
        log.warning("%s is not set: extension secrets are sealed with a key that "
                    "dies with this process", SECRETS_KEY_ENV)
        _FALLBACK_BOX = SecretBox(SecretBox.generate_key())
    return _FALLBACK_BOX

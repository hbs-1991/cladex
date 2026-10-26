"""Symmetric encryption for the secrets the app stores on behalf of extensions.

An MCP server's API key has to be readable by the process that launches it, so it
cannot be hashed — it has to be encrypted, and something has to hold the key. The
rule that makes that worth doing is that the key never sits next to the
ciphertext: the ciphertext is a row in PostgreSQL, the key is `CH_SECRETS_KEY` in
the environment and is popped from `os.environ` at startup. A stolen database
dump is then not a stolen credential, and neither is a shell inside the agent.

Fernet is AES-128-CBC + HMAC-SHA256 with a version byte and a timestamp, so a
sealed value is authenticated: a row edited in the database fails to open rather
than decrypting to attacker-chosen plaintext.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

from claude_hermes.core.exceptions import ApplicationException

SECRETS_KEY_ENV = "CH_SECRETS_KEY"

_GENERATE_HINT = (
    'generate one with: python -c "from cryptography.fernet import Fernet; '
    'print(Fernet.generate_key().decode())"')


class SecretsKeyError(ApplicationException):
    """`CH_SECRETS_KEY` is missing or is not a valid Fernet key.

    Deliberately loud and deliberately unrecoverable: starting without it would
    give a deployment that accepts secrets it can never read back, and every
    failure after that would look like a database problem.
    """
    status, code = 500, "secrets_key"

    def __init__(self, message: str = "") -> None:
        super().__init__(message=message)


class SecretDecryptError(ApplicationException):
    """A stored ciphertext did not open under this key — a rotated/replaced
    `CH_SECRETS_KEY`, or a row that was tampered with."""
    status, code = 500, "secret_decrypt"

    def __init__(self, message: str = "") -> None:
        super().__init__(message=message)


class SecretBox:
    """Seals a string into bytes and opens it again. One instance per process."""

    def __init__(self, key: str | bytes) -> None:
        if not key:
            raise SecretsKeyError(f"{SECRETS_KEY_ENV} is empty; {_GENERATE_HINT}")
        raw = key.encode() if isinstance(key, str) else key
        try:
            self._fernet = Fernet(raw)
        except (ValueError, TypeError) as e:
            raise SecretsKeyError(
                f"{SECRETS_KEY_ENV} is not a valid Fernet key ({e}); {_GENERATE_HINT}"
            ) from None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SecretBox:
        """Build from `CH_SECRETS_KEY`. Takes the mapping so the caller can pass
        a snapshot taken *before* the startup pop, rather than reaching into
        `os.environ` after the secrets are gone."""
        source = os.environ if env is None else env
        value = source.get(SECRETS_KEY_ENV, "")
        if not value:
            raise SecretsKeyError(f"{SECRETS_KEY_ENV} is not set; {_GENERATE_HINT}")
        return cls(value)

    @staticmethod
    def generate_key() -> str:
        """A fresh key, for `.env.example` and for tests."""
        return Fernet.generate_key().decode()

    def seal(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode())

    def open(self, blob: bytes) -> str:
        try:
            return self._fernet.decrypt(bytes(blob)).decode()
        except InvalidToken:
            raise SecretDecryptError(
                f"stored secret does not open under {SECRETS_KEY_ENV}"
                " (key rotated, or the row was modified)") from None

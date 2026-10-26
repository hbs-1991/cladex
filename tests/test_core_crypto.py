"""`SecretBox` — the thing standing between a database dump and a live API key."""
from __future__ import annotations

import pytest

from claude_hermes.core.crypto import (
    SECRETS_KEY_ENV,
    SecretBox,
    SecretDecryptError,
    SecretsKeyError,
)
from claude_hermes.core.exceptions import ApplicationException


def test_round_trip():
    box = SecretBox(SecretBox.generate_key())
    sealed = box.seal("sk-live-abcdef")
    assert isinstance(sealed, bytes)
    assert b"sk-live" not in sealed          # not merely encoded
    assert box.open(sealed) == "sk-live-abcdef"


def test_unicode_survives():
    box = SecretBox(SecretBox.generate_key())
    assert box.open(box.seal("пароль-🔐")) == "пароль-🔐"


def test_each_seal_is_different():
    """Fernet puts a random IV in every token, so two rows holding the same key
    do not look the same in a dump."""
    box = SecretBox(SecretBox.generate_key())
    assert box.seal("same") != box.seal("same")


def test_another_key_cannot_open_it():
    sealed = SecretBox(SecretBox.generate_key()).seal("secret")
    with pytest.raises(SecretDecryptError):
        SecretBox(SecretBox.generate_key()).open(sealed)


def test_a_tampered_row_fails_instead_of_decrypting():
    """Fernet authenticates, so editing the ciphertext in the database is caught
    rather than yielding attacker-chosen plaintext."""
    box = SecretBox(SecretBox.generate_key())
    sealed = bytearray(box.seal("secret"))
    sealed[-1] ^= 0x01
    with pytest.raises(SecretDecryptError):
        box.open(bytes(sealed))


def test_errors_are_application_exceptions():
    """They have to render as an error the HTTP layer understands, not as a
    bare crash, because the extensions router raises them through."""
    assert issubclass(SecretsKeyError, ApplicationException)
    assert issubclass(SecretDecryptError, ApplicationException)
    assert SecretsKeyError().code == "secrets_key"


def test_missing_key_says_how_to_make_one():
    with pytest.raises(SecretsKeyError) as e:
        SecretBox.from_env({})
    assert SECRETS_KEY_ENV in str(e.value)
    assert "Fernet.generate_key" in str(e.value)


@pytest.mark.parametrize("bad", ["", "not-a-key", "c2hvcnQ="])
def test_an_invalid_key_is_refused_at_construction(bad):
    """Loudly, at boot. Accepting it would give a deployment that stores secrets
    it can never read back, and every later failure would look like a DB bug."""
    with pytest.raises(SecretsKeyError):
        SecretBox(bad)


def test_from_env_reads_the_mapping_it_is_given(monkeypatch):
    key = SecretBox.generate_key()
    # A snapshot taken before startup pops the secret out of os.environ.
    assert SecretBox.from_env({SECRETS_KEY_ENV: key}).open(
        SecretBox(key).seal("v")) == "v"

    monkeypatch.setenv(SECRETS_KEY_ENV, key)
    assert SecretBox.from_env().open(SecretBox(key).seal("v")) == "v"
    monkeypatch.delenv(SECRETS_KEY_ENV)
    with pytest.raises(SecretsKeyError):
        SecretBox.from_env()

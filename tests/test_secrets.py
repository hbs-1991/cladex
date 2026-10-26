"""Extension secrets, Fernet-sealed in `extension_secrets` (Wave 3a).

The store moved off the volume — `secrets.env` was readable by anything running as
the agent, including the agent's own Bash — but every rule about what a secret may
be called and may contain moved with it unchanged: `server_spec` still hands these
values to a process environment.

Wave 5 gives every row an owner. `name` is the env-var name a manifest declares,
so two users' `NOTION_TOKEN` are two secrets and `owner_id` is required on every
call — a store that answered without one would answer with somebody's credentials.
"""
import pytest

from claude_hermes.core.crypto import SecretBox
from claude_hermes.modules.extensions.domain import secrets
from claude_hermes.modules.extensions.infrastructure.repositories.pg_secrets import (
    PgSecrets,
)

from conftest import ensure_users
from tests.ext_helpers import secrets_for


async def test_put_then_load_roundtrip(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    await s.put("EXAMPLE_API_KEY", "abc123", owner_id=1)
    assert await s.load(owner_id=1) == {"EXAMPLE_API_KEY": "abc123"}


async def test_put_overwrites_and_keeps_the_others(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    await s.put("A_KEY", "one", owner_id=1)
    await s.put("B_KEY", "two", owner_id=1)
    await s.put("A_KEY", "three", owner_id=1)
    assert await s.load(owner_id=1) == {"A_KEY": "three", "B_KEY": "two"}


async def test_load_of_an_empty_table_is_empty(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    assert await s.load(owner_id=1) == {}


async def test_values_may_contain_equals_signs(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    await s.put("TOKEN", "a=b=c", owner_id=1)
    assert (await s.load(owner_id=1))["TOKEN"] == "a=b=c"


async def test_forget_removes_one_and_an_absent_one_is_fine(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    await s.put("A_KEY", "one", owner_id=1)
    await s.forget("A_KEY", owner_id=1)
    assert "A_KEY" not in await s.load(owner_id=1)
    await s.forget("A_KEY", owner_id=1)


@pytest.mark.parametrize("name", ["lower", "1LEADS_WITH_DIGIT", "HAS-DASH", "HAS SPACE", ""])
async def test_bad_names_are_refused(engine, name):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    with pytest.raises(secrets.SecretNameError):
        await s.put(name, "v", owner_id=1)


async def test_newlines_in_a_value_are_refused(engine):
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    with pytest.raises(secrets.SecretNameError):
        await s.put("KEY", "line1\nline2", owner_id=1)


async def test_the_stored_row_is_ciphertext_not_the_value(engine):
    """The point of the table: a database dump is not a credential dump."""
    from sqlalchemy import select

    from claude_hermes.modules.extensions.domain.models import ExtensionSecret

    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    await s.put("EXAMPLE_API_KEY", "super-secret-value", owner_id=1)
    async with engine.connect() as conn:
        (blob,) = (await conn.execute(select(ExtensionSecret.ciphertext))).first()
    assert b"super-secret-value" not in bytes(blob)


async def test_a_row_sealed_under_another_key_is_skipped_not_fatal(engine):
    """A rotated CH_SECRETS_KEY must cost the one secret, not every turn: the
    compose pass already reports a missing secret per server, and that is a far
    better failure than a chat that cannot start."""
    await ensure_users(engine, owner=1)
    s = secrets_for(engine)
    await s.put("GOOD_KEY", "v", owner_id=1)
    stranger = PgSecrets(engine, SecretBox(SecretBox.generate_key()))
    await stranger.put("OTHER_KEY", "v", owner_id=1)
    assert await s.load(owner_id=1) == {"GOOD_KEY": "v"}


async def test_the_same_variable_name_is_two_secrets(engine):
    """`extension_secrets.name` is an env var name — a namespace two people share
    until the key says otherwise."""
    await ensure_users(engine, 7, owner=1)
    store = secrets_for(engine)
    await store.put("NOTION_TOKEN", "owners", owner_id=1)
    await store.put("NOTION_TOKEN", "hers", owner_id=7)
    assert (await store.load(owner_id=1))["NOTION_TOKEN"] == "owners"
    assert (await store.load(owner_id=7))["NOTION_TOKEN"] == "hers"


async def test_forget_takes_one_owners_secret_only(engine):
    await ensure_users(engine, 7, owner=1)
    store = secrets_for(engine)
    await store.put("NOTION_TOKEN", "owners", owner_id=1)
    await store.put("NOTION_TOKEN", "hers", owner_id=7)
    await store.forget("NOTION_TOKEN", owner_id=7)
    assert await store.load(owner_id=1) == {"NOTION_TOKEN": "owners"}
    assert await store.load(owner_id=7) == {}


def test_interpolate_substitutes_known_vars():
    got = secrets.interpolate("Bearer ${TOKEN}", {"TOKEN": "xyz"})
    assert got == "Bearer xyz"


def test_interpolate_leaves_unknown_vars_alone():
    """A literal placeholder is a visible failure; an empty string is a silent one."""
    assert secrets.interpolate("Bearer ${NOPE}", {}) == "Bearer ${NOPE}"


def test_missing_lists_absent_names():
    assert secrets.missing(["A", "B"], {"A": "1"}) == ["B"]


# --- the `/secret` seam (composition root) ------------------------------------

def _command_service(store):
    """telegram's `CommandService` over the REAL secret store, exactly as
    `main.build_app` wires it: the owner gate, `TranslatingSecrets` in between to
    swap one module's `SecretNameError` for the other's, and `PgSecrets` beneath.
    The other three ports are never reached by `/secret`."""
    from claude_hermes.core.app_context import TranslatingSecrets
    from claude_hermes.modules.telegram.application.services.commands import (
        CommandService,
    )
    from claude_hermes.modules.telegram.domain.exceptions import (
        SecretNameError as TelegramSecretNameError,
    )

    class Acl:
        async def is_owner(self, user_id):
            return user_id == 1

    return CommandService(
        acl=Acl(),
        secrets=TranslatingSecrets(store, error=TelegramSecretNameError),
        approvals=None, catalog=None, runner=None)


def _event(user_id: int = 1):
    from claude_hermes.modules.telegram.domain.models import MessageEvent
    return MessageEvent(chat_type="private", chat_id=1, user_id=user_id, text="",
                        mentioned=False, message_id=9)


async def test_the_secret_command_seals_the_row_the_senders_turns_compose(engine):
    """The composition-root seam, over the real store.

    `/secret` runs owner gate -> `TranslatingSecrets` -> `PgSecrets`, and every
    unit above this line uses a fake store. So this is the one test that would
    catch an adapter still calling `put(name, value)`: `handle_secret` deletes
    the carrying message FIRST, so a `TypeError` on the store loses the pasted
    value with nothing left to retry from.
    """
    await ensure_users(engine, 7, owner=1)
    store = secrets_for(engine)

    reply = await _command_service(store).handle_secret("NOTION_TOKEN=abc123",
                                                        _event())

    assert "abc123" not in reply
    assert await store.load(owner_id=1) == {"NOTION_TOKEN": "abc123"}
    assert await store.load(owner_id=7) == {}


async def test_a_malformed_name_still_answers_with_the_usage_line(engine):
    """`PgSecrets` raises its own `ValueError`; `TranslatingSecrets` turns it into
    telegram's `SecretNameError`, which the command answers with the usage line.
    Two modules, two exception classes, and the composition root between them."""
    await ensure_users(engine, owner=1)
    store = secrets_for(engine)

    assert "NAME=value" in await _command_service(store).handle_secret("lower=x",
                                                                       _event())
    assert await store.load(owner_id=1) == {}

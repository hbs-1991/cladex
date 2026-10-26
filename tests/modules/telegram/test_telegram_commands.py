"""`/secret` and `/approve` against port fakes — no filesystem, no catalog, no DB.

These are the two commands the gateway intercepts before `run_turn`, so every
branch here is a security branch.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from claude_hermes.core.config import Config
from claude_hermes.modules.telegram.application.services.commands import CommandService
from claude_hermes.modules.telegram.application.services.routing import MessageRouter
from claude_hermes.modules.telegram.domain.exceptions import SecretNameError
from claude_hermes.modules.telegram.domain.models import MessageEvent


@dataclass
class _Manifest:
    name: str = "someserver"
    label: str = "Some Server"
    kind: str = "mcp"


class Acl:
    def __init__(self, owner=True, verdict="allow"):
        self._owner, self._verdict = owner, verdict

    async def check(self, user_id, chat_type, profile=None):
        return self._verdict

    async def is_owner(self, user_id):
        return self._owner


class Secrets:
    """The `SecretStorePort` by shape only — async since Wave 3a, because the real
    provider seals the value and writes a row, and `owner_id`-taking since
    per-user connectors, because the row is keyed by the person it belongs to."""

    def __init__(self, reject=False):
        self.stored = {}
        self.owners = []
        self._reject = reject

    async def put(self, name, value, *, owner_id):
        if self._reject:
            raise SecretNameError("not an environment variable name")
        self.owners.append(owner_id)
        self.stored[name] = value


class Approvals:
    """The `ApprovalPort` by shape only — async and `home`-free since Wave 3a: the
    draft queue and the snapshots are tables now, so `extensions.ApprovalService`
    satisfies this port directly instead of through a home-bound adapter."""

    def __init__(self, started=None, confirmed=None):
        self.cleared = []
        self._started = started
        self._confirmed = confirmed

    async def begin(self, name):
        return self._started

    async def confirm(self, name, token):
        return self._confirmed

    async def clear(self, kind, name):
        self.cleared.append((kind, name))


class Catalog:
    def __init__(self, repo=False, visible=True):
        self._repo, self._visible = repo, visible

    def is_repo_entry(self, name):
        return self._repo

    def get(self, name):
        return _Manifest() if self._visible else None


class Writer:
    def __init__(self):
        self.promoted = []

    def promote(self, m, raw):
        self.promoted.append((m.name, raw))


class Runner:
    def __init__(self):
        self.turns, self.resets = [], []

    async def run_turn(self, key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        self.turns.append((key, text))
        return "reply"

    async def reset(self, key):
        self.resets.append(key)


def event(text="", user_id=1):
    return MessageEvent(chat_type="private", chat_id=1, user_id=user_id, text=text,
                        mentioned=False, message_id=99)


def build(*, acl=None, secrets=None, approvals=None, catalog=None, writer=None,
          runner=None):
    runner = runner or Runner()
    service = CommandService(acl=acl or Acl(), secrets=secrets or Secrets(),
                             approvals=approvals or Approvals(),
                             catalog=catalog or Catalog(), runner=runner,
                             writer=writer or Writer())
    return service, runner


async def test_a_stored_secret_is_never_echoed_back():
    secrets = Secrets()
    service, _ = build(secrets=secrets)
    reply = await service.handle_secret("EXAMPLE_API_KEY=abc123", event())
    assert secrets.stored == {"EXAMPLE_API_KEY": "abc123"}
    assert "EXAMPLE_API_KEY" in reply and "abc123" not in reply


async def test_the_secret_is_sealed_for_whoever_sent_it():
    """`extension_secrets` is keyed by `(owner_id, name)`. The command is
    owner-gated, so the id it passes is the owner's — but it passes the sender's
    id rather than a constant bound in the composition root, so the row the
    command writes is the row that sender's own turns compose."""
    secrets = Secrets()
    service, _ = build(secrets=secrets)
    await service.handle_secret("EXAMPLE_API_KEY=abc123", event(user_id=1))
    assert secrets.owners == [1]


async def test_the_carrying_message_is_deleted_even_for_a_non_owner():
    deleted = []

    async def delete_message():
        deleted.append(True)

    service, _ = build(acl=Acl(owner=False))
    reply = await service.handle_secret("K=v", event(), delete_message=delete_message)
    assert deleted == [True] and "owner" in reply.lower()


async def test_a_failing_delete_never_stops_the_store():
    async def delete_message():
        raise RuntimeError("message already gone")

    secrets = Secrets()
    service, _ = build(secrets=secrets)
    await service.handle_secret("K=v", event(), delete_message=delete_message)
    assert secrets.stored == {"K": "v"}


@pytest.mark.parametrize("rest", ["", "NOVALUE", "lower=x"])
async def test_a_malformed_secret_explains_itself(rest):
    # "lower=x" is rejected by the store, the other two never reach it.
    service, _ = build(secrets=Secrets(reject=(rest == "lower=x")))
    assert "NAME=value" in await service.handle_secret(rest, event())


async def test_a_repo_entry_cannot_be_shadowed_from_chat():
    service, _ = build(catalog=Catalog(repo=True))
    reply = await service.handle_approve("someserver", event(), session_key="k")
    assert "cannot be overridden" in reply


async def test_step_one_shows_the_surface_and_promotes_nothing():
    writer = Writer()
    approvals = Approvals(started=(_Manifest(), "command: npx …", "abcd1234"))
    service, runner = build(approvals=approvals, writer=writer)
    reply = await service.handle_approve("someserver", event(), session_key="k")
    assert "command: npx" in reply and "/approve someserver abcd1234" in reply
    assert writer.promoted == [] and runner.resets == []


async def test_a_confirmed_approve_promotes_clears_and_resets_the_lane():
    writer = Writer()
    approvals = Approvals(confirmed=(_Manifest(), "raw yaml"))
    service, runner = build(approvals=approvals, writer=writer)
    reply = await service.handle_approve("someserver tok", event(), session_key="tg:private:1")
    assert writer.promoted == [("someserver", "raw yaml")]
    assert approvals.cleared == [("mcp", "someserver")]
    assert runner.resets == ["tg:private:1"]
    assert "starts fresh" in reply


async def test_a_write_the_catalog_cannot_see_is_reported_not_celebrated():
    """P5 — the agent must not claim work it did not do. Nothing is cleared and
    the lane is not reset, so a retry is free."""
    approvals = Approvals(confirmed=(_Manifest(), "raw yaml"))
    service, runner = build(approvals=approvals, catalog=Catalog(visible=False))
    reply = await service.handle_approve("someserver tok", event(), session_key="k")
    assert "NOT installable" in reply and "is now in your catalog" not in reply
    assert approvals.cleared == [] and runner.resets == []


async def test_a_stale_token_neither_promotes_nor_resets():
    writer = Writer()
    service, runner = build(approvals=Approvals(confirmed=None), writer=writer)
    reply = await service.handle_approve("someserver 0000", event(), session_key="k")
    assert "no longer matches" in reply
    assert writer.promoted == [] and runner.resets == []


async def test_neither_command_ever_reaches_the_runner():
    runner = Runner()
    service, _ = build(runner=runner)
    router = MessageRouter(Config(), runner=runner, acl=Acl(), commands=service)
    await router.handle(event("/secret K=v"))
    await router.handle(event("/approve someserver"))
    assert runner.turns == []

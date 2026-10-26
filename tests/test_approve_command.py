"""`/approve` from a non-owner. The other `/approve` branches — the two steps,
the P5 catalog check, the lane reset and the stale token — are pinned in
`tests/modules/telegram/test_telegram_commands.py`; the owner gate on `/approve`
is the one that file does not exercise, and it is a security branch."""
from dataclasses import dataclass

from claude_hermes.modules.telegram.application.services.commands import CommandService
from claude_hermes.modules.telegram.domain.models import MessageEvent


@dataclass
class _Manifest:
    name: str = "someserver"
    label: str = "Some Server"
    kind: str = "mcp"


class Acl:
    def __init__(self, owner=True):
        self._owner = owner

    async def check(self, user_id, chat_type, profile=None):
        return "allow"

    async def is_owner(self, user_id):
        return self._owner


class Approvals:
    def __init__(self):
        self.begun = []

    async def begin(self, name):
        self.begun.append(name)
        return (_Manifest(), "command: npx …", "abcd1234")

    async def confirm(self, name, token):
        raise AssertionError("a refused approve must not reach confirm")

    async def clear(self, kind, name):
        raise AssertionError("a refused approve clears nothing")


class Catalog:
    def is_repo_entry(self, name):
        return False

    def get(self, name):
        return _Manifest()


class SpyWriter:
    def __init__(self):
        self.promoted = []

    def promote(self, m, raw):
        self.promoted.append((m.name, raw))


class SpyRunner:
    def __init__(self):
        self.turns, self.resets = [], []

    async def run_turn(self, key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        self.turns.append(text)
        return "reply"

    async def reset(self, key):
        self.resets.append(key)


def event(text, user_id=1):
    return MessageEvent(chat_type="private", chat_id=1, user_id=user_id, text=text,
                        mentioned=False, thread_id=None, message_id=99)


async def test_a_non_owner_cannot_approve():
    writer, approvals, runner = SpyWriter(), Approvals(), SpyRunner()
    service = CommandService(acl=Acl(owner=False), secrets=None, approvals=approvals,
                             catalog=Catalog(), runner=runner, writer=writer)
    reply = await service.handle_approve("someserver", event("/approve someserver", user_id=2),
                                         session_key="tg:private:1")
    assert writer.promoted == []
    assert approvals.begun == [] and runner.resets == []   # refused before step one
    assert "owner" in reply.lower()

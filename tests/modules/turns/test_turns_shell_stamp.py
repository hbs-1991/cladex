"""The lazy reset (0009) over the REAL session store.

`test_turns_service.py` proves the runner's arithmetic with a fake `SessionLane`
that hands back whatever dict the test wrote. That cannot catch the one mistake
that matters here: the key the runner READS off the row (`row.get("shell_enabled")`)
not being the column the writer STAMPS (`bind`/`touch` → `SessionRepository`). So
this file drives `TurnService` over `SessionRouter(SessionRepository(engine))` on
the test database, with only the CLI, the grants and the bus faked.
"""
from __future__ import annotations

from datetime import datetime, timezone

from conftest import ensure_users

from claude_hermes.core.config import Config
from claude_hermes.modules.sessions.application.services import SessionRouter
from claude_hermes.modules.sessions.infrastructure.repositories import SessionRepository
from claude_hermes.modules.turns.domain.policies import SHELL_CHANGED_NOTICE

from tests.modules.turns.test_turns_service import (
    USER_LANE,
    FakeExecutor,
    FakeGrants,
    Grant,
    UserWorkspace,
    blocks,
    make_service,
    user_turn,
)


class Sequential(FakeExecutor):
    """A fresh SDK session id for every turn that did not resume — so "was this
    session reset" is readable off the row, not only off the call."""

    def __init__(self):
        super().__init__()
        self._n = 0

    async def execute(self, **kw):
        if kw["resume_id"] is None:
            self._n += 1
            self._session_id = f"sdk-{self._n}"
        else:
            self._session_id = kw["resume_id"]
        return await super().execute(**kw)


async def over_the_real_store(engine, grants):
    await ensure_users(engine, 2)
    repo = SessionRepository(engine)
    ex = Sequential()
    svc, parts = make_service(sessions=SessionRouter(repo, Config()), executor=ex,
                              grants=grants)
    return svc, parts, repo, ex


async def test_the_stamp_the_writer_writes_is_the_one_the_reader_compares(engine):
    grants = FakeGrants(shell=False)
    svc, parts, repo, ex = await over_the_real_store(engine, grants)

    await user_turn(svc)                                     # a fresh lane: bound
    row = await repo.get(USER_LANE)
    assert (row["sdk_session_id"], row["shell_enabled"]) == ("sdk-1", False)

    await user_turn(svc)                                     # same grant: resumed
    assert ex.calls[-1]["resume"] == "sdk-1"
    assert (await repo.get(USER_LANE))["shell_enabled"] is False
    assert blocks(parts) == []

    grants.answer = Grant(shell=True)                        # the owner grants a shell
    reply = await user_turn(svc)
    assert ex.calls[-1]["resume"] is None and ex.calls[-1]["shell"] is True
    assert reply == f"{SHELL_CHANGED_NOTICE}\n\nok"
    assert blocks(parts) == [SHELL_CHANGED_NOTICE]
    row = await repo.get(USER_LANE)
    assert (row["sdk_session_id"], row["shell_enabled"]) == ("sdk-2", True)

    await user_turn(svc)                                     # and it sticks
    assert ex.calls[-1]["resume"] == "sdk-2"
    assert blocks(parts) == [SHELL_CHANGED_NOTICE]           # said once, not again


async def test_a_pre_0009_session_is_resumed_or_reset_by_the_old_rule(engine):
    """A row with a NULL stamp — what every session bound before the upgrade has —
    resumes when the user still inherits the default, and is stamped on the way;
    it resets when the owner has since chosen otherwise."""
    grants = FakeGrants(shell=False, default_shell=False)
    svc, parts, repo, ex = await over_the_real_store(engine, grants)
    now = datetime.now(timezone.utc).isoformat()
    await repo.upsert(USER_LANE, "sdk-old", "FROZEN", now, owner_id=2)  # the pre-0009 way
    assert (await repo.get(USER_LANE))["shell_enabled"] is None

    await user_turn(svc)
    assert ex.calls[-1]["resume"] == "sdk-old" and blocks(parts) == []
    assert (await repo.get(USER_LANE))["shell_enabled"] is False     # stamped now

    other = "web:2:y"
    await repo.upsert(other, "sdk-older", "FROZEN", now, owner_id=2)
    grants.answer = Grant(shell=True)                        # an explicit grant since
    reply = await svc.run_turn(other, "hi", user_id=2, channel="web",
                               workspace=UserWorkspace())
    assert ex.calls[-1]["resume"] is None
    assert reply.startswith(SHELL_CHANGED_NOTICE)
    row = await repo.get(other)
    assert row["sdk_session_id"] != "sdk-older" and row["shell_enabled"] is True

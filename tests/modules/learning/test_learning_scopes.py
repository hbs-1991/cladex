"""A learning scope per user (Wave 4b, D1/D2), through the real wiring.

What these pin is the isolation the registry exists for: two users get two
sidecars over two trees and two sets of `meta` rows, the owner's rows keep their
seven legacy names byte-for-byte, and `for_user(0)` IS the owner — one cached
object, not a second sidecar over the same `.usage.json`. And who is learned
from at all: the sweep and the queued-review check follow each user's own
learning grant, asked per call.

No database, no SDK: the wiring is handed fakes for everything that crosses a
module boundary, which is also what proves the scope reaches sessions, memory
and identity only through the arguments `main.build_app()` binds.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from claude_hermes.core.config import Config
from claude_hermes.core.app_context import SkillAdminTool
from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.learning import wiring
from claude_hermes.modules.learning.application.scopes import (
    LearningScope,
    LearningScopes,
)
from claude_hermes.modules.learning.domain.constants import (
    LAST_RUN_META_KEY,
    PAUSED_META_KEY,
    REVIEW_GATE_META_KEY,
    WIKI_NUDGE_META_KEY,
)
from claude_hermes.modules.learning.infrastructure.scoped_meta import ScopedMeta

NOW = "2026-09-18T10:00:00+00:00"


class FakeMeta:
    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


class FakeWorkspaces:
    """`WorkspaceResolver` with no table: the owner's tree is `home`, everybody
    else's is `users/<id>`, exactly the layout `Workspace.for_user` makes."""

    def __init__(self, home, owner: int):
        self._home = home
        self._owner = owner
        self.resolved: list[int] = []

    @property
    def home(self):
        return self._home

    async def owner_id(self) -> int:
        return self._owner

    async def for_user(self, user_id: int) -> Workspace:
        self.resolved.append(user_id)
        ws = Workspace.for_user(self._home,
                                "" if user_id == self._owner else f"users/{user_id}")
        ws.skills_dir.mkdir(parents=True, exist_ok=True)
        ws.logs_dir.mkdir(parents=True, exist_ok=True)
        return ws


class FakeActivity:
    def __init__(self, user_id):
        self.user_id = user_id

    async def last_message_ts(self):
        return None


class FakeLint:
    def __init__(self, workspace, user_id=0):
        self.workspace = workspace
        self.user_id = user_id

    async def lint(self, dry_run=False):
        return None


class FakeGrants:
    """identity's effective learning grant by shape — `learning_of(uid)` — read
    per call, so a test flips `answers` and the next ask sees it (no restart,
    exactly what Settings → Users relies on). An exception as an answer is what
    that lookup raises. `asked` is every id the registry asked about."""

    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.asked: list[int] = []

    async def __call__(self, user_id: int) -> bool:
        self.asked.append(user_id)
        answer = self.answers.get(user_id, False)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def build(tmp_path, *, owner=1, users=(), learning=None, meta=None):
    cfg = Config()
    grants = FakeGrants(learning)
    meta = meta if meta is not None else FakeMeta()
    workspaces = FakeWorkspaces(tmp_path, owner)
    activities: dict[int, FakeActivity] = {}

    def activity_for(uid):
        activities[uid] = FakeActivity(uid)
        return activities[uid]

    async def user_ids():
        return list(users)

    scopes = wiring.build_scopes(
        cfg, workspaces=workspaces, meta=meta, sessions=None,
        activity_for=activity_for, wiki_lint_for=FakeLint, fork=None,
        snapshots=SimpleNamespace(), users=user_ids, learning_of=grants,
        review_slot=None)
    return scopes, meta, workspaces, activities, grants


# ----------------------------------------------------------- scoped meta ----

async def test_scoped_meta_suffixes_every_key_and_the_empty_suffix_is_identity():
    meta = FakeMeta()
    await ScopedMeta(meta, ":7").set(LAST_RUN_META_KEY, NOW)
    await ScopedMeta(meta, "").set(LAST_RUN_META_KEY, "owner")
    assert meta.rows == {f"{LAST_RUN_META_KEY}:7": NOW, LAST_RUN_META_KEY: "owner"}
    assert await ScopedMeta(meta, ":7").get(LAST_RUN_META_KEY) == NOW
    assert await ScopedMeta(meta, "").get(LAST_RUN_META_KEY) == "owner"


# --------------------------------------------------------------- scopes -----

async def test_two_users_get_two_sidecars_and_two_meta_suffixes(tmp_path):
    """The failure this guards: one sidecar over one `.usage.json` counting
    everybody's skill uses, and one `turns_since_wiki_review` row nudging the
    owner about a stranger's conversation."""
    scopes, meta, _, _, _ = build(tmp_path, owner=1, users=[1, 7])
    owner, seven = await scopes.for_user(1), await scopes.for_user(7)

    assert isinstance(owner, LearningScope) and isinstance(seven, LearningScope)
    assert owner.is_owner and not seven.is_owner
    assert owner.skills_dir == tmp_path / "agent_home" / ".claude" / "skills"
    assert seven.skills_dir == tmp_path / "users" / "7" / "agent_home" / ".claude" / "skills"
    assert seven.logs_dir == tmp_path / "users" / "7" / "logs"
    assert owner.sidecar is not seven.sidecar

    seven.sidecar.bump_use("deploy", NOW)
    assert owner.sidecar.get("deploy") is None            # their count, not mine

    await seven.nudges.observe_turn(False, False, 0)
    await owner.nudges.observe_turn(False, False, 0)
    assert meta.rows[f"{WIKI_NUDGE_META_KEY}:7"] == "1"
    assert meta.rows[WIKI_NUDGE_META_KEY] == "1"           # the legacy row, unsuffixed
    assert owner.meta.suffix == "" and seven.meta.suffix == ":7"


async def test_the_owner_keys_are_the_legacy_keys(tmp_path):
    """No migration: the seven rows an operator and `skill_admin status` know by
    literal name are the owner scope's rows."""
    scopes, meta, _, _, _ = build(tmp_path, owner=1)
    owner = await scopes.owner()
    await owner.gate.claim()
    await owner.meta.set(LAST_RUN_META_KEY, NOW)
    assert set(meta.rows) == {REVIEW_GATE_META_KEY, LAST_RUN_META_KEY}


async def test_for_user_zero_is_the_owners_object(tmp_path):
    """`0` is what every legacy caller and every 4a payload carries. Two scopes
    for one tree would be two sidecars overwriting each other's counts."""
    scopes, _, workspaces, _, _ = build(tmp_path, owner=1)
    a = await scopes.for_user(0)
    b = await scopes.for_user(1)
    c = await scopes.owner()
    assert a is b is c
    assert workspaces.resolved == [1]                       # resolved once, cached


async def test_an_unclaimed_deploy_builds_an_uncached_owner_scope(tmp_path):
    """Owner 0: the scope is over `Workspace.owner(home)` and not cached, for the
    same reason the resolver does not cache a zero owner — the claim happens
    later and a cached scope would outlive it."""
    scopes, _, workspaces, _, _ = build(tmp_path, owner=0)
    a = await scopes.for_user(0)
    b = await scopes.for_user(0)
    assert a.user_id == 0 and a.is_owner and a.meta.suffix == ""
    assert a.workspace == Workspace.owner(tmp_path)
    assert a is not b
    assert workspaces.resolved == []
    assert await scopes.user_ids() == [0]


async def test_user_ids_lists_the_owner_first_then_each_granted_user(tmp_path):
    """The per-user filter: the owner leads unconditionally (and is never asked
    — their pass does not hang on a lookup), and every other user follows iff
    THEIR grant is on. The owner is a `users` row too, so the list is
    deduplicated, and a duplicate id is asked about once."""
    scopes, _, _, _, grants = build(tmp_path, owner=1, users=[7, 1, 9, 7, 11],
                                    learning={7: True, 9: False, 11: True})
    assert await scopes.user_ids() == [1, 7, 11]
    assert grants.asked == [7, 9, 11]


async def test_user_ids_reads_the_grant_at_call_time(tmp_path):
    """A flip in Settings → Users reaches the next sweep with no rebuild: the
    registry caches scopes, never answers."""
    scopes, _, _, _, grants = build(tmp_path, owner=1, users=[7, 9])
    assert await scopes.user_ids() == [1]                   # nobody granted: 4a
    grants.answers[9] = True
    assert await scopes.user_ids() == [1, 9]
    grants.answers[9] = False                               # withdrawn again
    grants.answers[7] = True
    assert await scopes.user_ids() == [1, 7]


async def test_a_failing_lookup_leaves_that_user_out_and_the_sweep_goes_on(tmp_path,
                                                                          caplog):
    """One bad row must not skip the owner's pass or anybody else's: the id
    whose lookup raised sits this sweep out, logged with the id, and the ones
    around it are still listed."""
    scopes, _, _, _, grants = build(
        tmp_path, owner=1, users=[7, 9, 11],
        learning={7: True, 9: RuntimeError("db blip"), 11: True})
    with caplog.at_level(logging.ERROR):
        assert await scopes.user_ids() == [1, 7, 11]
    [failure] = [r for r in caplog.records if "grant lookup failed" in r.getMessage()]
    assert "9" in failure.getMessage()
    assert grants.asked == [7, 9, 11]


async def test_an_unclaimed_deploy_still_leads_with_zero(tmp_path):
    """Owner `0`: the sweep's first pass is the one tree there is, and granted
    users follow it exactly as they would behind a claimed owner."""
    scopes, _, _, _, _ = build(tmp_path, owner=0, users=[7, 9],
                               learning={7: True})
    assert await scopes.user_ids() == [0, 7]


async def test_observes_the_owner_and_zero_without_a_lookup(tmp_path):
    """The owner's conversations are always learned from; `0` IS the owner.
    Neither costs a query — nor could one refuse them."""
    scopes, _, _, _, grants = build(tmp_path, owner=1, learning={1: False})
    assert await scopes.observes(1) is True
    assert await scopes.observes(0) is True
    assert grants.asked == []


async def test_observes_a_granted_and_a_withdrawn_user_at_call_time(tmp_path):
    """What the queued-review check asks: THIS user's grant, now."""
    scopes, _, _, _, grants = build(tmp_path, owner=1, learning={7: True, 9: False})
    assert await scopes.observes(7) is True
    assert await scopes.observes(9) is False
    grants.answers[7] = False                               # withdrawn after the nudge
    assert await scopes.observes(7) is False
    assert grants.asked == [7, 9, 7]


async def test_observes_lets_a_failed_lookup_raise(tmp_path):
    """Unlike the sweep, `observes` does not decide what a failure means: its
    caller holds a review gate and has to hand it back either way."""
    scopes, _, _, _, _ = build(tmp_path, owner=1, learning={7: RuntimeError("db blip")})
    with pytest.raises(RuntimeError):
        await scopes.observes(7)


async def test_each_scope_gets_its_own_activity_and_lint(tmp_path):
    """The quiet-hours check and the lint pass are per user (B-7, D4): a
    neighbour chatting at 3am must not hold this user's pass off, and the lint
    must run over THIS wiki."""
    scopes, _, _, activities, _ = build(tmp_path, owner=1, users=[7], learning={7: True})
    seven = await scopes.for_user(7)
    assert activities[7].user_id == 7 and 1 not in activities
    assert seven.curator._wiki_lint.workspace == seven.workspace
    assert seven.review.workspace == seven.workspace


async def test_a_non_owners_skill_admin_status_reads_their_suffixed_rows(tmp_path):
    """`status` through user 7's scope reads `curator_last_run_at:7` and
    `curator_paused:7`, never the owner's rows."""
    meta = FakeMeta(**{LAST_RUN_META_KEY: "owner-ran", PAUSED_META_KEY: "1",
                       f"{LAST_RUN_META_KEY}:7": NOW})
    scopes, _, _, _, _ = build(tmp_path, owner=1, users=[7], meta=meta)
    seven = await scopes.for_user(7)
    out = await seven.admin.handle("status")
    assert f"Last curator run: {NOW}" in out
    assert "Paused: no" in out                              # theirs, not the operator's


async def test_the_skill_admin_tool_resolves_the_lane_owner(tmp_path):
    """The tool administers the LANE OWNER's library: `ctx.owner_id` picks the
    scope, no ctx means the deploy owner."""
    scopes, _, _, _, _ = build(tmp_path, owner=1, users=[7])
    seven = await scopes.for_user(7)
    seven.sidecar.mark_created("theirs", "agent", NOW)
    (seven.skills_dir / "theirs").mkdir()
    (seven.skills_dir / "theirs" / "SKILL.md").write_text(
        "---\nname: theirs\ndescription: T.\n---\n")
    tool = SkillAdminTool(scopes)
    assert "theirs" in await tool.handle({"action": "list"}, ctx=SimpleNamespace(owner_id=7))
    assert await tool.handle({"action": "list"}) == "No skills tracked."
    assert await tool.handle({"action": "list"}, ctx=SimpleNamespace(owner_id=1)) == "No skills tracked."


async def test_the_registry_is_the_contract_phase_t_builds_against(tmp_path):
    scopes, _, _, _, _ = build(tmp_path, owner=1)
    assert isinstance(scopes, LearningScopes)
    scope = await scopes.for_user(0)
    for name in ("user_id", "workspace", "is_owner", "skills_dir", "logs_dir", "meta",
                 "sidecar", "archive", "library", "admin", "nudges", "gate", "review",
                 "curator"):
        assert hasattr(scope, name), name

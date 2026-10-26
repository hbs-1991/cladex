"""The curator pass and the consolidation pass, over their ports.

Rewritten in Wave 2: `curator_pass` is `CuratorService.run_pass()` and the things
the old test monkeypatched — the clock, the SDK `query`, `run_wiki_lint` — are
constructor arguments now. The gates, the report, the dry-run semantics and the
"the code disposes" eligibility rules are asserted exactly as before.

The fakes are the point of the rewrite: this module never touches a database, a
wiki directory or the SDK, which is what proves learning reads the sessions table
and the wiki only through ports.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from claude_hermes.core.config import Config
from claude_hermes.modules.learning.application.services.consolidation import (
    ConsolidationService,
)
from claude_hermes.modules.learning.application.services.curator import CuratorService
from claude_hermes.modules.learning.domain.constants import (
    LAST_RUN_META_KEY,
    PAUSED_META_KEY,
)
from claude_hermes.modules.learning.infrastructure.report import ReportWriter
from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar
from claude_hermes.core import snapshots

NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


class FakeMeta:
    """`MetaStore` in a dict. The keys are the real ones — a rename here would not
    fail, which is why `domain/constants.py` owns them."""

    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


class FakeGate:
    """`ReviewGate`. In production an expiry row in `meta`; the curator only ever
    asks the one question."""

    def __init__(self, running=False):
        self.claimed = running

    async def running(self):
        return self.claimed

    async def claim(self):
        self.claimed = True

    async def release(self):
        self.claimed = False


class FakeActivity:
    """`ActivityPort`. None means nothing was ever recorded → infinitely idle."""

    def __init__(self, ts=None):
        self.ts = ts

    async def last_message_ts(self):
        return self.ts


class FakeFork:
    """`ForkRunner`. `final` is the model's last message; `boom` makes the fork fail."""

    def __init__(self, final=None, boom=False):
        self.final = final
        self.boom = boom
        self.calls = []
        self.workspaces = []

    async def run_consolidation(self, *, prompt, skills_dir, workspace=None):
        self.calls.append(prompt)
        self.workspaces.append(workspace)
        if self.boom:
            raise RuntimeError("fork died")
        return self.final

    async def run_review(self, **kw):  # pragma: no cover - not used here
        raise AssertionError("the curator never runs a review fork")


class FakeWikiLint:
    """`WikiLintPort`, owned by memory in production."""

    def __init__(self, result="3 orphans, 1 contradiction"):
        self.result = result
        self.calls = []

    async def lint(self, dry_run=False):
        self.calls.append(dry_run)
        return self.result


class ExplodingWikiLint:
    async def lint(self, dry_run=False):
        raise AssertionError("lint must not run when wiki_lint_enabled is false")


def make_skill(skills_dir: Path, name: str) -> Path:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: X.\n---\n")
    return d


def make_home(tmp_path):
    home = tmp_path / "home"
    skills = home / "agent_home" / ".claude" / "skills"
    skills.mkdir(parents=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    return home, skills, UsageSidecar(skills)


def make_service(cfg, home, skills, sidecar, *, meta=None, activity=None,
                 fork=None, wiki_lint=None, gate=None, now=NOW):
    archive = SkillArchive(skills, sidecar)
    return CuratorService(
        # `logs_dir`, not `home` (4b, S-1): for the owner the same directory.
        cfg=cfg, logs_dir=home / "logs", skills_dir=skills,
        wiki_root=home / "agent_home" / "wiki",
        usage=sidecar, meta=meta or FakeMeta(), activity=activity or FakeActivity(),
        snapshots=snapshots, archive=archive,
        consolidation=ConsolidationService(
            skills_dir=skills, usage=sidecar, archive=archive,
            fork=fork or FakeFork(),
        ),
        reports=ReportWriter(home / "logs"), wiki_lint=wiki_lint, gate=gate,
        now=lambda: now,
    )


# ---------------------------------------------------------------- gates ----

async def test_disabled_gate_skips(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    svc = make_service(Config(curator_enabled=False), home, skills, sidecar)
    assert await svc.run_pass() == {"ran": False, "reason": "disabled"}


async def test_paused_gate_skips(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    svc = make_service(Config(), home, skills, sidecar,
                       meta=FakeMeta(**{PAUSED_META_KEY: "1"}))
    assert await svc.run_pass() == {"ran": False, "reason": "paused"}


async def test_a_review_in_flight_blocks_the_whole_pass(tmp_path):
    """Not just consolidation: a curator write must not race a review-fork write.

    Wave 3a reads this from a `ReviewGate` rather than from an in-process task
    handle, so it still holds when the review runs in a worker process.
    """
    home, skills, sidecar = make_home(tmp_path)
    svc = make_service(Config(), home, skills, sidecar, gate=FakeGate(running=True))
    assert await svc.run_pass() == {"ran": False, "reason": "review running"}


async def test_an_expired_gate_does_not_block(tmp_path):
    """The lease is the dead-worker guard: once it is free the pass runs."""
    home, skills, sidecar = make_home(tmp_path)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(hours=200)).isoformat()})
    svc = make_service(Config(), home, skills, sidecar, meta=meta, gate=FakeGate())
    assert (await svc.run_pass())["ran"] is True


async def test_first_run_defers_and_seeds(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    meta = FakeMeta()
    result = await make_service(Config(), home, skills, sidecar, meta=meta).run_pass()
    assert result["ran"] is False
    assert result["reason"] == "first-run deferral"
    assert meta.rows[LAST_RUN_META_KEY] == NOW.isoformat()


async def test_interval_not_elapsed_gate(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(hours=1)).isoformat()})
    result = await make_service(Config(curator_interval_hours=168.0), home, skills,
                                sidecar, meta=meta).run_pass()
    assert result["ran"] is False
    assert result["reason"] == "interval not elapsed"


async def test_not_idle_gate(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(hours=200)).isoformat()})
    result = await make_service(Config(curator_min_idle_hours=2.0), home, skills,
                                sidecar, meta=meta,
                                activity=FakeActivity(NOW.isoformat())).run_pass()
    assert result["ran"] is False
    assert result["reason"] == "not idle"


async def test_no_message_ever_recorded_reads_as_infinitely_idle(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(hours=200)).isoformat()})
    result = await make_service(Config(curator_min_idle_hours=48.0), home, skills,
                                sidecar, meta=meta,
                                activity=FakeActivity(None)).run_pass()
    assert result["ran"] is True


async def test_gates_pass_runs_snapshot_ages_and_reports(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(hours=200)).isoformat()})

    make_skill(skills, "ancient")
    sidecar.mark_created("ancient", "agent", (NOW - timedelta(days=200)).isoformat())
    sidecar.set_state("ancient", "stale")

    result = await make_service(Config(), home, skills, sidecar, meta=meta).run_pass()

    assert result["ran"] is True
    assert sidecar.get("ancient")["state"] == "archived"
    assert (home / "logs" / "skills_backups").is_dir()
    assert result["snapshot_id"] is not None
    report_path = Path(result["report_path"])
    assert report_path.is_file()
    # Under `<logs_dir>/curator/` — byte-identical to the pre-4b `home/logs`.
    assert report_path.parent.parent == home / "logs" / "curator"
    assert (report_path.parent / "REPORT.md").is_file()
    assert "## Recovery" in (report_path.parent / "REPORT.md").read_text(encoding="utf-8")
    assert meta.rows[LAST_RUN_META_KEY] == NOW.isoformat()


async def test_a_non_owners_pass_snapshots_and_reports_under_their_logs(tmp_path):
    """The scope hands the curator `users/<id>/logs`: their snapshots and their
    reports land in the one `logs/` their read guard admits, not the owner's."""
    home, skills, sidecar = make_home(tmp_path)
    logs = tmp_path / "users" / "7" / "logs"
    logs.mkdir(parents=True)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(hours=200)).isoformat()})
    archive = SkillArchive(skills, sidecar)
    svc = CuratorService(
        cfg=Config(), logs_dir=logs, skills_dir=skills,
        wiki_root=home / "agent_home" / "wiki", usage=sidecar, meta=meta,
        activity=FakeActivity(), snapshots=snapshots, archive=archive,
        consolidation=ConsolidationService(skills_dir=skills, usage=sidecar,
                                           archive=archive, fork=FakeFork()),
        reports=ReportWriter(logs), now=lambda: NOW)
    result = await svc.run_pass()
    assert result["ran"] is True
    assert (logs / "skills_backups").is_dir()
    assert Path(result["report_path"]).parent.parent == logs / "curator"
    assert not (home / "logs" / "skills_backups").exists()


async def test_dry_run_archives_nothing_and_does_not_advance_last_run(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    old_last_run = (NOW - timedelta(hours=200)).isoformat()
    meta = FakeMeta(**{LAST_RUN_META_KEY: old_last_run})

    make_skill(skills, "ancient")
    sidecar.mark_created("ancient", "agent", (NOW - timedelta(days=200)).isoformat())
    sidecar.set_state("ancient", "stale")

    result = await make_service(Config(curator_dry_run=True), home, skills, sidecar,
                                meta=meta).run_pass()

    assert result["ran"] is True
    assert sidecar.get("ancient")["state"] == "stale"          # untouched
    assert (skills / "ancient").is_dir()                        # not archived
    assert not (home / "logs" / "skills_backups").exists()      # no snapshot taken
    assert meta.rows[LAST_RUN_META_KEY] == old_last_run


# --------------------------------------------------------------- wiki lint --

def _ready(tmp_path, **cfg_kw):
    """A home whose curator gates are all open."""
    home, skills, sidecar = make_home(tmp_path)
    (home / "agent_home" / "wiki").mkdir(parents=True)
    meta = FakeMeta(**{LAST_RUN_META_KEY: (NOW - timedelta(days=30)).isoformat()})
    cfg = Config(curator_interval_hours=1.0, curator_min_idle_hours=0.0, **cfg_kw)
    return cfg, home, skills, sidecar, meta


async def test_pass_snapshots_the_wiki_and_runs_lint(tmp_path):
    cfg, home, skills, sidecar, meta = _ready(tmp_path, wiki_lint_enabled=True)
    lint = FakeWikiLint()
    result = await make_service(cfg, home, skills, sidecar, meta=meta,
                                wiki_lint=lint).run_pass()
    assert result["ran"] is True
    assert result["wiki_lint"] == "3 orphans, 1 contradiction"
    assert result["wiki_snapshot_id"]                   # snapshotted before the pass
    assert lint.calls == [False]                        # not a dry run


async def test_a_dry_run_pass_lints_read_only(tmp_path):
    cfg, home, skills, sidecar, meta = _ready(tmp_path, wiki_lint_enabled=True,
                                              curator_dry_run=True)
    lint = FakeWikiLint()
    result = await make_service(cfg, home, skills, sidecar, meta=meta,
                                wiki_lint=lint).run_pass()
    assert lint.calls == [True]
    assert result["wiki_snapshot_id"] is None           # nothing to roll back from


async def test_lint_is_skipped_when_disabled(tmp_path):
    cfg, home, skills, sidecar, meta = _ready(tmp_path, wiki_lint_enabled=False)
    result = await make_service(cfg, home, skills, sidecar, meta=meta,
                                wiki_lint=ExplodingWikiLint()).run_pass()
    assert result["ran"] is True
    assert result["wiki_lint"] is None


# ------------------------------------------------------- run_consolidation --

def consolidation(skills, sidecar, fork, workspace=None):
    return ConsolidationService(skills_dir=skills, usage=sidecar,
                                archive=SkillArchive(skills, sidecar), fork=fork,
                                workspace=workspace)


async def test_consolidation_forks_in_its_scopes_workspace(tmp_path):
    """The fork runs in the SCOPE's tree (D4): a non-owner's consolidation
    rewrites their library from their `agent_home`, never from the owner's."""
    home, skills, sidecar = make_home(tmp_path)
    fork = FakeFork("nothing to merge")
    ws = object()
    await consolidation(skills, sidecar, fork, workspace=ws).run()
    assert fork.workspaces == [ws]
    await consolidation(skills, sidecar, fork).run()
    assert fork.workspaces == [ws, None]                  # legacy: the owner's


async def test_run_consolidation_happy_path(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    make_skill(skills, "old-skill")
    sidecar.mark_created("old-skill", "agent", NOW.isoformat())
    make_skill(skills, "umbrella-skill")
    sidecar.mark_created("umbrella-skill", "agent", NOW.isoformat())

    fork = FakeFork(
        "Merged old-skill into umbrella-skill.\n\n```yaml\n"
        "consolidations:\n"
        "  - from: old-skill\n"
        "    into: umbrella-skill\n"
        "    reason: same class of task\n"
        "prunings: []\n"
        "```\n"
    )
    out = await consolidation(skills, sidecar, fork).run()

    assert out["consolidated"] == ["old-skill"]
    assert out["pruned"] == []
    assert not (skills / "old-skill").exists()
    assert (skills / ".archive" / "old-skill").is_dir()
    assert sidecar.get("old-skill")["state"] == "archived"
    # The prompt carries the candidate table, and only agent-created rows.
    assert "old-skill" in fork.calls[0] and "umbrella-skill" in fork.calls[0]


async def test_run_consolidation_missing_umbrella_leaves_source(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    make_skill(skills, "old-skill")
    sidecar.mark_created("old-skill", "agent", NOW.isoformat())

    fork = FakeFork(
        "```yaml\n"
        "consolidations:\n"
        "  - from: old-skill\n"
        "    into: nonexistent-umbrella\n"
        "    reason: x\n"
        "prunings: []\n"
        "```\n"
    )
    out = await consolidation(skills, sidecar, fork).run()

    assert out["consolidated"] == []
    assert "old-skill" in out["skipped"]
    assert (skills / "old-skill").is_dir()
    assert sidecar.get("old-skill")["state"] != "archived"


async def test_run_consolidation_absent_yaml_block(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    make_skill(skills, "some-skill")
    sidecar.mark_created("some-skill", "agent", NOW.isoformat())

    out = await consolidation(skills, sidecar,
                              FakeFork("No block here, just prose.")).run()

    assert out == {"consolidated": [], "pruned": [], "skipped": [],
                   "summary": "No block here, just prose."}
    assert (skills / "some-skill").is_dir()


async def test_run_consolidation_malformed_yaml_degrades_to_empty(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    make_skill(skills, "some-skill")
    sidecar.mark_created("some-skill", "agent", NOW.isoformat())

    out = await consolidation(
        skills, sidecar, FakeFork("```yaml\nconsolidations: [:::not yaml\n```\n")).run()

    assert out["consolidated"] == []
    assert out["pruned"] == []
    assert (skills / "some-skill").is_dir()


async def test_a_failed_fork_archives_nothing(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    make_skill(skills, "some-skill")
    sidecar.mark_created("some-skill", "agent", NOW.isoformat())

    out = await consolidation(skills, sidecar, FakeFork(boom=True)).run()

    assert out == {"consolidated": [], "pruned": [], "skipped": [], "summary": None}
    assert (skills / "some-skill").is_dir()


async def test_run_consolidation_never_touches_pinned_or_user_skill(tmp_path):
    home, skills, sidecar = make_home(tmp_path)
    make_skill(skills, "pinned-skill")
    sidecar.mark_created("pinned-skill", "agent", NOW.isoformat())
    sidecar.set_pinned("pinned-skill", True)
    make_skill(skills, "user-skill")
    sidecar.mark_created("user-skill", "user", NOW.isoformat())
    make_skill(skills, "umbrella")
    sidecar.mark_created("umbrella", "agent", NOW.isoformat())

    fork = FakeFork(
        "```yaml\n"
        "consolidations:\n"
        "  - from: pinned-skill\n"
        "    into: umbrella\n"
        "    reason: model tried anyway\n"
        "prunings:\n"
        "  - name: user-skill\n"
        "    reason: model tried anyway\n"
        "```\n"
    )
    out = await consolidation(skills, sidecar, fork).run()

    assert out["consolidated"] == []
    assert out["pruned"] == []
    assert "pinned-skill" in out["skipped"]
    assert "user-skill" in out["skipped"]
    assert (skills / "pinned-skill").is_dir()
    assert (skills / "user-skill").is_dir()


# ---------------------------------------------------------- the sweep (4b) --
# `workers.curator.run_all`: one pass per scope, owner first, behind the
# operator's unsuffixed pause. The scopes here are fakes with the two fields the
# sweep reads (`meta`, `curator`); the real registry is `test_learning_scopes.py`.

class RecordingCurator:
    def __init__(self, log: list, user_id: int, boom=False):
        self.log = log
        self.user_id = user_id
        self.boom = boom

    async def run_pass(self):
        if self.boom:
            raise RuntimeError("pass died")
        self.log.append(self.user_id)
        return {"ran": True, "counts": {}}


class FakeScopes:
    """`LearningScopes` over a dict. The owner's `meta` is the UNSUFFIXED store,
    exactly what `ScopedMeta(meta, "")` gives the real owner scope."""

    def __init__(self, owner: int, others: tuple[int, ...] = (), *, meta=None,
                 boom: set[int] = frozenset()):
        self.meta = meta or FakeMeta()
        self.log: list[int] = []
        self._owner = owner
        self._ids = [owner, *others]
        self._scopes = {}
        for uid in self._ids:
            self._scopes[uid] = SimpleNamespace(
                user_id=uid, is_owner=uid == owner,
                meta=self.meta if uid == owner else FakeMeta(),
                curator=RecordingCurator(self.log, uid, boom=uid in boom))

    async def owner(self):
        return self._scopes[self._owner]

    async def for_user(self, user_id):
        if user_id == 0:
            return self._scopes[self._owner]
        return self._scopes[user_id]

    async def user_ids(self):
        return list(self._ids)


async def test_run_all_sweeps_the_owner_first_then_every_user(tmp_path):
    from claude_hermes.workers.curator import run_all

    scopes = FakeScopes(1, (7, 9))
    results = await run_all(scopes)
    assert scopes.log == [1, 7, 9]
    assert [r["user_id"] for r in results] == [1, 7, 9]
    assert all(r["ran"] for r in results)


async def test_the_unsuffixed_pause_stops_the_whole_sweep(tmp_path):
    """`curator_paused` (no suffix) is the OPERATOR's switch (B-2). User 7's
    own `curator_paused:7` is unset, and their pass must still not run."""
    from claude_hermes.workers.curator import run_all

    scopes = FakeScopes(1, (7,), meta=FakeMeta(**{PAUSED_META_KEY: "1"}))
    assert await run_all(scopes) == [{"ran": False, "reason": "paused"}]
    assert scopes.log == []


async def test_run_all_takes_one_lock_per_user_and_releases_each(tmp_path):
    """Sequential, one lock each: `lock_for(uid)` is called with every id the
    registry listed, and a lock that is held by another worker skips only THAT
    user's pass."""
    from claude_hermes.workers.curator import run_all

    class FakeLock:
        def __init__(self, held):
            self.held, self.released = held, 0

        async def __aenter__(self):
            return self.held

        async def __aexit__(self, *exc):
            self.released += 1

    locks = {1: FakeLock(True), 7: FakeLock(False), 9: FakeLock(True)}
    asked = []

    def lock_for(uid):
        asked.append(uid)
        return locks[uid]

    scopes = FakeScopes(1, (7, 9))
    results = await run_all(scopes, lock_for)
    assert asked == [1, 7, 9]
    assert scopes.log == [1, 9]
    assert results[1] == {"ran": False, "reason": "locked", "user_id": 7}
    assert all(lock.released == 1 for lock in locks.values())


async def test_a_failing_users_pass_does_not_stop_the_sweep(tmp_path):
    from claude_hermes.workers.curator import run_all

    scopes = FakeScopes(1, (7, 9), boom={7})
    results = await run_all(scopes)
    assert scopes.log == [1, 9]
    assert results[1] == {"ran": False, "reason": "error", "user_id": 7}


async def test_a_scope_that_cannot_be_resolved_is_skipped_not_fatal(tmp_path):
    """A `users` row whose tree fails to seed must not stop every other user's
    library from ageing."""
    from claude_hermes.workers.curator import run_all

    scopes = FakeScopes(1, (9,))

    async def for_user(user_id):
        if user_id == 9:
            raise OSError("disk full")
        return await FakeScopes.for_user(scopes, user_id)

    scopes.for_user = for_user
    results = await run_all(scopes)
    assert scopes.log == [1]
    assert results[1] == {"ran": False, "reason": "error", "user_id": 9}

"""Shared factory for web tests: an in-memory app with a fake runner and a real DB.

The objects below are the ones `main.build_app()` builds, over the same engine —
only the runner is a fake, because a test that wanted the real one would want the
SDK too. What `make_client` returns is exactly what `build_web_app` was handed, so
a test can drive the store, the bus or the owner scope's sidecar behind a route.

Since Wave 3b the one fake wears two hats: it is `deps.runner` (the `TurnService`
every Customize mutation invalidates) AND `deps.turns` (the `TurnLauncher` the
message route queues onto). Deliberately one object: the busy set, the recorded
calls and the emitted events have to agree, and in a real deployment they do
because the row and the worker agree. A test that wants a lane to look busy adds
its key to `runner.busy`, and the launcher half, the runner half and sessions'
`LaneActivity` all read that one set.

The engine: `make_client` builds one over `CH_DATABASE_URL` (the per-process test
database `tests/conftest.py` migrated) unless the test hands in the `engine`
fixture, which is what a test that also wants to read rows directly does.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from claude_hermes.core.app_context import ThreadEnsure
from claude_hermes.core.config import Config
from claude_hermes.core.db.engine import make_engine
from claude_hermes.core.meta import MetaStore
from claude_hermes.core.workspace import Workspace
from claude_hermes.http.app import build_web_app
from claude_hermes.http.deps import (
    PluginSlashSources,
    WebDeps,
    extensions_view,
    prefix_invalidator,
    thread_guard,
)
from claude_hermes.modules.extensions import wiring as extensions_wiring
from claude_hermes.modules.extensions.infrastructure.file_catalog_writer import (
    FileCatalogWriter,
)
from claude_hermes.modules.extensions.infrastructure.pending_states import PendingStates
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (  # noqa: E501
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (  # noqa: E501
    SqlOauthTokenRepository,
)
from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
from claude_hermes.modules.files.infrastructure.repository import FileRepository
from claude_hermes.modules.identity.application.services import (
    AccessControl,
    user_workspace_dir,
)
from claude_hermes.modules.identity.domain.session_token import COOKIE, sign_session
from claude_hermes.modules.identity.domain.telegram_proof import login_key, webapp_key
from claude_hermes.modules.identity.infrastructure.repositories import UserRepository
from claude_hermes.modules.learning.application.services.library import SkillLibraryService
from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive
from claude_hermes.modules.learning.infrastructure.skill_repository import SkillRepository
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar
from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.modules.sessions.application.services import (
    SessionRouter,
    SessionService,
    ThreadService,
    ThreadTitleService,
)
from claude_hermes.modules.sessions.infrastructure.repositories import (
    SessionRepository,
    ThreadRepository,
)
from claude_hermes.modules.turns.application.bus import TurnBus
from claude_hermes.modules.turns.application.control import MetaSlashCommands
from claude_hermes.modules.turns.domain.value_objects import _attachment_wire
from claude_hermes.modules.turns.infrastructure.publishers import InProcessPublisher
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository

from conftest import ensure_users          # noqa: E402  (pytest puts tests/ on sys.path)

ORIGIN = "https://cladex.test"
SECRET = "unit-secret"


class FakeRunner:
    """Enough of `TurnService` AND of `TurnLauncher` for the routers.

    `start` is the launcher half: it takes the same arguments the route used to hand
    `run_turn`, records the same 5-tuple, emits the same four events and returns the
    turn id the 202 now carries. It runs the turn synchronously where the real one
    only queues it — the fastest possible worker, which is what lets a test read the
    events straight after the POST without a sleep long enough to be flaky.

    `is_busy` and `cancel` are async because the real launcher reads a row for both.
    `run_turn` delegates to `start`, so the fake cannot grow two copies of the wire
    format.
    """

    def __init__(self, bus):
        self.bus = bus
        self.calls = []
        self.busy: set[str] = set()
        self.cancelled = []
        self.reset_alls = 0                 # how many times a mutation invalidated the prefix
        # Wave 4b: `(owner_id, include_unowned)` per skills mutation — a skill edit
        # evicts ONE user's lanes, never everybody's (B-4).
        self.reset_owneds: list[tuple[int, bool]] = []

    async def is_busy(self, key):
        return key in self.busy

    async def cancel(self, key, turn_id=None):
        self.cancelled.append(key)
        return key in self.busy

    async def reset_all(self):
        self.reset_alls += 1

    async def reset_owned(self, owner_id, *, include_unowned=False):
        self.reset_owneds.append((owner_id, include_unowned))

    async def start(self, session_key, text, *, user_id=0, attachments=None,
                    channel="telegram"):
        self.calls.append((session_key, text, user_id, attachments, channel))
        turn = "t-" + str(len(self.calls))
        await self.bus.emit(session_key, turn, "turn.start", {"channel": channel, "user_id": user_id})
        # the real runner's wire builder, so the fake cannot drift from it
        await self.bus.emit(session_key, turn, "user.message", {
            "text": text,
            "attachments": [_attachment_wire(a) for a in (attachments or [])],
        })
        await self.bus.emit(session_key, turn, "text.block", {"text": "reply to " + text})
        await self.bus.emit(session_key, turn, "turn.end", {"status": "ok", "cost_usd": 0.01,
                                                            "sdk_session_id": "sdk", "error": None})
        return turn

    async def run_turn(self, session_key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        await self.start(session_key, text, user_id=user_id, attachments=attachments,
                         channel=channel)
        return "reply to " + text


@dataclass(frozen=True)
class FakeLearningScope:
    """learning's `LearningScope` by shape (Wave 4b, D1). The four the skills
    router reads are real objects over a real directory; the rest are `None`
    because no route reaches them."""

    user_id: int
    workspace: Workspace
    is_owner: bool
    skills_dir: Path
    logs_dir: Path
    sidecar: Any
    library: Any
    meta: Any = None
    archive: Any = None
    admin: Any = None
    nudges: Any = None
    gate: Any = None
    review: Any = None
    curator: Any = None


class FakeLearningScopes:
    """learning's `LearningScopes` by shape: `for_user(uid)` -> a scope over that
    user's tree under `home`, built once and cached; `owner()` is the deploy
    owner's, which a test drives directly when it wants the sidecar or the skills
    directory behind a route. `0` resolves to the owner, as the real registry
    normalises it (R-6).
    """

    def __init__(self, home: Path, owner_id: int | None, owner_scope: FakeLearningScope):
        self._home = home
        self._owner_id = owner_id
        self._scopes: dict[int, FakeLearningScope] = {}
        if owner_id is not None:
            self._scopes[owner_id] = owner_scope
        self._owner_scope = owner_scope

    async def owner(self) -> FakeLearningScope:
        return self._owner_scope

    async def for_user(self, user_id: int) -> FakeLearningScope:
        if user_id == 0:
            return self._owner_scope
        scope = self._scopes.get(user_id)
        if scope is None:
            ws = Workspace.for_user(self._home, user_workspace_dir(user_id))
            ws.skills_dir.mkdir(parents=True, exist_ok=True)
            sidecar = UsageSidecar(ws.skills_dir)
            scope = FakeLearningScope(
                user_id=user_id, workspace=ws, is_owner=False,
                skills_dir=ws.skills_dir, logs_dir=ws.logs_dir, sidecar=sidecar,
                library=SkillLibraryService(SkillRepository(ws.skills_dir), sidecar,
                                            SkillArchive(ws.skills_dir, sidecar)))
            self._scopes[user_id] = scope
        return scope


class FakeLaneActivity:
    """sessions' `LaneActivity` over the fake runner's busy set.

    The real one is `TurnRepository.active_lanes` — the row is the one source of
    truth for "running" — and a fake runner has no rows, so the set it already
    keeps for `is_busy` answers the same question.
    """

    def __init__(self, runner):
        self._runner = runner

    async def active_lanes(self, session_keys) -> set:
        return {k for k in session_keys if k in self._runner.busy}


async def make_client(tmp_path: Path, *, owner: int | None = 1, allowed=(), voice=None,
                      dist=None, cfg: Config | None = None,
                      bot_username: str = "cladex_bot", probe=None, wiki: bool = True,
                      catalog_root: Path | None = None, engine=None):
    cfg = cfg or Config()
    cfg.web_enabled = True
    cfg.web_secret = SECRET
    cfg.web_public_origin = ORIGIN
    cfg.allowed_users = list(allowed)
    if engine is None:
        engine = make_engine(os.environ["CH_DATABASE_URL"])
    # Wave 4a: every `owner_id` and `threads.owner_user_id` references `users(id)`,
    # so the rows that make the foreign keys hold are created HERE rather than in
    # each router test. The owner's row IS the claim (`owner=None` is a deploy
    # nobody has claimed yet); its workspace_dir is `""` (the root is CH_HOME
    # itself), and an allowlisted id gets `users/<id>`, exactly what
    # `AccessControl.check` would have written.
    await ensure_users(engine, *cfg.allowed_users, owner=owner)
    (tmp_path / "agent_home").mkdir(exist_ok=True)
    # The same two objects main.py builds, from the same path.
    skills_dir = tmp_path / "agent_home" / ".claude" / "skills"
    sidecar = UsageSidecar(skills_dir)
    # The same path WikiStore.root resolves to. `wiki=False` is how a test asks for
    # the deployment that never built one — the router must then not register.
    wiki_root = tmp_path / "agent_home" / "wiki" if wiki else None
    if wiki_root is not None:
        wiki_root.mkdir(parents=True, exist_ok=True)

    bus = TurnBus(TurnEventRepository(engine), InProcessPublisher())
    thread_repo = ThreadRepository(engine)
    runner = FakeRunner(bus)
    # `LaneActivity` is the `turns` table in a real deployment; here it is the
    # fake runner's busy set, which is what a test drives to make a lane look busy.
    threads = ThreadService(thread_repo, FakeLaneActivity(runner))
    # No generator: a test never calls a model to name a thread, so `autotitle`
    # returns None — exactly as it does on a deployment without a title model.
    titles = ThreadTitleService(thread_repo, None, bus)
    extensions = extensions_wiring.build(
        tmp_path, engine, cfg=cfg, catalog_root=catalog_root,
        pending=PendingStates(), probe_fn=probe, writer=FileCatalogWriter(tmp_path),
        store=SqlExtensionRepository(engine), tokens=SqlOauthTokenRepository(engine))
    jobs = JobRepository(engine)
    library = SkillLibraryService(SkillRepository(skills_dir), sidecar,
                                  SkillArchive(skills_dir, sidecar))
    # The real invalidator `web_deps` binds, over the fake runner: a route that
    # names an owner drops that owner's lanes, and an approved draft (the shared
    # catalog) still drops every lane. A fake of its own would let the two drift.
    async def deploy_owner() -> int:
        return owner or 0

    invalidate = prefix_invalidator(runner, deploy_owner)
    owner_scope = FakeLearningScope(
        user_id=owner or 0, workspace=Workspace.owner(tmp_path), is_owner=True,
        skills_dir=skills_dir, logs_dir=tmp_path / "logs", sidecar=sidecar,
        library=library)
    # The grant defaults as main.py passes them (0009): read off the live `cfg` at
    # call time, so a test that flips `users_shell_enabled` sees the list follow.
    acl = AccessControl(UserRepository(engine), allowed_users=lambda: cfg.allowed_users,
                        shell_default=lambda: cfg.users_shell_enabled,
                        learning_default=lambda: cfg.users_learning_enabled)
    # Which model a lane runs, built exactly as main.py builds it: the same thread
    # rows, a session router over the same engine, the ACL as the owner defaults.
    lane_models = SessionService(thread_repo, SessionRouter(SessionRepository(engine), cfg),
                                 cfg, defaults=acl, events=bus)
    deps = WebDeps(
        cfg=cfg, runner=runner, acl=acl, bus=bus,
        threads=threads, lane_models=lane_models,
        turns=runner, turn_events=TurnEventRepository(engine),
        owned=thread_guard(threads), auto_title=titles.autotitle,
        slash_sources=PluginSlashSources(extensions.query),
        # The names the CLI reported, read from `meta` as `web_deps` does: a test
        # that wants some records them there or overrides `get_sdk_commands`.
        sdk_commands=MetaSlashCommands(MetaStore(engine)),
        file_store=FileService(FileRepository(engine), LocalBlobStore(tmp_path), None),
        broker=None, voice=voice,
        scheduler=SchedulerService(jobs, threads=ThreadEnsure(threads.touch)),
        extensions=extensions_view(extensions, cfg=cfg, catalog_root=catalog_root,
                                   invalidate=invalidate),
        catalog_root=catalog_root,
        # Always set: the skills router mounts on `learning`, and a harness that
        # forgot it would lose the router silently (R-6).
        learning=FakeLearningScopes(tmp_path, owner, owner_scope),
        invalidate=invalidate,
        wiki_root=wiki_root,
        login_key=login_key("123:ABC"), webapp_key=webapp_key("123:ABC"),
        bot_username=bot_username, home=tmp_path, dist_dir=dist,
        now=lambda: int(time.time()),
    )
    app = build_web_app(deps)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    return client, deps


def login(client: httpx.AsyncClient, user_id: int) -> None:
    client.cookies.set(COOKIE, sign_session(user_id, int(time.time()) + 3600, SECRET))


def json_headers() -> dict:
    return {"Origin": ORIGIN, "Content-Type": "application/json"}

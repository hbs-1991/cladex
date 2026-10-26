"""The composition root's vocabulary: what a built application is made of.

`main.build_app()` returns an `AppContext` — one object holding every service the
process needs, with the module objects already plugged into each other's ports.
Nothing reaches for a global afterwards, and nothing is assigned onto a service
after construction (the `runner._x = y` era ended here).

Two rules shape this file:

* **`core` may not import a module** (import-linter, "core does not depend on the
  application"). So every field is typed `Any` and every adapter below takes its
  collaborators as arguments rather than importing them. `main.py` does the
  importing; this file only says how the pieces fit.
* **An adapter here bridges a port and its provider, never a behaviour.** Each one
  below exists because a consumer's Protocol and the providing module's method
  disagree about argument shape — a dict where the service wants keywords, a
  bound `home`, a two-argument `dismiss`. A body that decides anything belongs in
  a module.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_hermes.core.exceptions import NotFound
from claude_hermes.core.session_keys import lane_owner

# --- sub-bundles -------------------------------------------------------------


@dataclass
class MemoryBundle:
    """The agent wiki: the index the prompt reads and the hook table every fork
    installs. The lint pass is built per scope (`main.build_app`'s `wiki_lint_for`)
    and lives on the learning scope, not here."""
    wiki: Any                 # WikiStore — satisfies turns' WikiPort
    wiki_root: Path
    hooks: Any                # TurnHooksProvider (see WikiHooksProvider below)
    indexer: Any = None       # WikiIndexService — the `wiki_pages` cache over the trees
    reindex_all: Any = None   # async () -> None: rebuild every user's index from disk (boot)


@dataclass
class LearningBundle:
    """Skills, usage, nudges, the review loop and the curator pass.

    Since Wave 4b the fields below `loop` are the DEPLOY OWNER's scope's objects —
    `main.build_app` builds that scope eagerly and the curator/review tests read
    them by name — and `scopes` is the registry every consumer goes through: the
    `learning_review` task, the curator sweep, the `skill_admin` tool and the
    skills API all resolve a user's scope per call.
    """
    skills_dir: Path
    logs_dir: Path
    sidecar: Any              # UsageSidecar — the same instance the turn loop bumps
    library: Any              # SkillLibraryService
    admin: Any                # SkillAdminService — the skill_admin tool's body
    nudges: Any               # NudgeTracker
    loop: Any                 # LearningLoop — turns' TurnObserver
    curator: Any              # CuratorService
    # Wave 3a: the review is a queued task, so the process that RUNS it is not
    # the one that decided to. `learning_review` reaches the service through
    # here; before the queue, only the loop's own `asyncio.Task` ever had it.
    review: Any = None        # ReviewService
    gate: Any = None          # ReviewGate — the `learning_review_until` lease
    # Wave 4b: a scope per user. `modules.learning.application.scopes.LearningScopes`.
    scopes: Any = None


@dataclass
class AppContext:
    """Everything `build_app()` built. Headless by construction: no bot, no
    network, no token — `run()` adds the Telegram half afterwards, which is what
    keeps `build_app()` testable and what lets a web-only deployment exist."""

    cfg: Any
    workspace: Any            # core.workspace.Workspace
    engine: Any               # AsyncEngine — the one pool; `shutdown` disposes it
    meta: Any                 # core.meta.MetaStore

    # sessions
    sessions: Any             # SessionRouter  — turns' SessionLane
    threads: Any              # ThreadService  — ThreadRegistry / ThreadVisibility
    titles: Any               # ThreadTitleService
    messages: Any             # MessageRepository — MessageLog / MessageSearch

    # turns
    bus: Any                  # TurnBus
    turn_events: Any          # TurnEventRepository
    costs: Any                # TurnCostRepository
    runner: Any               # TurnService
    ask_broker: Any           # AskBroker
    ask_presenter: Any        # LatePresenter — run() binds the Telegram keyboard
    deliver_document: Any     # LateDocumentDelivery — run() binds the Bot

    # the rest
    acl: Any                  # identity AccessControl
    files: Any                # files FileService
    scheduler: Any            # scheduler SchedulerService
    jobs: Any                 # scheduler JobRepository
    extensions: Any           # extensions ExtensionsBundle
    learning: LearningBundle
    memory: MemoryBundle

    # Wave 3a — the queue and the two clients that only exist in some modes.
    # `registry` and `queue` are always set by `build_app`; they carry defaults
    # only because the fields above them do not.
    registry: Any = None      # core.tasks.TaskRegistry — both sides share this one
    queue: Any = None         # core.tasks.TaskQueue — InlineQueue or TaskiqQueue
    redis: Any = None         # redis.asyncio.Redis in redis mode, else None
    s3: Any = None            # integrations.s3.S3Client with blob_backend=s3, else None
    # The Fernet box the extension secrets table is sealed with, built from the
    # environment BEFORE `run()` pops `CH_SECRETS_KEY`. Held here so a second
    # store (or a worker) can be given the same one rather than re-reading an
    # environment that no longer has the key in it.
    secret_box: Any = None    # core.crypto.SecretBox

    # Wave 3b — the turn is a row and a task. `events_bus` is the channel bus every
    # turn event and control nudge rides on (in-process inline, Redis pub/sub
    # otherwise) and `control` is this process's subscription to `hermes:ctl`; both
    # are STARTED by the entry point, after the secrets are popped, never by
    # `build_app` — which dials nothing, which is what keeps it testable. `turns` is
    # the launcher every channel starts a turn through, `turn_runs` is the side that
    # claims a row and runs the SDK loop (the worker's half; the agent holds one only
    # so inline mode has somewhere to run), and `turn_store` is the `turns` table,
    # held here because the sidebar's "running" and the reaper both read it.
    events_bus: Any = None    # core.eventbus.EventBus
    control: Any = None       # modules.turns.application.control.TurnControl
    turns: Any = None         # TurnLauncher
    turn_runs: Any = None     # TurnRunService
    turn_store: Any = None    # TurnRepository
    toolset_sync: Any = None  # async () -> bool: reset every lane when the hermes toolset moved
    # Which model a lane runs: sessions' SessionService, the one gatherer behind
    # `core.model_catalog.resolve()` for the turn, the web routes and /model.
    lane_models: Any = None

    # Wave 4a — which workspace a user, and therefore a turn, runs in.
    # `core.workspace.WorkspaceResolver`, bound at composition to the ACL's
    # `workspace_dir_of` and to the deploy owner. Every process that runs a turn
    # (agent, worker, scheduler) holds the same one, so nothing resolves a path
    # from `CH_HOME/agent_home` directly any more except the learning services
    # (4b). `workspace` above stays the OWNER's, which is what the curator, the
    # skills library and the backups still take.
    workspaces: Any = None    # core.workspace.WorkspaceResolver

    # Wave 4a — webhook mode. Telegram's delivery is at-least-once, so the route
    # drops an `update_id` it has already accepted; this is where the record of
    # "already accepted" lives. In redis mode it is shared by every instance
    # behind the public origin, which is the only spelling that works with
    # `--scale agent=N`. Unused in polling mode and by every non-web process.
    update_dedup: Any = None  # modules.telegram UpdateDedup

    # The content plan: `modules.content` `ContentService`, built only when
    # `content_enabled`. `None` is the whole of "disabled" — no routes (the shell
    # does not mount them), no `content_plan` tool, and a tick that skips it.
    content: Any = None
    # The plan's two Telegram edges, both `None` with the plan off. `content_cards`
    # is the late-bound holder of content's `ApprovalCards` port: `build_delivery`
    # sets its `.target` to telegram's card sender in the agent AND in every
    # worker, because the card outbox is drained by whichever tick claims it.
    # `content_decisions` is telegram's `ContentDecisions` port (the `cnt:`
    # buttons, a reply to a card, the "Контент" topic the titler skips).
    content_cards: Any = None
    content_decisions: Any = None

    voice: Any = None        # integrations.openai.voice.OpenAIVoice, or None
    web_login_key: bytes = b""
    web_webapp_key: bytes = b""
    # Filled by `main.build_telegram_gateway(ctx)` in `run()`; None in a web-only
    # process and in every test that only calls `build_app()`.
    telegram: Any = None


# --- port adapters -----------------------------------------------------------


class SkillAdminTool:
    """turns' `SkillAdminPort` (`handle(args, *, ctx)`) over learning's
    `SkillAdminService.handle(action, *, name, backup_id)` — of the LANE OWNER's
    scope.

    The tool hands the raw MCP argument dict down; the service takes named
    arguments so it can be called from anywhere. Since Wave 4b `scopes` is the
    `LearningScopes` registry and the scope is resolved per call from
    `ctx.owner_id`: the library `skill_admin` administers is the lane owner's,
    never the sender's and never the deploy owner's by default. No `ctx` means
    `0`, which the registry normalises to the deploy owner.
    """

    def __init__(self, scopes: Any):
        self._scopes = scopes

    async def handle(self, args: dict, *, ctx: Any = None) -> str:
        scope = await self._scopes.for_user(getattr(ctx, "owner_id", 0) or 0)
        return await scope.admin.handle(
            args.get("action", "list"),
            name=args.get("name", "") or "",
            backup_id=args.get("backup_id") or None,
        )


class ExtensionAdminTool:
    """turns' `ExtensionAdminPort` (`handle(args, *, ctx) -> (text, resets_lane)`)
    over extensions' `ExtensionAdminService`.

    Two shapes are bridged. The argument dict becomes keywords, and the owner gate
    is resolved here — the service takes `is_owner` already decided, and it
    **fails closed**: no acl or no turn context means nobody to identify, so
    nobody to authorize.

    The reset signal is the subtle half. The service reports "the toolset changed"
    by awaiting its `on_change` invalidator, which is what the web channel wants
    (reset every lane). The tool channel wants it per CALL, because the turn sets
    `ctx.flags.reset_lane` and the runner applies it *after* `sessions.bind` —
    resetting during the turn would be undone by the bind. `with_on_change` gives
    this call its own sink.

    `name`/`description`/`schema` ride along because the tool is *extensions'* tool:
    `modules/extensions/presentation/agent_tool.py` declares them and the MCP server
    registers what this port carries, so there is one `ext_manage` rather than a
    body in one module and a description in another. `core` may not import a module,
    so `main.py` passes them in; a caller that passes none leaves the server nothing
    to register from, and `build_hermes_server` says so rather than guessing.
    """

    def __init__(self, service: Any, *, acl: Any = None, name: str = "",
                 description: str = "", schema: dict | None = None):
        self._service = service
        self._acl = acl
        self.name = name
        self.description = description
        self.schema = dict(schema or {})

    async def handle(self, args: dict, *, ctx) -> tuple[str, bool]:
        resets = False

        async def flag(owner_id: int) -> None:
            # The invalidator's argument is ignored here on purpose: this sink
            # resets THIS lane, and this lane is that owner's by construction.
            # The web channel's sink is the one that has to act on the id.
            nonlocal resets
            resets = True

        user_id = ctx.user_id if ctx is not None else 0
        # The LANE's owner, not the sender: `list` reports the set the turn will
        # actually compose with, which on a group lane is the deploy owner's.
        owner_id = getattr(ctx, "owner_id", 0) if ctx is not None else 0
        is_owner = bool(
            self._acl is not None and ctx is not None
            and await self._acl.is_owner(user_id)
        )
        text = await self._service.with_on_change(flag).handle(
            args.get("action", "list"),
            name=args.get("name") or "",
            kind=args.get("kind") or None,
            manifest=args.get("manifest") or "",
            user_id=user_id,
            is_owner=is_owner,
            owner_id=owner_id,
        )
        return text, resets


class ThreadEnsure:
    """scheduler's `ThreadRegistry` over sessions' thread writer (`ThreadService.touch`
    — the same arguments). Only the verb differs: scheduler says "make sure the
    delivery lane has a row", and does not want to know it is one upsert."""

    def __init__(self, write):
        self._write = write

    async def ensure(self, session_key: str, channel: str, owner_id: int | None,
                     now_iso: str, *, talked: bool = False) -> None:
        await self._write(session_key, channel, owner_id, now_iso, talked=talked)


class LateDocumentDelivery:
    """turns' `DocumentDeliveryPort`, bound once `run()` has an aiogram Bot.

    `build_app()` is headless on purpose — no token, no network — but the runner
    is built there, and `SdkTurnExecutor` takes its ports once. This holder is the
    seam: `run()` sets `.target`, and a web-only process leaves it unset, where
    the tool still works through the file store (`send_document` publishes to the
    conversation and reports it went there).
    """

    def __init__(self):
        self.target = None

    async def __call__(self, chat_id: int, thread_id: int | None, path: str,
                       caption: str | None = None) -> None:
        if self.target is None:
            raise RuntimeError("no Telegram channel is wired for send_document")
        await self.target(chat_id, thread_id, path, caption)


class LatePresenter:
    """turns' `AskPresenter`, bound when `run()` builds the Telegram client.

    Unbound it presents nothing, which is exactly what a browser-only lane wants:
    `AskService` still opens the question, emits `ask.open` and waits, and the
    answer comes back through `POST /api/asks/{qid}`.
    """

    def __init__(self):
        self.target = None

    async def present(self, chat_id: int, thread_id: int | None, qid: str,
                      question: str, options: list[str]):
        if self.target is None:
            return None
        return await self.target.present(chat_id, thread_id, qid, question, options)

    async def dismiss(self, handle) -> None:
        if self.target is not None:
            await self.target.dismiss(handle)


class QueuedTurnRunner:
    """telegram's `TurnRunnerPort` over the launcher: start a turn, wait for it.

    The Telegram handler used to `await runner.run_turn(...)` and therefore WAS
    the process running the turn. Since Wave 3b it starts a row and waits for the
    end event, which is what lets a worker in another container do the work — and
    what lets an agent restart mid-turn stop killing the turn.

    **A busy lane is retried, not refused, and that is a product decision.** Today
    a second Telegram message on a busy lane waits behind the per-lane lock and is
    answered; double-texting is the most common Telegram behaviour there is, and
    turning it into "Something went wrong on my side" would be a break, not a
    wording change. So `LaneBusy` is slept on until the lane frees or the queue
    timeout elapses, after which it propagates and the dispatcher's existing
    `except Exception` says its line.

    `busy` is passed in rather than imported: `core` may not import a module
    (import-linter), and the exception the launcher raises belongs to `turns`.
    `main.py` hands over `domain.exceptions.LaneBusy`, the same way
    `TranslatingSecrets` is handed telegram's `SecretNameError`.

    `reset` goes straight to the runner: `/new` drops the lane's SDK session and
    starts nothing.
    """

    def __init__(self, launcher: Any, runner: Any, *, busy: type[BaseException],
                 busy_retry_seconds: float = 1.0,
                 timeout_seconds: float | None = None):
        self._launcher = launcher
        self._runner = runner
        self._busy = busy
        self._retry = max(float(busy_retry_seconds), 0.01)
        # The launcher's own queue timeout by default, so the wait for a lane and
        # the lease on a queued turn cannot drift apart.
        self._timeout = float(
            timeout_seconds if timeout_seconds is not None
            else getattr(launcher, "queue_timeout_seconds", 300.0))

    async def run_turn(self, session_key: str, text: str, *, user_id: int = 0,
                       attachments=None, channel: str = "telegram") -> str | None:
        """The reply, or None when the turn was cancelled (the caller's `if reply:`
        then skips the final message, which is what a cancelled turn always did).
        `TurnFailed` propagates."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while True:
            try:
                turn_id = await self._launcher.start(
                    session_key, text, user_id=user_id, attachments=attachments,
                    channel=channel)
                break
            except self._busy:
                if loop.time() >= deadline:
                    raise
                await asyncio.sleep(self._retry)
        return await self._launcher.wait(turn_id, session_key)

    async def reset(self, session_key: str) -> None:
        await self._runner.reset(session_key)


class JobLookup:
    """turns' `JobLookup` over `SchedulerService.by_thread`. The runner asks "is
    this forum topic a routine's delivery lane?" to inject the cron context."""

    def __init__(self, scheduler: Any):
        self._scheduler = scheduler

    async def job_for_thread(self, chat_id: int, thread_id: int):
        return await self._scheduler.by_thread(chat_id, thread_id)


class SchedulerPublisher:
    """content's `Publisher` AND `Drafter` ports over `SchedulerService`.

    The two ports are the same shape and every call names its job id, so
    `main.build_app` passes ONE instance as both: which routine is woken — the
    publisher or the drafter — is the plan's choice, read from its own meta keys.

    `describe(job_id, owner_id)` is `SchedulerService.get` scoped to the owner and
    cut down to the six fields the plan reads — the paused/missing checks (`kind`,
    `state`), the busy check that holds the wake backoff (`next_run_at`,
    `lease_until`) and the stuck timeout (`timeout_seconds`). Somebody else's
    routine is `None`, exactly as a deleted one is. `wake_if_idle` is the
    scheduler's own compare-and-set, passed straight through: the plan's one way to
    start the publisher, never `run_now`, which clears a live lease.

    **Late-bound, and that is a cycle, not a taste.** The scheduler's executor
    takes content's routine tools, those are built over content's service, and the
    service takes this adapter — so one edge has to wait for the scheduler to
    exist. `main.build_app` binds `scheduler` right after constructing it, and
    nothing calls the port before `build_app` has returned (the tick and the
    routes are the only callers); an unbound call says so instead of an
    `AttributeError` on None.
    """

    FIELDS = ("name", "kind", "state", "next_run_at", "lease_until",
              "timeout_seconds")

    def __init__(self, scheduler: Any = None):
        self.scheduler = scheduler

    def _bound(self) -> Any:
        if self.scheduler is None:
            raise RuntimeError("the content publisher port has no scheduler bound")
        return self.scheduler

    async def describe(self, job_id: int, owner_id: int) -> dict | None:
        job = await self._bound().get(int(job_id), int(owner_id))
        if job is None:
            return None
        return {key: job.get(key) for key in self.FIELDS}

    async def wake_if_idle(self, job_id: int, now_iso: str) -> bool:
        return bool(await self._bound().wake_if_idle(int(job_id), now_iso))


class ExtensionCompose:
    """turns' `ExtensionComposePort` — `for_turn(owner_id)` is `for_scope("turn",
    owner_id)` on extensions' `ComposeService`. The scope literal lives here rather
    than in the runner so a second scope cannot be invented by a caller; the owner
    does NOT live here, because it is a property of the lane the runner is running."""

    def __init__(self, compose: Any):
        self._compose = compose

    async def for_turn(self, *, owner_id: int):
        return await self._compose.for_scope("turn", owner_id=owner_id)


class WikiHooksProvider:
    """turns' `TurnHooksProvider` over memory's `wiki_hooks` provider.

    A fresh `WikiTurnState` per turn — the stop gate and the page-touch recorder
    share it, and carrying one across turns would make the second turn think the
    first turn's pages were already logged. The Bash guard is NOT here:
    `SdkTurnExecutor` seeds that itself so it exists in every composition.

    Since Wave 4a the hooks are built over the LANE OWNER's wiki. `wiki_for`
    turns the turn's workspace into that user's store; `wiki` (the deploy
    owner's) is what a caller that resolved no workspace gets. Without this the
    `raw/` guard, the page-touch recorder and the Stop gate would all be pointed
    at the deploy owner's tree while the turn wrote in somebody else's — the gate
    would nag about pages that do not exist and a `Write` into the owner's wiki
    would be waved through by the one hook that is supposed to see it (D6, B1).

    `indexer` (the graph-memory cut) adds the Stop-hook flush that writes what the
    turn touched into `wiki_pages`. It needs the row's OWNER, which is why `build`
    grew an `owner_id`: `0` — a bare fork context — means "do not index", and a
    composition that wired no indexer gets exactly the hook table it got before.
    """

    def __init__(self, wiki: Any, *, wiki_hooks, new_state, wiki_for=None,
                 indexer: Any = None):
        self._wiki = wiki
        self._wiki_hooks = wiki_hooks
        self._new_state = new_state
        self._wiki_for = wiki_for
        self._indexer = indexer

    def build(self, session_key: str, index_seen, workspace: Any = None, *,
              owner_id: int = 0) -> dict:
        wiki = self._wiki
        if workspace is not None and self._wiki_for is not None:
            wiki = self._wiki_for(workspace)
        return self._wiki_hooks(
            wiki.root, self._new_state(), index=wiki, seen=index_seen,
            session_key=session_key, webfetch_hint=True,
            indexer=self._indexer, owner_id=owner_id, workspace=workspace,
        )


class TelegramAskPresenter:
    """turns' `AskPresenter` over telegram's `TelegramQuestions`.

    `AskService` treats the presenter's return value as an opaque handle and
    dismisses with `dismiss(handle)`; `TelegramQuestions.dismiss` needs the chat
    too (editMessageReplyMarkup is addressed by chat + message). So the handle
    carries the chat id.
    """

    def __init__(self, questions: Any):
        self._questions = questions

    async def present(self, chat_id: int, thread_id: int | None, qid: str,
                      question: str, options: list[str]):
        sent = await self._questions.present(chat_id, thread_id, qid, question,
                                             options)
        return None if sent is None else (chat_id, sent)

    async def dismiss(self, handle) -> None:
        chat_id, sent = handle
        await self._questions.dismiss(chat_id, sent)


class SenderDeliverer:
    """scheduler's `ReportDeliverer` over telegram's `RichSender`. The argument
    order differs (a report names its chat and text first, the topic last)."""

    def __init__(self, sender: Any):
        self._sender = sender

    async def deliver(self, chat_id: int, text: str,
                      thread_id: int | None = None) -> None:
        await self._sender.send(chat_id, thread_id, text)


# `BoundApprovals` lived here until Wave 3a: it bound `home` into every call
# because the approval queue was a directory under it. The queue is a table now
# and `ApprovalService` satisfies telegram's `ApprovalPort` as it stands, so the
# adapter would have had nothing left to translate — `main.py` passes the service
# straight through.


class TranslatingSecrets:
    """telegram's `SecretStorePort` over extensions' `PgSecrets`.

    The two modules each own a `SecretNameError` and neither may import the
    other's, so the composition root translates. `error` is telegram's class.

    `put` is async because the store is a table: extensions' `SecretStore` port
    awaits, and the name check that raises happens inside that await.

    `owner_id` is required on both sides and simply forwarded: extensions' store
    keys `extension_secrets` by `(owner_id, name)`, and this adapter is the seam
    where `/secret` reaches it. Nothing is defaulted here — an adapter that
    invented an owner would seal one person's credential into another's row, and
    the keyword-only argument with no default is what makes a caller that forgets
    it a `TypeError` instead.
    """

    def __init__(self, secrets: Any, *, error: type[Exception]):
        self._secrets = secrets
        self._error = error

    async def put(self, name: str, value: str, *, owner_id: int) -> None:
        try:
            await self._secrets.put(name, value, owner_id=owner_id)
        except ValueError as exc:
            raise self._error(str(exc)) from exc


class RowedLaneModels:
    """telegram's `LaneModelsPort` over sessions' `SessionService`.

    The two agree on everything but one signal. Telegram reads "this lane has no
    thread row" from `effective` raising `NotFound` — `/model` in a chat nobody
    has spoken in answers "send a message here first" instead of offering a menu
    whose every tap would fail. `SessionService.effective` deliberately never
    raises: the TURN asks it before `threads.touch` creates the row on a lane's
    first message, and a raise there would cost that first turn the owner's own
    default. So the rowless case is spelled here, for telegram alone; `set_model`
    already raises `NotFound` and passes straight through.
    """

    def __init__(self, lanes: Any, threads: Any):
        self._lanes = lanes
        self._threads = threads

    async def effective(self, session_key: str, owner_id: int | None = None):
        if await self._threads.get(session_key) is None:
            raise NotFound("no such thread")
        return await self._lanes.effective(session_key, owner_id)

    async def set_model(self, session_key: str, model: str | None, effort: str | None):
        return await self._lanes.set_model(session_key, model, effort)


class MessageActivity:
    """learning's `ActivityPort` (`last_message_ts`) over sessions'
    `MessageRepository.last_ts`.

    Scoped to ONE user. The one consumer is the curator's quiet-hours check
    ("has this user talked recently?"), and since Wave 4b the curator is one
    pass per user over their own tree — a neighbour chatting at 3am must not
    hold it off, and this user's silence must not be hidden by someone else's
    traffic. `owner` is an ASYNC callable returning the id (advisor B-7: a bare
    `lambda: uid` would `TypeError` inside the swallowed pass and the curator
    would silently never run) — a closure over the scope's id from
    `main.build_app`'s `activity_for`. `0` means "unscoped": the unclaimed
    deploy, whose messages carry no owner.
    """

    def __init__(self, messages: Any, owner: Any = None):
        self._messages = messages
        self._owner = owner

    async def last_message_ts(self) -> str | None:
        owner_id = None
        if self._owner is not None:
            owner_id = int(await self._owner() or 0) or None
        return await self._messages.last_ts(owner_id=owner_id)


class LearnPromptBuilder:
    """telegram's `LearnPromptPort` over learning's `build_learn_prompt`.

    A function where the port wants an object, and nothing else — the wording of
    the prompt stays in `modules.learning.domain.prompts`."""

    def __init__(self, build):
        self._build = build

    def build(self, text: str) -> str:
        return self._build(text)


class LaneWorkspace:
    """telegram's `WorkspacePort` over files' inbox layout, per LANE OWNER.

    `lane_dir(root, session_key)` is a files rule (the sanitizing and the disk
    layout are that module's); what this adds is WHOSE root it is applied to.

    `IntakeService` registers an attachment through the `FileService` (a `files`
    row and, with `blob_backend: s3`, a copy in the bucket); this port answers the
    two questions that registration needs — where the lane's inbox is and whose
    row the file becomes.

    Async and lane-aware since Wave 4a (advisor blocker B5): with one bound `home`
    a non-owner's photo landed in the DEPLOY OWNER's `inbox/`, where the sender's
    own turn could not even read it. `resolver` is `core.workspace.WorkspaceResolver`
    and is required — a composition without one would be that bug, one mis-wire
    away.
    """

    def __init__(self, lane_dir, resolver: Any):
        self._lane_dir = lane_dir
        self._resolver = resolver

    async def lane_dir(self, session_key: str, channel: str = "telegram",
                       user_id: int = 0) -> Path:
        workspace = await self._resolver.for_lane(session_key, channel, user_id)
        return self._lane_dir(workspace.root, session_key)

    async def lane_owner(self, session_key: str, channel: str = "telegram",
                         user_id: int = 0) -> int:
        """Whose row an attachment on this lane becomes (`files.owner_id`).

        The same `lane_owner` rule the directory above is chosen by, so the row
        and the bytes cannot end up in different people's trees.
        """
        return lane_owner(session_key, channel, user_id,
                          await self._resolver.owner_id())


class WikiForkGuards:
    """learning's `WikiGuardsPort` over memory's hook factories.

    `raw/` immutability and the "update the index before you stop" gate are
    memory's rules; the review and consolidation forks install them. The
    factories arrive as arguments because `core` may not import a module.

    `index_flush` is the fifth, and the review fork is the one that most needs
    it: curation runs out of band and the fork is the wiki's main rewriter, so
    without a flush `memory_search` quotes a page the fork has already rewritten.
    Both it and `indexer` are optional — a composition that wired no index hands
    the fork `None` and its Stop list is unchanged.
    """

    def __init__(self, *, new_state, raw_guard, page_touch, stop_gate,
                 index_flush=None, indexer: Any = None):
        self._new_state = new_state
        self._raw_guard = raw_guard
        self._page_touch = page_touch
        self._stop_gate = stop_gate
        self._index_flush = index_flush
        self._indexer = indexer

    def new_turn_state(self):
        return self._new_state()

    def raw_guard(self, wiki_root: Path):
        return self._raw_guard(wiki_root)

    def page_touch(self, state, wiki_root: Path):
        return self._page_touch(state, wiki_root)

    def stop_gate(self, state):
        return self._stop_gate(state)

    def index_flush(self, state, owner_id: int, workspace: Any):
        """The Stop hook for a review fork, or `None` when no indexer is wired."""
        if self._index_flush is None or self._indexer is None:
            return None
        return self._index_flush(state, self._indexer, owner_id, workspace)

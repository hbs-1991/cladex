"""Everything `turns` needs from outside itself, stated as consumer-side Protocols.

A port is named for the capability, never for the provider: `SessionLane`, not
`SessionRouter`. Structural typing does the matching, so no other module is ever
imported here — the composition root hands in objects that happen to fit.

`None` is a meaningful value for most of these: the tool-registration gates in
`infrastructure/hermes_server.py` are driven by `port is not None`, and wiring a
stub instead of leaving a port `None` would expose a tool that must not exist
(see `ask_user`, which must not appear on a group lane).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, MutableMapping, Protocol, Sequence

from claude_hermes.core.model_catalog import Choice
from claude_hermes.modules.turns.domain.events import TurnEvent

# --- settings ---------------------------------------------------------------


@dataclass(frozen=True)
class TurnSettings:
    """The slice of `core.config.Config` a turn actually reads. A dataclass rather
    than the Config object so a new unrelated setting cannot change this module."""
    model: str = ""
    max_turns: int = 30
    web_tool_output_max_chars: int = 8000
    ask_user_max_per_turn: int = 2
    ask_user_timeout_seconds: float = 120.0
    backup_keep: int = 5
    ext_probe_timeout_seconds: float = 60.0
    plugin_clone_timeout_seconds: float = 120.0
    # No `users_shell_enabled` / `users_learning_enabled` here any more (0009).
    # Whether a non-owner lane gets a shell or the learning observer is a per-user
    # GRANT, read from PostgreSQL per turn through `LaneGrants` — a copy frozen
    # into this dataclass at boot is exactly what made a flip need a restart of
    # both the agent and the worker.
    #
    # Whether `mcp__hermes__memory_search` — the wiki index tool — is registered
    # at all. True by default: the tool is two fields and one sentence, and an
    # index nothing queries is an index nobody pays for the right way round.
    # Flipping it changes the MCP schema list, which is the cached prefix, so it
    # costs every lane one session reset (the toolset fingerprint in `main.run()`
    # is what performs it at boot).
    memory_search_enabled: bool = True
    # The models a lane may be switched to (`Config.models`), a tuple because this
    # class is frozen. The offer only; `core.model_catalog` knows what each one is.
    models: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, cfg: Any) -> "TurnSettings":
        if cfg is None:
            return cls()
        values = {f: getattr(cfg, f) for f in (
            "model", "max_turns", "web_tool_output_max_chars",
            "ask_user_max_per_turn", "ask_user_timeout_seconds", "backup_keep",
            "ext_probe_timeout_seconds", "plugin_clone_timeout_seconds",
            "memory_search_enabled", "models",
        ) if hasattr(cfg, f)}
        if "models" in values:
            values["models"] = tuple(values["models"] or ())
        return cls(**values)


# --- persistence ------------------------------------------------------------


class TurnEventRepository(Protocol):
    async def append(self, session_key: str, turn_id: str, seq: int, ts: str,
                     type: str, payload: dict) -> int: ...

    async def after(self, session_key: str, after_id: int = 0,
                    limit: int = 1000) -> list[Mapping]: ...


class TurnCostRepository(Protocol):
    """The spend ledger. Wave 4a stamps each row with the lane's owner and scopes
    the report to it: `cost_report` is offered to every lane, and a user must see
    their own spend, not the deploy's. `owner_id=None` is "every owner" — a
    fork with no lane."""

    async def record(self, ts: str, session_key: str, cost_usd: float, *,
                     owner_id: int | None = None) -> None: ...

    async def by_day(self, days: int = 7, *,
                     owner_id: int | None = None) -> list[Mapping]: ...


class SessionLane(Protocol):
    """The SDK session bound to one lane (provided by `sessions`).

    `index_mtime` rides along on `bind`/`touch` and comes back on the row from
    `route`: which wiki index version this lane has already been shown is a
    per-LANE fact, not a per-process one. It was a dict on `TurnService` — fine
    while one process served every lane, a measurable prompt-cache regression the
    moment N workers each keep their own copy. Both keywords default to `None`
    ("nothing to write"), so every existing caller is unchanged.

    `owner_id` (Wave 4a) rides along the same way and follows the same rule: the
    runner decides it once with `lane_owner` and hands it over, `None` leaves the
    column alone. `sessions` never derives it — that rule needs a deploy owner.

    `reset_owned` (Wave 4b, B-4) is the bulk reset for a change that touched ONE
    user's prompt prefix — a skill they edited — where `reset_all` is for a change
    that touched everyone's (an extension). `include_unowned` is the deploy
    owner's belt: a lane bound while the deploy was still unclaimed wrote NULL,
    and those lanes are theirs.

    `model` / `effort` ride along like `index_mtime`: they record the pair that
    ACTUALLY ran this session (`AssistantMessage.model`, never the requested id),
    which is what the resolver reads back as "what this live session runs" — a
    changed personal default must not move a session whose cached prefix is
    already model-scoped. `None` means "leave the column alone", so every caller
    that knows nothing about models is unchanged.

    `shell_enabled` (0009) rides along the same way: `TurnContext.has_shell` of
    the turn that started or continued the session, read back off the row by
    `route` at the next turn's start. A session whose stamp differs from the
    lane's current `has_shell` was built with another toolset, and the turn
    resets it (P1). The row key is `"shell_enabled"`; NULL means "started before
    0009", under the old deploy-wide rule.
    """

    async def route(self, session_key: str, now) -> Mapping | None: ...

    async def bind(self, session_key: str, sdk_session_id: str,
                   system_append: str, now_iso: str, *,
                   index_mtime: float | None = None,
                   owner_id: int | None = None,
                   model: str | None = None,
                   effort: str | None = None,
                   shell_enabled: bool | None = None) -> None: ...

    async def touch(self, session_key: str, now_iso: str, *,
                    index_mtime: float | None = None,
                    owner_id: int | None = None,
                    model: str | None = None,
                    effort: str | None = None,
                    shell_enabled: bool | None = None) -> None: ...

    async def reset(self, session_key: str) -> None: ...

    async def reset_all(self) -> None: ...

    async def reset_owned(self, owner_id: int, *,
                          include_unowned: bool = False) -> None: ...


class MessageLog(Protocol):
    """The searchable transcript. `owner_id` is the lane's owner (Wave 4a), so a
    row can be found again by exactly one person."""

    async def record(self, session_key: str, role: str, content: str, ts: str, *,
                     owner_id: int | None = None) -> None: ...

    async def last(self, session_key: str, role: str | None = None) -> Mapping | None: ...


class MessageSearch(Protocol):
    """What `session_search` (the recall tool) reads. The turn passes its OWN
    owner: a lane may only recall what its owner said, in one query."""

    async def search(self, query: str, limit: int = 8, *,
                     owner_id: int | None = None) -> list[Mapping]: ...


class LaneModels(Protocol):
    """Which (model, effort) this lane runs next, answered by `sessions`.

    One gatherer, one resolver: `SessionService.effective` reads the thread's
    explicit pair, the live session's recorded pair and the lane owner's personal
    default, and hands them to `core.model_catalog.resolve`. The turn asks the
    question once, before `turn.start`, and carries the answer on `TurnContext` —
    re-deriving it lower down would be a second answer to "which model is this",
    and `turn.start` has already told the user the first one.

    Returns a `core.model_catalog.Choice` (`model`, `cli_id`, `effort`). A turn
    built without this port runs the deploy default, which is every lane's
    behaviour before this feature.
    """

    async def effective(self, session_key: str, owner_id: int) -> Choice: ...


class LaneGrants(Protocol):
    """What the lane's owner may do beyond talking — a shell and the learning
    loop — answered by identity (`AccessControl.grants` / `grant_defaults`).

    Both return objects with `.shell` and `.learning` (identity's `Grants`,
    matched structurally — this module never imports identity). `grants` is the
    person's EFFECTIVE answer, their own choice or the config default; the turn
    asks it once per turn for a non-owner lane, inside the turn's error envelope,
    and never substitutes a default for a failed lookup — a guessed False would
    reset the lane under a notice that lies (P5). `grant_defaults` is the config
    default alone: what a session stamped before 0009 was started under.

    Read from PostgreSQL per call and never cached here: the agent and the worker
    are two processes, and a grant the owner flips must reach whichever of them
    runs the lane's next turn without a restart. A lane owned by the deploy owner
    never asks (`is_owner_lane` already gives it everything). `None` — a fork, a
    test — is a service in which non-owner lanes get neither.
    """

    async def grants(self, user_id: int) -> Any: ...

    async def grant_defaults(self) -> Any: ...


class ThreadRegistry(Protocol):
    """`owner_user_id` is `lane_owner(...)` since Wave 4a — an int for every lane,
    where the pre-4a `thread_owner` answered `None` for a group."""

    async def touch(self, session_key: str, channel: str, owner_user_id: int | None,
                    now_iso: str, *, talked: bool = False) -> None: ...


# --- the event bus ----------------------------------------------------------


class TurnEventBusPort(Protocol):
    """Exported BY turns as well as consumed: telegram, scheduler and sessions all
    emit onto it.

    `snapshot` and `is_running` are **async** since Wave 3b: the in-flight state
    they read lives in a store that may be Redis in another process, not in a dict
    on this object. `subscribe` stays **synchronous** — it never fails and never
    waits — and `ready` is the seam a caller that must not miss the first event
    awaits: in redis mode `subscribe` only schedules the SUBSCRIBE, and reading
    state before the subscription is live leaves a window in which an event is in
    neither place.
    """

    async def emit(self, session_key: str, turn_id: str, type: str,
                   payload: dict) -> TurnEvent: ...

    def subscribe(self, session_key: str, listener) -> Callable[[], None]: ...

    async def ready(self, session_key: str) -> None: ...

    async def snapshot(self, session_key: str) -> list[TurnEvent]: ...

    async def is_running(self, session_key: str) -> bool: ...


class EventPublisher(Protocol):
    """Fan-out onto one lane's channel — in-process, or Redis pub/sub."""

    def subscribe(self, session_key: str, listener) -> Callable[[], None]: ...

    async def ready(self, session_key: str) -> None: ...

    async def publish(self, event: TurnEvent) -> None: ...


class SnapshotStore(Protocol):
    """The in-flight mirror of every turn running in a lane.

    `record` applies the rules the bus's own bookkeeping used to: `turn.start`
    registers the turn, a persisted event is appended, a `text.delta` /
    `thinking.delta` extends the matching buffer, `text.block` / `thinking` clears
    it, `turn.end` drops the turn — **and every `record` stores `ev.seq` as that
    turn's latest seq**, because the synthetic deltas `read` produces carry that
    seq and the SSE watermark is written against it.

    `read` yields the persisted events of every active turn in start order, each
    followed by at most one synthetic `thinking.delta` and one `text.delta` at the
    turn's latest seq.
    """

    async def record(self, ev: TurnEvent) -> None: ...

    async def read(self, session_key: str) -> list[TurnEvent]: ...

    async def is_running(self, session_key: str) -> bool: ...

    async def clear(self, session_key: str, turn_id: str) -> None: ...


# --- the turn as a row and a task -------------------------------------------


class TurnStore(Protocol):
    """The `turns` table seen from here — the one source of truth for "is this
    lane busy", in every process.

    Every method is one statement in the implementation, and two signatures encode
    why: `claim` matches `queued` only (a `running` row with a lapsed lease is the
    reaper's, never a second execution), and `finish` carries `worker_id` and
    returns a bool, so a worker whose turn was reaped cannot flip the row terminal
    without anyone noticing.
    """

    async def create(self, turn_id: str, session_key: str, channel: str, user_id: int,
                     text: str, attachment_ids: list[str], now_iso: str,
                     lease_until: str) -> None: ...

    async def claim(self, turn_id: str, worker_id: str, now_iso: str,
                    lease_until: str) -> dict | None: ...

    async def heartbeat(self, turn_id: str, worker_id: str,
                        lease_until: str) -> dict | None: ...

    async def finish(self, turn_id: str, worker_id: str, status: str, *,
                     reply: str | None = None, error: str | None = None,
                     now_iso: str) -> bool: ...

    # The one terminal write that carries no `worker_id`: a row whose dispatch
    # raised was never claimed, so there is no worker to name. Narrowed to
    # `queued` in the implementation for exactly that reason.
    async def fail_queued(self, turn_id: str, error: str,
                          now_iso: str) -> bool: ...

    async def request_cancel(self, session_key: str, now_iso: str,
                             turn_id: str | None = None) -> dict | None: ...

    async def active(self, session_key: str) -> dict | None: ...

    async def active_lanes(self, session_keys: list[str]) -> set[str]: ...

    async def expired(self, now_iso: str, limit: int = 100) -> list[dict]: ...

    async def mark_lost(self, turn_id: str, now_iso: str) -> bool: ...

    async def prune(self, before_iso: str, limit: int = 1000) -> int: ...

    async def get(self, turn_id: str) -> dict | None: ...


class TurnDispatcher(Protocol):
    """Put a created turn on the queue. One primitive, because the argument list
    has to survive being JSON on a Redis list — everything else is on the row."""

    async def dispatch(self, turn_id: str) -> None: ...


# --- the turn's collaborators ----------------------------------------------


class WorkspacePort(Protocol):
    def agent_home(self) -> Path: ...

    def root(self) -> Path: ...


class WorkspaceResolverPort(Protocol):
    """Which workspace a lane — or a user — runs in (`core.workspace`).

    Consumed by `TurnRunService`, which holds a `turns` row and therefore knows
    the key, the channel and the sender, and by nothing else in this module: the
    resolution happens ONCE, at the top of the worker's half, and travels down on
    `TurnContext.workspace`. `for_lane` deliberately reads no table — it applies
    the same `lane_owner` rule `sessions.owner_id` is written from — so a turn on
    a brand-new lane resolves exactly like a turn on an old one.
    """

    async def for_lane(self, session_key: str, channel: str,
                       user_id: int) -> Any: ...

    async def for_user(self, user_id: int) -> Any: ...

    async def owner_id(self) -> int: ...


class WikiPort(Protocol):
    def index(self) -> str: ...

    def index_mtime(self) -> float: ...

    @property
    def root(self) -> Path: ...


class WikiSearch(Protocol):
    """Full-text + link-hop search over the lane owner's wiki index (and the
    shared namespace). Satisfied by memory's `PgWikiIndex`.

    The rows are the derived index over the markdown tree, never the tree itself:
    a missing or stale row is a cache miss, not an error, so this port is allowed
    to answer nothing and the tool says so in words the model can act on.

    `owner_id` is the LANE's owner, decided once by the runner — the same number
    `session_search` scopes to. The store answers `owner_id = :me OR shared`, so
    a hit may belong to somebody else (the deploy owner's `shared/` pages) and
    every row carries its own `owner_id` for exactly that reason: identity is
    `(owner_id, path)`, never `path` alone.
    """

    async def search(self, query: str, *, owner_id: int, limit: int = 8,
                     depth: int = 1) -> Sequence[Mapping[str, Any]]: ...


class TurnHooksProvider(Protocol):
    """Hook matchers for one turn, opaque to `turns`: they are SDK objects built by
    `memory`, and the shape is the SDK's, not ours.

    `workspace` since Wave 4a: the wiki the hooks guard and the index they
    re-inject are the LANE OWNER's, not the deploy owner's. Without it user B's
    Stop gate would nag about — and their `Write` would reach — the owner's wiki
    through a path the read guard never sees (advisor blocker B1).

    `owner_id` is what the Stop-hook flush stamps the index rows it writes with.
    It is keyword-only and defaults to `0` — "no lane owner, do not index" — so a
    fork context that never resolved one cannot silently write another user's
    pages under a wrong id. A lane that HAS a workspace and passes `0` is the
    call site that forgot the keyword, and memory logs it once per process.
    """

    def build(self, session_key: str, index_seen: MutableMapping[str, float],
              workspace: Any = None, *,
              owner_id: int = 0) -> Mapping[str, Sequence[Any]]: ...


class TurnObserver(Protocol):
    """The learning loop's view of a finished turn.

    `owner_id` (Wave 4b, D3) is the LANE's owner, so the observer can bump that
    user's sidecar and counters rather than the deploy owner's. The default,
    `0`, is what a caller with no lane passes and reads as "the owner" on the
    other side.
    """

    async def after_turn(self, session_key: str, stats, *,
                         owner_id: int = 0) -> None: ...


class ExtensionComposePort(Protocol):
    async def for_turn(self, *, owner_id: int) -> Any:
        """Something with `.tool_names`, `.mcp_servers` and `.plugins`, composed for
        the LANE's owner — a turn runs as whoever owns the lane, never as its sender.
        """


class SlashCommandsPort(Protocol):
    """What the CLI itself reported in the last turn's `init` message.

    **Async** since Wave 3b: the worker that ran the last turn is not the process
    serving `GET /api/commands`, so this is a read of shared state and no longer
    an attribute on an object this process happens to hold.
    """

    async def sdk_commands(self) -> list[str]: ...


class SlashCommandsStore(Protocol):
    """The same list from the writing side: the turn service saves what the CLI
    reported so that any process can serve it."""

    async def sdk_commands(self) -> list[str]: ...

    async def save(self, names: list[str]) -> None: ...


class JobLookup(Protocol):
    """Only what the cron-context bridge needs; the full scheduler API is a
    separate port so a fork that cannot create jobs can still read one."""

    async def job_for_thread(self, chat_id: int, thread_id: int) -> Mapping | None: ...


class TurnExecutor(Protocol):
    """The SDK loop, isolated so the application layer never imports the SDK."""

    async def execute(self, *, user_text: str, resume_id: str | None, append: str,
                      session_key: str, ctx, attachments,
                      index_seen: MutableMapping[str, float],
                      on_slash_commands: Callable[[list[str]], None],
                      on_session_id: Callable[[str], None] | None = None) -> tuple:
        """-> (final_text, sdk_session_id, TurnStats, cost_usd, usage_wire, ran_model).

        `ran_model` is the catalog id of the model that ACTUALLY answered
        (`lookup(AssistantMessage.model).id` of the last main-loop message), which
        is what `turn.end` names and what the session row records — never the id
        the call asked for. `""` when the call reported no model at all.

        Raises `ModelRejected` when the API refused that model before any tool ran
        (the caller re-runs the turn once on the deploy default), and
        `CliCallFailed` when an errored `AssistantMessage` said more than the
        SDK's own exception text does.
        """

    def is_cli_death(self, error: Exception) -> bool: ...


# --- tool-backing ports -----------------------------------------------------


class SchedulerPort(Protocol):
    """The `cron_manage` tool's body, owned by `scheduler`."""

    async def handle(self, args: Mapping, *, ctx) -> str: ...


class SkillAdminPort(Protocol):
    """The `skill_admin` tool's body, owned by `learning`.

    `ctx` (Wave 4b, D6) is how the body learns WHOSE library it administers:
    `ctx.owner_id` picks the lane owner's scope. `None` is a
    handler with no turn, which reads as the deploy owner's.
    """

    async def handle(self, args: Mapping, *, ctx=None) -> str: ...


class ExtensionAdminPort(Protocol):
    """The `ext_manage` tool's body, owned by `extensions`. Returns the reply text
    and whether the mutation invalidated this lane's cached prefix."""

    async def handle(self, args: Mapping, *, ctx) -> tuple[str, bool]: ...


class ContentPlanPort(Protocol):
    """The `content_plan` tool — body AND declaration — owned by `content`.

    Declared the `ext_manage` way: `name`, `description` and `schema` are read off
    this object by `hermes_server.declared_spec`, because the same tool is also
    built, by the content module itself, into the `hermes` server of the deploy
    owner's routine runs, and one tool must not be spelled twice.

    Unlike the other delegated bodies it takes plain values rather than `ctx`, and
    it answers the MCP result dict itself (`{"content": [...]}`, plus
    `"is_error": True` on a refusal): the same body serves a routine run, which
    has no `TurnContext` at all. `owner_id` is the LANE's owner; `origin` is
    `"chat"` here (a routine run passes `"cron:<job_id>"`) and becomes the item's
    `created_via`; `thread_id` is the session key the item links back to.
    """

    name: str
    description: str
    schema: Mapping

    async def handle(self, args: Mapping, *, owner_id: int, origin: str,
                     thread_id: str | None) -> dict: ...


class FilePublisher(Protocol):
    """`files` seen from here: publish what the agent wrote, and turn an id a
    client sent back into a file on disk.

    `resolve` is async as of Wave 3a — the blob store behind it may have to
    fetch the object before there is a path to hand out.

    `owner_id` on `publish` is the LANE's owner (Wave 4a): it decides which
    user's `agent_home/outbox/` the blob mirror lands in, and it is what the row
    records. `None` means the deploy owner, which is every pre-4a caller.
    """

    async def publish(self, session_key: str, path: Path, *,
                      owner_id: int | None = None) -> Mapping: ...

    def to_wire(self, record: Mapping) -> dict: ...

    async def get(self, file_id: str) -> Mapping | None: ...

    async def resolve(self, record: Mapping) -> Path: ...


class DocumentDeliveryPort(Protocol):
    async def send_document(self, chat_id: int, thread_id: int | None, path: str,
                            caption: str | None = None) -> None: ...


class AskPresenterPort(Protocol):
    """Puts a question in front of the user and waits for the tap. The wait itself
    lives in `infrastructure/ask_broker.py`; this is the channel half."""

    async def __call__(self, chat_id: int, thread_id: int | None, question: str,
                       options: list[str], user_id: int, timeout: float,
                       emit=None) -> str | None: ...


class AskBrokerPort(Protocol):
    """The pending-question broker, which is Redis-backed in redis mode: the
    worker that opened the question is not the process a browser answers it to.

    `open`, `wait`, `options_for` and `resolve` are async for that reason.
    **`discard` and `deliver` stay synchronous, and that is load-bearing.**
    `discard` is called from `AskService.ask`'s `finally`, where a
    `CancelledError` can land — an `await` there is skipped and the entry leaks
    forever (the comment in `ask_service.py` was paid for with a live bug). So
    `discard` pops the local entry synchronously and leaves any remote cleanup to
    a fire-and-forget task. `deliver` is the control channel's entry point — the
    answer arriving from another process — and is a no-op when the qid is not
    pending here.

    `ttl_seconds` on `open` is how long a remote broker keeps the question; the
    in-memory one accepts and ignores it, because the `wait` timeout already is
    its lifetime.
    """

    async def open(self, options: list[str], user_id: int, *,
                   ttl_seconds: float | None = None) -> tuple[str, Any]: ...

    async def wait(self, qid: str, fut, timeout: float) -> str | None: ...

    async def options_for(self, qid: str) -> list[str] | None: ...

    async def resolve(self, qid: str, token: str, user_id: int) -> bool: ...

    def deliver(self, qid: str, answer: str | None) -> bool: ...

    def discard(self, qid: str) -> None: ...


class VoicePort(Protocol):
    async def transcribe(self, audio: bytes, filename: str = "audio.ogg") -> str: ...

    async def synthesize(self, text: str) -> bytes: ...


class SlashSourcePort(Protocol):
    """Where the `/` menu's plugin half comes from — `extensions` knows which
    plugins are enabled and where each one was cloned."""

    async def plugin_roots(self) -> list[tuple[Path, str]]:
        """(clone root, fallback namespace) for every ENABLED plugin."""


class ThreadAccess(Protocol):
    """The owned-thread guard the web routes apply before touching a lane."""

    async def __call__(self, request, thread_id: str) -> Mapping: ...


class ThreadTitles(Protocol):
    def __call__(self, session_key: str, text: str) -> None: ...


class AclPort(Protocol):
    async def is_owner(self, user_id: int) -> bool: ...


Unsubscribe = Callable[[], None]
Emit = Callable[[str, dict], Awaitable[None]]

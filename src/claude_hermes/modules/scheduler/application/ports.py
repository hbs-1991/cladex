"""What the scheduler needs from the outside world, stated on the consumer side.

Every one of these is satisfied structurally: the composition root hands in an
object with the right methods and nothing here ever learns which module it came
from. `ReportDeliverer` and `TopicProvisioner` are the two that only exist after
the aiogram Bot does, which is why `SchedulerService` accepts them as None and
takes them later through `bind_delivery()` — `build_app()` stays headless.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from claude_hermes.core.model_catalog import lookup

# What a `claim_due` `next_of` answers for a job that has no next fire and is NOT
# spent: an on-demand (`manual`) routine, which goes back to waiting with
# `next_run_at` NULL and stays `active`. `None` keeps its old meaning — spent, the
# row goes `done` — so the repository can tell the two apart without importing a
# schedule. A string, so the `Callable[[dict], str | None]` shape is unchanged; no
# ISO timestamp can ever spell it.
STAY_IDLE = "stay-idle"


@runtime_checkable
class JobRepository(Protocol):
    """Persistence for jobs and their run history. Provided by this module's own
    infrastructure; declared as a port so the service can be tested against a
    dict-backed fake and so Wave 3 can swap in a leasing implementation.

    Wave 4a: `owner_id` is whose routine it is. On `create` it is written; on
    `get`/`list_jobs` it is an optional SCOPE, and the two callers differ on
    purpose — a request path always names the asking user, while the ticker, the
    dispatcher and the executor pass nothing, because they run a claimed fire and
    there is nobody asking.
    """

    async def create(
        self, name: str, kind: str, value: str, prompt: str,
        deliver_chat_id: int, origin_session_key: str, next_run_at: str | None,
        deliver_thread_id: int | None = ..., timeout_seconds: int | None = ...,
        owner_id: int | None = ..., model: str | None = ...,
        effort: str | None = ...,
    ) -> int: ...

    async def get(self, job_id: int, owner_id: int | None = ...) -> dict | None: ...

    async def by_thread(self, chat_id: int, thread_id: int) -> dict | None: ...

    async def list_jobs(self, owner_id: int | None = ...) -> list[dict]: ...

    async def due(self, now_iso: str) -> list[dict]: ...

    async def claim_due(
        self, now_iso: str,
        next_of: Callable[[dict], str | None] | None = ...,
        limit: int = ...,
    ) -> list[dict]: ...

    async def clear_lease(self, job_id: int) -> None: ...

    async def reschedule(self, job_id: int, next_run_at: str | None) -> None: ...

    async def set_state(self, job_id: int, state: str) -> None: ...

    async def update(self, job_id: int, **fields: Any) -> None: ...

    async def record_run(self, job_id: int, ts: str, status: str) -> None: ...

    async def runs(self, job_id: int, limit: int = ...) -> list[dict]: ...

    async def runs_between(self, owner_id: int, start_iso: str,
                           end_iso: str) -> list[dict]: ...

    async def wake_if_idle(self, job_id: int, now_iso: str) -> bool: ...

    async def delete(self, job_id: int) -> None: ...


@dataclass(frozen=True)
class JobOutcome:
    """What one unattended run produced, and on which model.

    `model`/`effort` are the pair the executor RESOLVED for the run (the job's own,
    then `cron_model`, then `model` — `core.model_catalog.resolve`); `ran` is the
    model that actually answered (`AssistantMessage.model`), empty when nothing
    answered at all (a timeout, a refused prompt, an executor that reports none).
    The service puts the first pair on the cron `turn.start` and the second on
    `turn.end`, so the report names the model it came from rather than the one that
    was asked for.
    """

    status: str
    text: str
    model: str = ""
    effort: str | None = None
    ran: str = ""

    @classmethod
    def of(cls, result) -> JobOutcome:
        """Accept an executor that still answers with the bare `(status, text)` this
        port had before models were a choice — a fake, or a deployment whose executor
        predates the column. It reports no model, which reads as "unknown" downstream."""
        return result if isinstance(result, cls) else cls(*result)

    @property
    def model_that_ran(self) -> str:
        """What to name on `turn.end`: the model that answered, or — when nothing
        did — the one the run was resolved to."""
        return self.ran or self.model

    @property
    def fallback_from(self) -> str | None:
        """The resolved model, when something else answered instead of it (the CLI's
        own substitution, or a fallback); `None` on every ordinary run. Compared
        through `lookup` so an alias of the same model is not a substitution."""
        if not self.ran or not self.model:
            return None
        return self.model if lookup(self.ran).id != lookup(self.model).id else None


@runtime_checkable
class JobExecutor(Protocol):
    """Runs one job's prompt in an unattended fork. Returns the outcome, never raises:
    a failed job is a report the user reads, not a crashed ticker."""

    async def run(self, job: dict) -> JobOutcome: ...


@runtime_checkable
class JobDispatcher(Protocol):
    """Hands one claimed fire to whatever will run it.

    The split between "decide what fires" and "run it" is what lets a worker
    process do the running: `tick` claims, then dispatches primitives — an id and
    the ISO instant the claim consumed, never the row and never a service. The
    composition root satisfies this with the module's `QueueJobDispatcher` over
    `core.tasks.TaskQueue`; a `SchedulerService` built without one runs the job in
    the ticker's own coroutine, which is what the single-process deployment did
    before the queue existed.
    """

    async def dispatch(self, job_id: int, fire_at: str) -> None: ...


@runtime_checkable
class ReportDeliverer(Protocol):
    """Pushes a finished report into a chat (Telegram today)."""

    async def deliver(self, chat_id: int, text: str, thread_id: int | None = ...) -> None: ...


@runtime_checkable
class TopicProvisioner(Protocol):
    """Opens and closes the per-job forum topic its reports are delivered to.
    `create` returns None when the chat cannot host topics — the job then falls
    back to flat delivery, which is a degradation, not a failure."""

    async def create(self, chat_id: int, name: str) -> int | None: ...

    async def close(self, chat_id: int, thread_id: int) -> None: ...


@runtime_checkable
class TurnEventPublisher(Protocol):
    """Publishes turn events onto a lane (the TurnBus today, Redis in Wave 3)."""

    async def emit(self, session_key: str, turn_id: str, type: str,
                   payload: dict) -> None: ...


@runtime_checkable
class ThreadRegistry(Protocol):
    """Creates or bumps the `threads` row for a lane. Owned by the sessions module;
    the scheduler only ever needs to say "this lane exists / somebody talked here"."""

    async def ensure(self, session_key: str, channel: str, owner_id: int | None,
                     now_iso: str, *, talked: bool = ...) -> None: ...


@runtime_checkable
class MessageRecorder(Protocol):
    """Appends to a lane's searchable transcript. Owned by the sessions module."""

    async def record(self, session_key: str, role: str, content: str,
                     ts: str, *, owner_id: int | None = None) -> None: ...


@runtime_checkable
class OwnerGrants(Protocol):
    """What the owner has granted one user, resolved at fire time. Owned by the
    identity module (`AccessControl.grants`), which alone knows the rule — the
    user's own column, else the deploy default — so the executor never reads
    `users.shell_enabled` or `users_shell_enabled` itself.

    Answers an object with `.shell` and `.learning`; a routine reads `.shell`
    only. Typed `Any` because the value is identity's own frozen type and naming
    it here would be the module-to-module import the contracts forbid. Asked only
    for a non-owner's routine: the deploy owner's reach comes from their lane.
    """

    async def grants(self, user_id: int) -> Any: ...


@runtime_checkable
class ComposedExtensions(Protocol):
    """What an extension composition contributes to an unattended fork."""

    mcp_servers: dict
    tool_names: list
    plugins: list


@runtime_checkable
class ExtensionComposer(Protocol):
    """Resolves the extensions in scope for a fork. Owned by the extensions module."""

    async def for_scope(self, scope: str, *, owner_id: int) -> ComposedExtensions: ...


@runtime_checkable
class RoutineTools(Protocol):
    """In-process MCP tools another module lends to an unattended run. Owned by the
    content module today (`content_plan`, in its own server named `hermes` so the
    tool is `mcp__hermes__content_plan` in a routine exactly as it is in chat).

    Answers `(mcp_servers, tool_names)`: the servers merge into the fork's
    `mcp_servers` AFTER the composed extensions' — so an extension that happens to
    be named `hermes` cannot shadow the lent tool — and the names are appended to
    `allowed_tools`. `({}, [])` for anyone but the deploy owner, or when the lending
    module is switched off. The executor never asks for `owner_id <= 0` (a routine
    whose owner nobody resolved) and passes the resolved workspace's verdict as
    `is_deploy_owner`, the same one that gives the owner's routine its shell.
    """

    def for_owner(self, owner_id: int, *, is_deploy_owner: bool,
                  job_id: int) -> tuple[dict, list[str]]: ...

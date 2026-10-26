"""What the turns routers ask for, and where it comes from.

One typed provider per collaborator, built once at module scope out of
`core.di.provide` (FastAPI keys `dependency_overrides` by the callable *object*, so a
provider rebuilt per request could never be overridden). A handler writes
`runner: TurnService = Depends(get_runner)` and never sees the bundle.

Bundle attributes read by this module — the list `modules/turns/README.md` publishes:

`owned`, `auto_title`, `turns`, `runner`, `bus`, `turn_events`, `file_store`,
`broker`, `voice`, `home`, `workspaces`, `slash_sources`, `sdk_commands`, `cfg`.

Six of them are `required=False` because `None` is a documented answer the handler
renders itself, not a mounting mistake: a deployment with no voice key answers
`503 voice_disabled` through `VoiceService`, a deployment with no ask broker answers
`503 unavailable "questions are not wired"`, `home`/`slash_sources` are how
`SlashCommandsService` says "no skills, no plugins", a `None` `workspaces` is
`get_caller_home` falling back to `home`, and a `None` `sdk_commands` is
"nothing recorded yet" — the same empty list a deployment that never ran a turn
answers.

`turns` is required. It is the `TurnLauncher`, and without it `POST …/messages` has
nowhere to put the row a worker will claim; `503 unavailable` says the shell and this
module disagree about that, which beats an `AttributeError` rendered as a 500.

The two `*_for(deps, ...)` helpers at the bottom keep a bundle-shaped signature for
`sse.event_stream(deps, ...)`, which the tests iterate directly without HTTP. Each is
a thin wrapper over the port-shaped function the router uses.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import Depends
from starlette.requests import Request

from claude_hermes.core.config import Config
from claude_hermes.core.contracts import IMAGE_TYPES, Attachment
from claude_hermes.core.di import provide
from claude_hermes.core.exceptions import ApplicationException

from ..application.ports import (
    AskBrokerPort,
    FilePublisher,
    SlashCommandsPort,
    SlashSourcePort,
    ThreadAccess,
    ThreadTitles,
    TurnEventBusPort,
    TurnEventRepository,
)
from ..application.turn_service import TurnService
from ..application.voice_service import VoiceService

__all__ = ["StreamDeps", "TurnLauncherPort", "events_after",
           "get_auto_title", "get_broker", "get_bus", "get_caller_home", "get_cfg",
           "get_events", "get_files", "get_home", "get_owned", "get_runner",
           "get_sdk_commands", "get_slash_sources", "get_turns", "get_voice",
           "owned_thread", "resolve_attachments"]


class TurnLauncherPort(Protocol):
    """The three things a route asks of `application/launcher.py::TurnLauncher`.

    A consumer-side port declared here rather than in `application/ports.py`,
    because these three methods are the whole of what presentation can reach: the
    launcher's `wait` belongs to the Telegram adapter and `reap` to the scheduler,
    and no HTTP handler may call either. Structural typing does the matching, so
    this module never imports the class it is handed — which is also what lets a
    test pass a fake with three methods and nothing else.
    """

    async def start(self, session_key: str, text: str, *, user_id: int,
                    attachments: list[Attachment], channel: str) -> str: ...

    async def is_busy(self, session_key: str) -> bool: ...

    async def cancel(self, session_key: str, turn_id: str | None = None) -> bool: ...


_owned = provide("owned")
_auto_title = provide("auto_title")
_turns = provide("turns")
_runner = provide("runner")
_bus = provide("bus")
_turn_events = provide("turn_events")
_file_store = provide("file_store")
_broker = provide("broker", required=False)
_voice = provide("voice", required=False)
_home = provide("home", required=False)
_workspaces = provide("workspaces", required=False)
_slash_sources = provide("slash_sources", required=False)
_sdk_commands = provide("sdk_commands", required=False)
_cfg = provide("cfg")


def get_owned(request: Request) -> ThreadAccess:
    """`deps.owned` — the lane guard, `async (request, thread_id) -> row`.

    A callable rather than the `ThreadService` behind it because the question is about
    *this* request: the guard reads the user the middleware put on `request.state`, and
    a service may not read a Request. `owned_thread` below is the dependency handlers
    actually take.
    """
    return _owned(request)


async def owned_thread(id: str, request: Request,
                       owned: ThreadAccess = Depends(get_owned)) -> Mapping:
    """The thread row for `{id:path}`, or `NotFound` — the first line of all four
    thread routes, spelled once.

    `id` is resolved from the path by FastAPI (the `:path` convertor hands over the
    whole tail, session keys contain colons and slashes), so a handler that only needs
    the row does not declare it.
    """
    return await owned(request, id)


def get_auto_title(request: Request) -> ThreadTitles:
    """`deps.auto_title` — fire-and-forget naming of a thread from its first message."""
    return _auto_title(request)


def get_turns(request: Request) -> TurnLauncherPort:
    """`deps.turns` — the `TurnLauncher`: `is_busy` / `start` / `cancel`.

    Since Wave 3b a POST does not run a turn, it creates a row and enqueues a task;
    the busy question and the cancel flag are both answered by that row, so they are
    one object's business and no route reaches the runner for them any more.
    """
    return _turns(request)


def get_runner(request: Request) -> TurnService:
    """`deps.runner` — the in-process `TurnService`.

    No route in this module reads it (a POST creates a row and enqueues a task, see
    `get_turns`); it is here because the bundle carries it for learning's
    `reset_owned` and extensions' `reset_all`, and the name is published once.
    """
    return _runner(request)


def get_bus(request: Request) -> TurnEventBusPort:
    """`deps.bus` — `subscribe` / `ready` / `snapshot`, for SSE.

    Not the busy check any more: the `turns` row is the one source of truth for "is
    this lane busy" in every process, and a bus only knows what its own process saw.
    """
    return _bus(request)


def get_events(request: Request) -> TurnEventRepository:
    """`deps.turn_events` — the persisted turn events behind `GET …/events` and the
    SSE replay."""
    return _turn_events(request)


def get_files(request: Request) -> FilePublisher:
    """`deps.file_store` — `get`/`resolve`, to turn attachment ids into files."""
    return _file_store(request)


def get_broker(request: Request) -> AskBrokerPort | None:
    """`deps.broker`, or `None` on a deployment that wired no ask broker.

    `required=False`: the handler answers `503 unavailable "questions are not wired"`
    itself, and that message is part of this router's wire contract.
    """
    return _broker(request)


def get_voice(request: Request) -> VoiceService:
    """`deps.voice` wrapped in its service, which owns the "not configured" answer.

    `required=False` on the attribute: a deployment without an OpenAI key answers
    `503 voice_disabled` from `VoiceService`, not the provider's generic `unavailable`.
    """
    return VoiceService(_voice(request))


def get_home(request: Request) -> Path | None:
    """`deps.home` — where `agent_home/.claude/skills` is looked for. `None` is the
    answer `SlashCommandsService` renders as "no skills".
    """
    return _home(request)


async def get_caller_home(request: Request) -> Path | None:
    """The requesting user's tree root, or `deps.home` when no resolver is bound.

    `deps.home` is the deployment's, which for the deploy owner is the same path
    and for everybody else is somebody else's skills.
    """
    workspaces = _workspaces(request)
    if workspaces is None:
        return _home(request)
    ws = await workspaces.for_user(request.state.user_id)
    return ws.root


def get_sdk_commands(request: Request) -> SlashCommandsPort | None:
    """`deps.sdk_commands` — the names the CLI reported, read from wherever they were
    stored, because the worker that learned them is not this process.

    `required=False`: `None` means nothing is bound, which the commands router
    renders as the empty list a deployment that has never run a turn answers.
    """
    return _sdk_commands(request)


def get_cfg(request: Request) -> Config:
    """`deps.cfg` — read per request, never captured, so `GET /api/models` answers
    from the config the process holds NOW rather than from the one the app was
    built with. Only `models`, `model` and `review_model` are read: which models
    are offered, the deployment's default, and the model the review fork resumes
    lanes on."""
    return _cfg(request)


def get_slash_sources(request: Request) -> SlashSourcePort | None:
    """`deps.slash_sources` — where the `/` menu's plugin half comes from. `None` on a
    deployment with no extensions, which `SlashCommandsService` reads as "no plugins".
    """
    return _slash_sources(request)


@dataclass(frozen=True)
class StreamDeps:
    """The two collaborators `sse.event_stream` reads off its `deps` argument.

    `event_stream(deps, key, after)` takes a bundle because the tests iterate it
    directly, without HTTP (httpx's ASGITransport buffers whole responses and cannot
    consume an endless stream). The SSE route builds this two-field stand-in out of its
    own providers instead of handing over `app.state.deps`, so a test that overrides
    `get_bus` or `get_events` reaches the stream as well.
    """

    bus: TurnEventBusPort
    turn_events: TurnEventRepository


async def resolve_attachments(files: FilePublisher, session_key: str,
                              ids, *, owner_id: int | None = None) -> list[Attachment]:
    """Resolve uploaded file ids to attachments — refusing any id that belongs to
    another lane, which is the whole point of doing it here rather than trusting
    the request body.

    `owner_id` (Wave 4a) is the lane's owner off the thread row, checked as an
    AND with the lane: a row stamped with another owner — or with none — is
    refused even when the keys agree. `None` skips it only for a caller that
    has no thread row in hand.
    """
    out = []
    for fid in ids:
        rec = await files.get(str(fid))
        if rec is None or rec["session_key"] != session_key:
            raise ApplicationException(400, "bad_request", f"unknown attachment {fid}")
        if owner_id is not None and rec.get("owner_id") != owner_id:
            raise ApplicationException(400, "bad_request", f"unknown attachment {fid}")
        path = await files.resolve(rec)
        out.append(Attachment(path=path, filename=rec["name"], media_type=rec["media_type"],
                              is_image=rec["media_type"] in IMAGE_TYPES, file_id=rec["id"]))
    return out


async def events_after(deps, session_key: str, after_id: int = 0, limit: int = 1000):
    """Persisted turn events off a bundle (`deps.turn_events`).

    The router reads `get_events` instead; this stays for `sse.event_stream`, whose
    `deps` argument is the two-field `StreamDeps` above.
    """
    return await deps.turn_events.after(session_key, after_id=after_id, limit=limit)

"""Turns over HTTP: start one, read its history, tail it over SSE, cancel it,
answer an ask_user question.

Route paths, wire shapes and error `code` strings are frozen (spec req. 36). These
routes must be registered BEFORE the threads routes: `/api/threads/{id}/messages`
would otherwise be shadowed by `/api/threads/{id}`. The shell keeps that order in its
`include_router` list.

Dependencies: `owned_thread` (the lane guard, which is the 404 on someone else's
thread), `get_turns`, `get_bus`, `get_events`, `get_files`, `get_auto_title` and
`get_broker` — see `dependencies.py`. `core.auth.current_user` replaces the
`request.state.user_id` every handler used to read; there is no owner gate here, an
allowlisted user owns their own lanes.

Since Wave 3b **no handler here runs a turn.** `post_message` inserts a `turns` row
and enqueues the task; a worker (or, inline, the same process through `InlineQueue`)
claims it and runs the SDK loop. The route returns 202 and the browser watches the
lane over SSE, exactly as it did when the coroutine was local. That is why the
background-task set this module used to keep is gone: nothing here outlives the
response any more, so nothing here can be lost when the process restarts.
"""
from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, Response, StreamingResponse

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.core.model_catalog import is_model_command

from ..application.ports import (
    AskBrokerPort,
    FilePublisher,
    ThreadTitles,
    TurnEventBusPort,
    TurnEventRepository,
)
from ..domain.exceptions import LaneBusy, TurnNotRunning
from .dependencies import (
    StreamDeps,
    TurnLauncherPort,
    get_auto_title,
    get_broker,
    get_bus,
    get_events,
    get_files,
    get_turns,
    owned_thread,
    resolve_attachments,
)
from .schemas import AnswerIn, MessageIn
from .sse import event_stream

router = APIRouter(tags=["turns"])


@router.post("/api/threads/{id:path}/messages", operation_id="turns_send_message")
async def post_message(body: MessageIn,
                       t: Mapping = Depends(owned_thread),
                       user: CurrentUser = Depends(current_user),
                       turns: TurnLauncherPort = Depends(get_turns),
                       files: FilePublisher = Depends(get_files),
                       auto_title: ThreadTitles = Depends(get_auto_title)) -> JSONResponse:
    """Queue a turn on this lane: 202, and a worker runs it.

    The `is_busy` call is a **courtesy**, not the guarantee: the unique partial index
    on `turns(session_key) WHERE status IN ('queued','running')` is what makes one
    turn per lane true, and it answers a lost race with `LaneBusy` — which is
    `409 busy` with the same message, because `LaneBusy` carries it. Checking first
    only means the common case never has to raise.

    Attachments are resolved **here** and not in the worker's task arguments, because
    this is where the request's user is known and where "that file belongs to another
    lane" is a 400 somebody sees. The worker re-checks them against the row's lane.
    """
    key = t["session_key"]
    text = str(body.text).strip()
    ids = body.attachment_ids or []
    if not text and not ids:
        raise ApplicationException(400, "bad_request", "text or attachments required")
    if is_model_command(text):
        # The CLI runs `/model` and `/effort` as its own local commands in
        # non-interactive mode, which would move this session's model with nothing
        # recorded on any row and nothing shown anywhere. The composer opens the
        # picker instead of sending; this is the answer for a client that sent it
        # anyway. `/models` is an ordinary message and reaches the agent.
        raise ApplicationException(400, "model_command",
                                   "use the model picker to change the model")
    if await turns.is_busy(key):
        raise LaneBusy("the agent is busy in this thread")
    attachments = await resolve_attachments(files, key, ids,
                                            owner_id=t.get("owner_user_id"))
    if not t["title"]:
        auto_title(key, text or attachments[0].filename)
    turn_id = await turns.start(key, text, user_id=user.user_id,
                                attachments=attachments, channel="web")
    return JSONResponse({"accepted": True, "thread_id": key, "turn_id": turn_id},
                        status_code=202)


@router.post("/api/threads/{id:path}/cancel", operation_id="turns_cancel_turn")
async def cancel(t: Mapping = Depends(owned_thread),
                 turns: TurnLauncherPort = Depends(get_turns)) -> Response:
    """Stop the turn in flight. 409 when there is none.

    Cancelling by lane, not by turn id: that is the shape the SPA sends and it can
    hit a newer turn if the previous one finished between the click and the UPDATE.
    The race predates this wave; the launcher takes an optional `turn_id` for the
    day the SPA sends the one it is watching.
    """
    if not await turns.cancel(t["session_key"]):
        raise TurnNotRunning("no turn is running")
    return Response(status_code=204)


@router.get("/api/threads/{id:path}/events", operation_id="turns_list_events")
async def events(after: int = 0,
                 t: Mapping = Depends(owned_thread),
                 turn_events: TurnEventRepository = Depends(get_events)) -> JSONResponse:
    """The persisted history after a cursor. `after` is a cursor, not a page number."""
    rows = await turn_events.after(t["session_key"], after_id=after, limit=2000)
    return JSONResponse({"events": rows})


@router.get("/api/threads/{id:path}/stream", operation_id="turns_stream_events")
async def stream(after: int = 0,
                 t: Mapping = Depends(owned_thread),
                 bus: TurnEventBusPort = Depends(get_bus),
                 turn_events: TurnEventRepository = Depends(get_events)) -> StreamingResponse:
    """The same history, then the live tail. The frame format is frozen (`sse.py`)."""
    return StreamingResponse(event_stream(StreamDeps(bus=bus, turn_events=turn_events),
                                          t["session_key"], after),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.post("/api/asks/{qid}", operation_id="turns_answer_ask")
async def answer_ask(qid: str, body: AnswerIn,
                     user: CurrentUser = Depends(current_user),
                     broker: AskBrokerPort | None = Depends(get_broker)) -> Response:
    """Answer an `ask_user` question from the browser.

    The broker is what refuses a stranger's answer: `resolve` checks the user id
    against the one that opened the question, and a question someone else already
    answered is `410 gone` — never a hint that it existed.
    """
    if broker is None:
        raise ApplicationException(503, "unavailable", "questions are not wired")
    answer = str(body.answer)
    options = await broker.options_for(qid)
    if options is None:
        raise ApplicationException(410, "gone", "this question is no longer active")
    if answer not in options:
        raise ApplicationException(400, "bad_request", "answer is not one of the options")
    if not await broker.resolve(qid, str(options.index(answer)), user.user_id):
        raise ApplicationException(410, "gone", "this question is no longer active")
    return Response(status_code=204)

"""The thread wire shapes, and the two request bodies this router takes.

`to_wire` is frozen: the SPA reads these exact keys. `ThreadPatch` is the rename
body, `ThreadModelPut` the model body, and this file is the only place in the
module where Pydantic may appear.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from claude_hermes.core.model_catalog import Choice


class ThreadPatch(BaseModel):
    """`PATCH /api/threads/{id}` — `{"title": "…"}`.

    `Any` with a default, not `str`: the old handler read `str(body.get("title", ""))`,
    so a missing title, a `null` and a number all reached its own
    `400 bad_request` / "title is required" check (or were stringified) rather than
    being refused by a schema. A `str` field here would answer `400 bad_request`
    with pydantic's wording instead of the router's, and `extra="ignore"` keeps a
    client that sends more keys than we read working exactly as it did.
    """

    model_config = ConfigDict(extra="ignore")
    title: Any = ""


class ThreadModelPut(BaseModel):
    """`PUT /api/threads/{id}/model` — `{"model": str|null, "effort": str|null}`.

    `Any` with a `None` default for both, and `extra="ignore"`, for the reason
    `ThreadPatch` gives: the refusals belong to the router, which answers
    `400 bad_model` / `400 bad_effort` in the wording every channel shares
    (`core.model_catalog.validate`). A `str | None` field here would answer
    pydantic's `400 bad_request` for `{"model": 5}` instead, and the SPA branches
    on the code. `model: null` is the documented way to clear a lane's choice, so
    it is a value and not a missing field.
    """

    model_config = ConfigDict(extra="ignore")
    model: Any = None
    effort: Any = None


def to_wire(thread: dict, running: bool) -> dict:
    return {
        "id": thread["session_key"], "title": thread["title"], "channel": thread["channel"],
        "archived": thread["archived_at"] is not None, "running": running,
        "last_event_at": thread["last_event_at"], "created_at": thread["created_at"],
    }


def model_wire(model: str | None, effort: str | None, choice: Choice) -> dict:
    """The four model keys, wherever they appear.

    Two pairs, never one: `model`/`effort` is what this lane was EXPLICITLY given
    (both `null` when it has no choice of its own) and `effective_*` is what its
    next turn will actually run — a personal default, or the deploy's. Showing
    only the second would make "clear" look like it did nothing; showing only the
    first would leave the picker unable to say what a cleared lane runs.
    """
    return {"model": model, "effort": effort,
            "effective_model": choice.model, "effective_effort": choice.effort}


def detail_wire(thread: dict, running: bool, choice: Choice) -> dict:
    """`GET /api/threads/{id}`: the frozen thread shape plus the four model keys.

    Additive, and only on the DETAIL route: the list route stays as it was, both
    because the sidebar has no use for a per-row model and because resolving one
    would be a session read and an owner-default read per row.
    """
    return {**to_wire(thread, running),
            **model_wire(thread.get("model"), thread.get("effort"), choice)}

"""The wire shapes of a routine: the row the UI reads, and the two bodies it posts.

`to_wire` is frozen for Wave 2 — the SPA matches on it. The two request models are
the only Pydantic in this module, and they are deliberately lenient: every field is
`Any` with a default, because `SchedulerService` owns the validation and answers with
`ScheduleRejected` (400 `bad_request`) in wording the Routines page shows verbatim.
A Pydantic 422 in front of that would replace a message the tests pin.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from claude_hermes.modules.scheduler.domain.lane import delivery_lane


class RoutineCreate(BaseModel):
    """`POST /api/routines`. The handler still does `str(...)` on each field, so a
    number or a null reaches `create_or_raise` exactly as `body.get(k, "")` delivered
    it — and comes back as the tool's own refusal rather than a schema error."""

    model_config = ConfigDict(extra="ignore")

    name: Any = ""
    schedule: Any = ""
    prompt: Any = ""
    timeout_seconds: Any = None
    # The routine's own (model, effort). Absent or null means "inherit" —
    # `cron_model`, then `model` — and the router validates them against the
    # offered list before the service ever sees them.
    model: Any = None
    effort: Any = None


class RoutinePatch(BaseModel):
    """`PATCH /api/routines/{id}`. A *partial* edit: `SchedulerService.edit` branches
    on `"name" in body`, so the handler hands it `model_dump(exclude_unset=True)` and
    an absent key stays absent. Naming the four keys `edit` reads is what makes that
    faithful — `extra="ignore"` drops the rest, which `edit` ignored anyway."""

    model_config = ConfigDict(extra="ignore")

    name: Any = None
    schedule: Any = None
    prompt: Any = None
    timeout_seconds: Any = None
    # A patch that changes either half of the pair carries both (the router sends
    # them together): an effort belongs to a model, and a `null` model clears it.
    model: Any = None
    effort: Any = None


def to_wire(job: dict) -> dict:
    """The job row as the UI wants it: `schedule` is the human string the create
    form round-trips, `thread_id` the lane its reports land in.

    `kind` may be `"manual"` (an on-demand routine): `schedule` is then `"manual"`
    and `next_run_at` is null between wakes.

    `model`/`effort` are additive and always present, `null` for a routine that
    inherits the deploy pair. `.get` and not `[…]`: the two keys arrived with
    migration 0008, and a caller handing this a hand-built row (a fake service, an
    older snapshot) must still get a wire shape the SPA can read.
    """
    return {
        "id": job["id"], "name": job["name"], "kind": job["kind"], "value": job["value"],
        # `manual` stores an empty value; the form round-trips the word itself.
        "schedule": job["value"] if job["kind"] == "cron" else
                    f"every {job['value']}s" if job["kind"] == "interval" else
                    "manual" if job["kind"] == "manual" else job["value"],
        "prompt": job["prompt"], "state": job["state"], "next_run_at": job["next_run_at"],
        "last_run_at": job["last_run_at"], "last_status": job["last_status"],
        "timeout_seconds": job["timeout_seconds"], "thread_id": delivery_lane(job),
        "created_at": job["created_at"],
        "model": job.get("model"), "effort": job.get("effort"),
    }

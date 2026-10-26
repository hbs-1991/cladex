"""The scheduler's one use-case layer: create, edit, tick, deliver.

Both channels that can create a job — the `cron_manage` tool and the web Routines
router — go through `create_job` here, so the two can never validate a schedule, a
prompt or a timeout differently. `tick` is the at-most-once decision: advance the
clock before running, skip a fire that is older than its catch-up window.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import structlog

from claude_hermes.core.exceptions import ApplicationException, NotFound
from claude_hermes.core.model_catalog import lookup
from claude_hermes.core.session_keys import lane_owner
from claude_hermes.modules.scheduler.application.ports import STAY_IDLE, JobOutcome
from claude_hermes.modules.scheduler.domain.exceptions import ScheduleRejected
from claude_hermes.modules.scheduler.domain.lane import delivery_lane
from claude_hermes.modules.scheduler.domain.policy import job_timeout, scan_prompt
from claude_hermes.modules.scheduler.domain.schedule import (
    Schedule,
    fires_between,
    is_on_demand,
    next_fire,
    parse_schedule,
    period_seconds,
)

log = structlog.get_logger(__name__)

# A fire older than this is never worth running: nobody wants yesterday's brief at
# lunchtime. Per-schedule it is halved to the job's own period, so a 10-minute job
# does not deliver a stale run nine minutes late.
_MAX_CATCHUP = timedelta(hours=2)

NAME_MAX_CHARS = 80

# The calendar's widest window. Two months covers a month view with its leading
# and trailing weeks; anything wider is a request for a report, not a calendar, and
# the expansion below is linear in it.
CALENDAR_MAX_DAYS = 62

# A routine that fires this often or more is one `dense` marker on the calendar,
# never a dot per fire: "every 5 minutes" drawn as 288 chips a day is unreadable,
# and the Tasks form's own "every 1h" preset would still be 24 a day.
CALENDAR_DENSE_SECONDS = 3600


class SchedulerService:
    def __init__(self, jobs, *, executor=None, deliverer=None, topics=None,
                 events=None, threads=None, dispatcher=None,
                 deploy_owner: Callable[[], Awaitable[int]] | None = None):
        self._jobs = jobs
        self._executor = executor
        self._deliverer = deliverer
        self._topics = topics
        self._events = events
        self._threads = threads
        # No dispatcher means "run it here": `tick` awaits `execute_job` in its own
        # coroutine, which is exactly what the single-process ticker did before the
        # queue existed. The composition root passes a `QueueJobDispatcher` and the
        # queue decides whether that is still this process.
        self._dispatcher = dispatcher
        # Who a routine created with no `owner_id` belongs to (Wave 4b, D11): the
        # deploy owner, resolved at create time. `None` keeps the 4a behaviour —
        # the row is written NULL and every reader treats NULL as the owner's.
        self._deploy_owner = deploy_owner

    # --- late binding -----------------------------------------------------
    # Delivery needs the aiogram Bot, which is built long after the service is:
    # `build_app()` has to stay headless (no token, no network) for the tests that
    # assert its shape. Both stay None in a web-only or test deployment.
    def bind_delivery(self, *, deliverer=None, topics=None) -> None:
        if deliverer is not None:
            self._deliverer = deliverer
        if topics is not None:
            self._topics = topics

    @property
    def topics(self):
        return self._topics

    # --- reads ------------------------------------------------------------
    # `owner_id` is the ASKING user, and it is `None` only where nobody is asking
    # (the ticker, the executor, the delivery path). A request path that forgot to
    # pass it would list the deploy's routines to whoever logged in, which is why
    # the routers below name it on every single read.
    async def list_jobs(self, owner_id: int | None = None) -> list[dict]:
        return await self._jobs.list_jobs(owner_id)

    async def get(self, job_id: int, owner_id: int | None = None) -> dict | None:
        return await self._jobs.get(job_id, owner_id)

    async def require(self, job_id: int, owner_id: int | None = None) -> dict:
        """The routine, or `NotFound` — never "it exists but is not yours". A 403
        would tell a stranger that a routine with that id exists."""
        job = await self._jobs.get(job_id, owner_id)
        if job is None:
            raise NotFound("no such routine")
        return job

    async def by_thread(self, chat_id: int, thread_id: int) -> dict | None:
        return await self._jobs.by_thread(chat_id, thread_id)

    async def runs(self, job_id: int, limit: int = 30) -> list[dict]:
        return await self._jobs.runs(job_id, limit=limit)

    async def calendar(self, owner_id: int, start: datetime, end: datetime,
                       now: datetime | None = None) -> dict:
        """The asking user's routines on a timeline: future fires, dense markers and
        recorded runs in `[start, end)`.

        Fires are expanded from `max(start, now)` — a calendar never draws a fire
        that already happened as one still to come; what did happen is in `runs`.
        Only `active` routines fire, a `manual` one never does on its own, and one
        whose period is an hour or less is listed once in `dense` instead of being
        expanded. The content plan is NOT joined here: the SPA merges this with
        `GET /api/content` itself, so neither module learns the other exists.

        The refusal is `bad_range`, not `ScheduleRejected`'s frozen `bad_request`:
        the window is the SPA's own arithmetic, and the code tells it which bug it
        has.
        """
        now = now or datetime.now(timezone.utc)
        if end <= start or end - start > timedelta(days=CALENDAR_MAX_DAYS):
            raise ApplicationException(400, "bad_range", (
                f"The range must be positive and at most {CALENDAR_MAX_DAYS} days."))
        lower = max(start, now)
        fires: list[dict] = []
        dense: list[dict] = []
        for job in await self._jobs.list_jobs(owner_id):
            if job["state"] != "active" or not job["next_run_at"]:
                continue
            sched = Schedule(job["kind"], job["value"])
            period = period_seconds(sched)
            if period is not None and period <= CALENDAR_DENSE_SECONDS:
                dense.append({"routine_id": job["id"], "name": job["name"],
                              "every_seconds": int(period)})
                continue
            first = datetime.fromisoformat(job["next_run_at"])
            fires += [{"routine_id": job["id"], "name": job["name"],
                       "at": at.isoformat()}
                      for at in fires_between(sched, first, lower, end)]
        fires.sort(key=lambda f: f["at"])
        runs = await self._jobs.runs_between(
            owner_id, start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat())
        return {"fires": fires, "dense": dense, "runs": [
            {"routine_id": r["job_id"], "name": r["name"], "at": r["ts"],
             "status": r["status"]} for r in runs]}

    # --- writes -----------------------------------------------------------
    async def create_job(self, *, name: str, schedule: str, prompt: str,
                         deliver_chat_id: int, origin_session_key: str,
                         timeout_seconds=None, now=None,
                         owner_id: int | None = None,
                         model: str | None = None,
                         effort: str | None = None) -> tuple[int | None, str | None]:
        """The one create path for scheduled jobs, shared by the cron_manage tool and
        the web Routines router so the two can never validate differently. Returns
        `(job_id, None)` or `(None, reason)` with the reason already phrased for a user.

        `owner_id` (Wave 4a, D6) is whose routine this is: the turn's LANE owner
        from the tool, the request's user from the Routines router (owner-gated, so
        the two agree for the owner). It decides whose workspace each unattended run
        happens in and who can see the routine afterwards.

        A caller that passes no owner (a fork with no lane, a cron lane bound
        while nobody had claimed the deploy) gets the deploy owner (D11): a NULL
        row runs in the owner's tree anyway, but it is invisible to the owner's
        Routines page and its delivery lane belongs to nobody. On an unclaimed
        deploy `deploy_owner()` is 0 and the row stays NULL — there is nobody to
        give it to yet.

        `model`/`effort` are the routine's own pair, NULL meaning "inherit"
        (`cron_model`, then `model`) exactly as `timeout_seconds` does. They arrive
        already validated against the offered list — the Routines router owns that,
        because it is the only channel that sets them; `cron_manage`'s schema is
        prompt-cache surface and takes neither.
        """
        now = now or datetime.now(timezone.utc)
        if owner_id is None and self._deploy_owner is not None:
            owner_id = int(await self._deploy_owner() or 0) or None
        reason = scan_prompt(prompt)
        if reason:
            return None, f"Rejected: prompt looks unsafe ({reason})."
        try:
            sched = parse_schedule(schedule, now)
        except ValueError as e:
            return None, str(e)
        first = next_fire(sched, now)
        # An on-demand routine has no first fire BY DESIGN: it is stored active with
        # `next_run_at` NULL and waits for `run_now` or `wake_if_idle`. Only a
        # one-shot's None is a refusal.
        if first is None and not is_on_demand(sched):
            return None, "Rejected: one-shot time is in the past."
        try:
            timeout = job_timeout(timeout_seconds)
        except ValueError as e:
            return None, str(e)
        # Each job gets its own topic, so its reports never interrupt a
        # conversation. A failure to open one degrades to flat delivery.
        thread_id = await self._topics.create(deliver_chat_id, name) \
            if self._topics is not None else None
        jid = await self._jobs.create(
            name, sched.kind, sched.value, prompt, deliver_chat_id, origin_session_key,
            first.isoformat() if first else None,
            deliver_thread_id=thread_id, timeout_seconds=timeout,
            owner_id=owner_id, model=model, effort=effort,
        )
        if self._threads is not None:
            # The delivery lane gets its `threads` row now, not at the first run: the
            # Routines page opens on that lane as soon as the job exists, and every
            # thread route 404s on a lane with no row — a routine that fires next week
            # would show an unreadable page until then.
            #
            # The lane's owner is `lane_owner` with the ROUTINE's owner standing in
            # for the deploy owner (D3): a routine delivered to a private chat is
            # owned by that chat, and one delivered to a group would otherwise be
            # owned by nobody and vanish from its creator's sidebar.
            lane = delivery_lane(await self._jobs.get(jid))
            await self._threads.ensure(
                lane, "telegram",
                lane_owner(lane, "telegram", deliver_chat_id, owner_id or 0) or None,
                now.isoformat())
        return jid, None

    async def create_or_raise(self, *, name: str, schedule: str, prompt: str,
                              deliver_chat_id: int, origin_session_key: str,
                              timeout_seconds=None, now=None,
                              owner_id: int | None = None,
                              model: str | None = None,
                              effort: str | None = None) -> dict:
        """`create_job` for a caller that wants an exception and the created row —
        the HTTP channel. The refusal text is the tool's, word for word."""
        name = str(name).strip()[:NAME_MAX_CHARS]
        if not name:
            raise ScheduleRejected("name is required")
        jid, err = await self.create_job(
            name=name, schedule=schedule, prompt=prompt,
            deliver_chat_id=deliver_chat_id, origin_session_key=origin_session_key,
            timeout_seconds=timeout_seconds, now=now, owner_id=owner_id,
            model=model, effort=effort,
        )
        if err:
            raise ScheduleRejected(err)
        return await self._jobs.get(jid)

    async def edit(self, job_id: int, body: dict, now=None,
                   owner_id: int | None = None) -> dict:
        """Apply a partial edit. Same three validators as create, so a job cannot be
        patched into a state create would have refused.

        `owner_id` scopes the lookup, so patching somebody else's routine is a 404
        and not a silent rewrite — this is the one mutation whose target is not
        already resolved by a `require` in the router.
        """
        job = await self.require(job_id, owner_id)
        now = now or datetime.now(timezone.utc)
        fields: dict = {}
        if "name" in body:
            fields["name"] = str(body["name"]).strip()[:NAME_MAX_CHARS] or job["name"]
        if "prompt" in body:
            reason = scan_prompt(str(body["prompt"]))
            if reason:
                raise ScheduleRejected(f"Rejected: prompt looks unsafe ({reason}).")
            fields["prompt"] = str(body["prompt"])
        if "timeout_seconds" in body:
            try:
                fields["timeout_seconds"] = job_timeout(body["timeout_seconds"])
            except ValueError as e:
                raise ScheduleRejected(str(e)) from e
        # The pair the router validated, and it travels WHOLE: an effort belongs to a
        # model (the resolver drops one that has no model, and one the model does not
        # take), so the router sends both keys whenever either changes and `null`
        # means "back to the deploy pair".
        if "model" in body or "effort" in body:
            fields["model"] = body.get("model") or None
            fields["effort"] = (body.get("effort") or None) if fields["model"] else None
        if "schedule" in body:
            try:
                sched = parse_schedule(str(body["schedule"]), now)
            except ValueError as e:
                raise ScheduleRejected(str(e)) from e
            # To `manual` this clears `next_run_at` (the routine now waits for a
            # wake); from `manual` it puts the routine back on a clock.
            first = next_fire(sched, now)
            fields.update(kind=sched.kind, value=sched.value,
                          next_run_at=first.isoformat() if first else None)
        if fields:
            await self._jobs.update(job["id"], **fields)
        return await self._jobs.get(job["id"])

    async def set_state(self, job_id: int, state: str) -> None:
        await self._jobs.set_state(job_id, state)

    async def run_now(self, job_id: int, now=None) -> None:
        # The ticker fires anything whose next_run_at is in the past on its next tick.
        now = now or datetime.now(timezone.utc)
        await self._jobs.reschedule(job_id, now.isoformat())
        # …but only if nothing holds a lease on it. A job claimed a minute ago is
        # invisible to `claim_due` until that lease expires, and "run now" that
        # waits five minutes is not run now.
        await self._jobs.clear_lease(job_id)

    async def wake_if_idle(self, job_id: int, now: datetime | str) -> bool:
        """Wake an on-demand routine that is doing nothing, in ONE compare-and-set.

        The content publisher's only wake, and deliberately not `run_now`: that one
        clears the lease, so in redis mode — where `scheduler_tick` runs on several
        workers at once — a check-then-`run_now` would start a second publisher run
        next to a live one. Here the check IS the write: `True` only when this call
        set `next_run_at`, which the next tick then claims like any fire. A job that
        is paused, not `manual`, already has a pending wake, or holds a live lease
        answers `False` and is left exactly as it was.

        `now` may be a datetime or an ISO string (the publisher port speaks ISO);
        either way it is normalised to UTC, because `lease_until` is compared as
        text and only one spelling of an instant compares correctly.
        """
        if isinstance(now, str):
            now = datetime.fromisoformat(now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return await self._jobs.wake_if_idle(
            job_id, now.astimezone(timezone.utc).isoformat())

    async def delete(self, job_id: int) -> None:
        job = await self._jobs.get(job_id)
        if self._topics is not None and job and job["deliver_thread_id"]:
            try:
                await self._topics.close(job["deliver_chat_id"], job["deliver_thread_id"])
            except Exception:
                # A topic left open is cosmetic; a job that survives its own delete
                # keeps firing into a chat nobody asked.
                log.exception("closing the topic of job #%s failed", job_id)
        await self._jobs.delete(job_id)

    # --- the tick ---------------------------------------------------------
    def _catchup_window(self, sched: Schedule) -> timedelta:
        period = period_seconds(sched)
        if period is None:
            return _MAX_CATCHUP
        return min(timedelta(seconds=period / 2), _MAX_CATCHUP)

    @staticmethod
    def _next_of(now: datetime):
        """The clock arithmetic `claim_due` applies inside its own transaction.

        The repository must not import a schedule to know when a job fires next,
        and the reschedule must not happen in a second statement afterwards — that
        gap is precisely where two ticker processes would both see the same fire.
        """
        def next_run_at(job: dict) -> str | None:
            sched = Schedule(job["kind"], job["value"])
            if is_on_demand(sched):
                # Fired, not spent: back to waiting with `next_run_at` NULL.
                return STAY_IDLE
            nxt = next_fire(sched, now)
            return nxt.isoformat() if nxt else None
        return next_run_at

    async def tick(self, now: datetime) -> None:
        """Claim what is due, then hand each claimed fire on.

        The clock is advanced BEFORE anything runs — inside the claim, not after
        it. Any reordering here double-runs jobs.

        Nothing is awaited to completion here: a claimed fire goes to the
        dispatcher and the tick moves on. How many of them then run at once is
        the queue's decision — the number of worker containers in redis mode, and
        `InlineQueue`'s `{"run_job": 1}` limit inline, which is what keeps one
        process from forking a CLI per job due on the same minute.
        """
        for job in await self._jobs.claim_due(now.isoformat(), self._next_of(now)):
            sched = Schedule(job["kind"], job["value"])
            fire_at = datetime.fromisoformat(job["fire_at"])

            if now - fire_at > self._catchup_window(sched):
                await self._jobs.record_run(job["id"], now.isoformat(), "missed")
                # Nothing will run, so nothing will hand the lease back.
                await self._jobs.clear_lease(job["id"])
                log.info("cron #%s missed its window; skipped", job["id"])
                continue

            if self._dispatcher is not None:
                await self._dispatcher.dispatch(job["id"], job["fire_at"])
            else:
                await self.execute_job(job["id"], job["fire_at"], now)

    async def execute_job(self, job_id: int, fire_at: str, now=None) -> None:
        """Run one claimed fire: execute, record, emit, deliver, release.

        This is the body a worker process runs, which is why it takes primitives
        and re-reads the row: by the time the task is picked up, the claim has
        already moved `next_run_at`, and the row may even be gone. A job deleted
        between claim and run is logged and dropped — an enqueued task is not a
        reason to resurrect a routine the user cancelled.

        `fire_at` is the instant the claim consumed. The run is still recorded at
        `now` — that is what the history has always meant — but the two travel
        together so a late run is visible as a late run in the logs.
        """
        now = now or datetime.now(timezone.utc)
        job = await self._jobs.get(job_id)
        if job is None:
            log.info("cron #%s disappeared between claim and run; dropped",
                     job_id, fire_at=fire_at)
            return
        try:
            outcome = JobOutcome.of(await self._executor.run(job))
            await self._jobs.record_run(job["id"], now.isoformat(), outcome.status)
            text = outcome.text if outcome.status == "ok" else f"⚠️ {outcome.text}"
            await self._emit_delivery(job, outcome, text, now)
            if self._deliverer is not None:
                try:
                    await self._deliverer.deliver(job["deliver_chat_id"], text,
                                                  job["deliver_thread_id"])
                except Exception:
                    log.exception("cron delivery failed for #%s", job["id"])
        finally:
            # The lease guards the window between the claim and the run, and this
            # is the end of that window whatever happened inside it.
            await self._jobs.clear_lease(job_id)

    async def _emit_delivery(self, job: dict, outcome: JobOutcome, text: str,
                             now: datetime) -> None:
        """The report as a `cron` turn on the delivery lane, so the Routine page and
        any web reload show it. The cron:<id> SDK session stays isolated: these
        events describe the delivery, not the run.

        The model keys are the run's, not the delivery's: `turn.start` names the pair
        the executor RESOLVED and `turn.end` the model that actually answered, which
        is the same rule a foreground turn follows — the label a reader sees must be
        the model that wrote the text, never only the one that was asked for.
        """
        if self._events is None:
            return
        status = outcome.status
        lane = delivery_lane(job)
        turn_id = uuid.uuid4().hex
        try:
            # The delivery lane belongs to whoever created the routine (D6/D7):
            # `None` would leave a brand-new lane's thread row ownerless and
            # therefore invisible to the very person the report is for.
            owner = int(job.get("owner_id") or 0) or None
            if self._threads is not None:
                await self._threads.ensure(lane, "telegram", owner, now.isoformat(),
                                           talked=True)
            await self._events.emit(lane, turn_id, "turn.start", {
                "channel": "cron", "user_id": owner or 0,
                "job_id": job["id"], "run_id": turn_id,
                "model": outcome.model or None, "effort": outcome.effort,
            })
            await self._events.emit(lane, turn_id, "text.block", {"text": text})
            await self._events.emit(lane, turn_id, "turn.end", {
                "status": status if status in ("ok", "error") else "error",
                "cost_usd": None, "sdk_session_id": None,
                "error": None if status == "ok" else text,
                "usage": None,   # a delivery, not an API call: nothing sits in a window
                # the catalog id, like the turns path: a dated alias the API may
                # answer with one day must not reach the wire as a new model
                "model": lookup(outcome.model_that_ran).id if outcome.model_that_ran else None,
                "effort": outcome.effort,
                "fallback_from": outcome.fallback_from,
            })
        except Exception:
            log.exception("cron delivery events failed for #%s", job["id"])

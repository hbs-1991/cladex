"""Schedule parsing and the arithmetic of "when does this fire next".

Pure: a string and a clock in, a value object out. croniter is a calendar
library, not an I/O dependency, so it is allowed here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from croniter import croniter

_UNITS = {"m": 60, "h": 3600, "d": 86400}
_DURATION = re.compile(r"^(?:every\s+)?(\d+)\s*([mhd])$", re.IGNORECASE)


# An on-demand routine: never on a clock, fired only by `run_now` (the owner's
# "Run now") or by `wake_if_idle` (the content publisher's wake). Its value is the
# empty string — there is nothing to store.
MANUAL = "manual"

# However dense a schedule, the calendar never expands more fires than this for one
# routine: a `*/5` cron over two months is 17 000 dots nobody can read. The calendar
# lists anything hourly or denser once, as `dense`, so what it does expand is at most
# 62 days * 12 = 744 fires; the cap sits above that so a month view is never cut
# short, and only guards a caller that asks for a denser expansion.
MAX_FIRES_PER_ROUTINE = 1500


@dataclass(frozen=True)
class Schedule:
    kind: str   # "once" | "interval" | "cron" | "manual"
    value: str  # ISO ts | seconds | 5-field cron expr | ""


def parse_schedule(text: str, now: datetime) -> Schedule:
    text = text.strip()
    if text.lower() == MANUAL:
        return Schedule(MANUAL, "")
    m = _DURATION.match(text)
    if m:
        return Schedule("interval", str(int(m.group(1)) * _UNITS[m.group(2).lower()]))
    if len(text.split()) == 5 and croniter.is_valid(text):
        return Schedule("cron", text)
    try:
        ts = datetime.fromisoformat(text)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return Schedule("once", ts.astimezone(timezone.utc).isoformat())
    except ValueError:
        pass
    raise ValueError(
        "Unparseable schedule. Use '30m'/'every 2h', a 5-field cron "
        "expression like '0 8 * * *', or an ISO timestamp for one-shots."
    )


def next_fire(sched: Schedule, after: datetime) -> datetime | None:
    """The first fire strictly after `after`, or None when there is none on the
    clock: a one-shot already past, and a `manual` routine always. The two Nones
    mean different things to the claim (spent vs. waiting for its next wake), which
    is why the service asks `is_on_demand` rather than reading None alone."""
    if sched.kind == MANUAL:
        return None
    if sched.kind == "interval":
        return after + timedelta(seconds=int(sched.value))
    if sched.kind == "cron":
        return croniter(sched.value, after).get_next(datetime)
    ts = datetime.fromisoformat(sched.value).astimezone(timezone.utc)
    return ts if ts > after else None


def is_on_demand(sched: Schedule) -> bool:
    """A routine with no clock of its own: it stays `active` with `next_run_at`
    NULL between fires instead of going `done` like a spent one-shot."""
    return sched.kind == MANUAL


def period_seconds(sched: Schedule) -> float | None:
    """Seconds between two fires, or None for a schedule that has no period (a
    one-shot, an on-demand routine)."""
    if sched.kind == "interval":
        return float(sched.value)
    if sched.kind == "cron":
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        it = croniter(sched.value, base)
        first, second = it.get_next(datetime), it.get_next(datetime)
        return (second - first).total_seconds()
    return None


def fires_between(sched: Schedule, first: datetime, start: datetime, end: datetime,
                  *, limit: int = MAX_FIRES_PER_ROUTINE) -> list[datetime]:
    """The fires in `[start, end)`, walking forward from `first` — the routine's
    stored `next_run_at`.

    Anchored on `first` and not on `start` because that is how the claim moves an
    interval job: `next_run_at` is `claim time + period`, so `start + period` would
    draw every fire of a 6-hour job at the wrong hour. A cron fire is the same from
    either anchor; it jumps straight to `start` so a stale `first` costs one croniter
    step, not one per missed hour. An interval jumps arithmetically for the same
    reason. A one-shot is its `first` or nothing; `manual` is always nothing.
    """
    if sched.kind == MANUAL:
        return []
    if sched.kind == "once":
        return [first] if start <= first < end else []
    fire: datetime | None = first
    if first < start:
        if sched.kind == "interval":
            step = timedelta(seconds=int(sched.value))
            fire = first + step * -(-(start - first) // step)   # ceil division
        else:
            # Cron has minute granularity, so one second back is "at or after
            # `start`" for croniter's strictly-after `get_next`.
            fire = next_fire(sched, start - timedelta(seconds=1))
    out: list[datetime] = []
    while fire is not None and fire < end and len(out) < limit:
        out.append(fire)
        fire = next_fire(sched, fire)
    return out

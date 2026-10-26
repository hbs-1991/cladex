"""Times in and out of the content plan.

Stored: ISO-8601 UTC, one writer (`iso_utc`) — the tick compares the column as a
string, which is only sound while every row is spelled the same way.
Read: an ISO string WITH an offset is taken at its word; a naive one is a wall-clock
time in the deploy's `timezone` (the owner says "10:00" and means their 10:00).
Written back to a person or the agent: in that same zone, with its offset, so the
reader never has to guess which clock a time is on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone(name: str) -> ZoneInfo:
    """The IANA zone, or `ValueError` — config validates it at load, so a failure
    here is a programming error surfacing, not a user typo."""
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"unknown timezone {name!r}") from e


def iso_utc(moment: datetime) -> str:
    """The one spelling every `content_items` timestamp is written in."""
    return moment.astimezone(timezone.utc).isoformat()


def parse_instant(text: str, tz: str) -> datetime:
    """An aware UTC datetime from ISO text; a naive time is read in `tz`.

    `ValueError` with a sentence a person can act on — the router and the tool both
    answer it verbatim.
    """
    raw = str(text or "").strip()
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            f"Unreadable time {raw!r}: use ISO-8601 like 2026-10-01T10:00 "
            f"(read in {tz}) or with an offset, 2026-10-01T10:00+05:00.") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone(tz))
    return moment.astimezone(timezone.utc)


def local_iso(value: str | None, tz: str) -> str | None:
    """A stored UTC instant as the owner's wall clock, offset included, to the
    minute: `2026-10-01T10:00+05:00`. `None` stays `None` (a backlog item)."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(zone(tz)).isoformat(timespec="minutes")

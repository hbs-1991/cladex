"""The content plan's one use-case layer: the owner's routes, the agent's tool, the
drafter's and the publisher's actions, the Telegram card decisions and the tick —
over one set of validators.

Four actors reach an item, and the rules for each live in `domain/status.py`:

* the **owner** (web routes, and the buttons of a Telegram card) creates, edits,
  approves a theme, approves content, unapproves, retries, deletes;
* the **agent** (the `content_plan` tool, in chat or in a routine) edits the plan
  but never approves it;
* the **drafter** routine (`claim_draft`/`submit`/`draft_failed`, answered only
  from a run of the configured drafter) turns an approved theme into a post;
* the **publisher** routine (`due`/`claim`/`published`/`failed`, answered only
  from a run of the configured publisher) publishes an approved post.

The **tick** wakes both routines — through the scheduler's `wake_if_idle`, never
`run_now` — at most `len(WAKE_BACKOFF_MINUTES)` times per item, and sends the
approval cards (one per tick) through the `ApprovalCards` port. It issues no LLM
call: the routines' runs are the only ones.

Approval binds the owner's intent to the content — body, channel, format, time in
the row, and a sha256 per media file re-checked at `claim`. It is defence in depth,
not a boundary: the owner-lane agent has Bash and could run a publish script itself.
Theme approval binds nothing but a theme: it never schedules a post.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import structlog

from claude_hermes.core.exceptions import NotFound
from claude_hermes.modules.content.domain import cards as cd
from claude_hermes.modules.content.domain import status as st
from claude_hermes.modules.content.domain.exceptions import (
    ContentConflict,
    ContentRejected,
)
from claude_hermes.modules.content.domain.formats import approval_refusal, card_media_kind
from claude_hermes.modules.content.domain.media import MAX_MEDIA, split_paths, stored_media
from claude_hermes.modules.content.domain.time import iso_utc, parse_instant, zone

log = structlog.get_logger(__name__)

# UTC by another name: the constructor's `timezone` argument (the deploy zone) shadows
# `datetime.timezone` inside it.
_UTC = timezone.utc

# The `meta` keys the two routines' ids live under. By id, never by name: a
# routine renamed in the Tasks tab is still the publisher (or the drafter).
PUBLISHER_KEY = "content_publisher_job_id"
DRAFTER_KEY = "content_drafter_job_id"
# The "Контент" forum topic's thread id, written by compare-and-set.
TOPIC_KEY = "content_card_topic"
# A `TOPIC_KEY` value that says "a tick is creating the topic right now, since
# <iso>": the other ticks wait instead of creating a second topic.
TOPIC_RESERVED = "creating:"

# An approved item more than this late when the tick reaches it is `failed`, not
# queued: nobody wants a morning post at midnight. The scheduler's own
# `_MAX_CATCHUP` is the same two hours.
LATE_WINDOW = timedelta(hours=2)
# The idle-routine wait after each wake: woken when due, again after 5 minutes of
# an idle routine that did not claim the item, again after 15, and after a final
# 60 the item fails (publisher) or waits for the owner (drafter). Three wakes per
# item, at most — the same budget for both routines.
WAKE_BACKOFF_MINUTES = (5, 15, 60)
MAX_WAKES = len(WAKE_BACKOFF_MINUTES)
# A `publishing` item older than the publisher's timeout plus this is `stuck`; a
# `drafting` item older than the drafter's timeout plus this goes back to `planned`.
STUCK_GRACE = timedelta(minutes=10)
# Claimed drafter runs that may fail (or time out) before the item is `failed`.
MAX_DRAFT_ATTEMPTS = 3
# "Redo it" replies one item takes from Telegram; the fourth is refused.
MAX_REDRAFTS = 3
COMMENT_MAX_CHARS = 2000
# The widest calendar range one read may ask for: a month view with its spill-over
# weeks is ~6 weeks; two months is the most any view needs.
MAX_RANGE = timedelta(days=62)

DEFAULT_CHANNELS = ("linkedin", "telegram", "x", "threads", "instagram", "youtube")
TITLE_MAX_CHARS = 300
FORMAT_MAX_CHARS = 40
ERROR_MAX_CHARS = 2000

MISSED_WINDOW_ERROR = (
    "Missed its publish window: the time passed more than 2 hours before the "
    "publisher could take it. Reschedule it and approve it again, or retry.")
NEVER_PICKED_ERROR = (
    "The publisher routine was woken 3 times and never picked this item up. "
    "Check the routine's last run, then retry.")
STUCK_ERROR = (
    "Publishing never finished: the publisher claimed this item and reported "
    "nothing within its timeout. Check the channel — then retry with 're-post' "
    "or 'it was published'.")
NOTHING_TO_PUBLISH_ERROR = (
    "Nothing to publish: the post has no text. Write it, then approve it again.")
DRAFTER_IGNORED_ERROR = (
    "The drafter routine was woken 3 times and never claimed this item. Check the "
    "routine's last run, then retry drafting.")
DRAFT_TIMEOUT_ERROR = (
    "Drafting never finished: the drafter claimed this item and submitted nothing "
    "within its timeout, three times. Check the routine's runs, then set it back "
    "to an idea and approve the theme again.")

# How each routine is named when the tick explains why it cannot wake it.
_ROLES = {
    PUBLISHER_KEY: ("publisher", "Content publisher", "publish"),
    DRAFTER_KEY: ("drafter", "Content drafter", "draft"),
}
# `_card_topic`'s answer when another tick is creating the topic: try next tick.
_WAIT = object()


def _cleared(media: Iterable[dict]) -> list[dict]:
    """Media with the approval's hashes dropped."""
    return [{"path": m["path"], "sha256": None} for m in media]


def _unapproved(item_media: Any) -> dict[str, Any]:
    """The column values that withdraw an approval."""
    return {"approved_at": None, "approved_by": None,
            "media": _cleared(stored_media(item_media))}


def _theme_withdrawn() -> dict[str, Any]:
    """The column values of an item leaving `planned` for anywhere but `drafting`:
    the theme approval and the drafter's wake bookkeeping go with it."""
    return {"theme_approved_at": None, "draft_wakes": 0, "draft_next_wake_at": None,
            "error": None}


def _https_url(raw: Any) -> str:
    url = str(raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ContentRejected("bad_url", "A published item needs its post's URL "
                                         "(https://…).")
    return url


def _topic_id(raw: str | None) -> int | None:
    """A stored topic id, or None for an absent key or a reservation."""
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None



def _draft_retryable(item: dict) -> bool:
    """Planned with an error, or failed while drafting: a theme approved and the
    content never approved (a publish failure always has `approved_at`)."""
    if item["status"] == st.PLANNED:
        return item["error"] is not None
    return (item["status"] == st.FAILED and item.get("theme_approved_at") is not None
            and item.get("approved_at") is None)

class ContentService:
    """The content plan for every owner (v1: the deploy owner alone).

    `store` is `ContentStore`, `media` is `MediaFiles`, `publisher` is the scheduler
    seen through the `Publisher` port and `settings` is `core.meta.MetaStore`.
    `timezone` is the IANA zone naive times are read in and answers are written
    in; `channels` the accepted channel names; `publisher_timeout_seconds` a
    routine's run limit when it declares none (`cron_timeout_seconds`).

    `drafter` is the `Drafter` port (the publisher's adapter when not given — in
    `main.py` it is the same instance); `cards` is the `ApprovalCards` port or a
    late-bound holder of one (`.target`, None in a process with no Bot, where the
    card outbox simply waits); `draft_lead_hours` how long before `publish_at` an
    approved theme is drafted; `calendar_origin` the web app's origin for the
    card's "open in calendar" link (empty: no link); `clock` the "now" of every
    call that is not handed one (the card decisions never are).

    The card chat is the item owner's private chat with the bot: a user id IS the
    Telegram user id, and the plan is the deploy owner's alone.
    """

    def __init__(self, store, media, publisher, settings, *, timezone: str = "UTC",
                 channels: Iterable[str] = DEFAULT_CHANNELS,
                 publisher_timeout_seconds: float = 180.0, drafter: Any = None,
                 cards: Any = None, draft_lead_hours: float = 24.0,
                 calendar_origin: str = "",
                 clock: Callable[[], datetime] | None = None):
        zone(timezone)  # fail at construction, not at the first naive time
        self._clock = clock or (lambda: datetime.now(_UTC))
        self._store = store
        self._media = media
        self._publisher = publisher
        self._drafter = drafter if drafter is not None else publisher
        self._cards = cards
        self._settings = settings
        self._tz = timezone
        self._channels = tuple(str(c).strip().lower() for c in channels if str(c).strip())
        self._fallback_timeout = float(publisher_timeout_seconds)
        self._lead = timedelta(hours=float(draft_lead_hours))
        self._calendar_origin = str(calendar_origin or "").strip().rstrip("/")

    @property
    def timezone(self) -> str:
        return self._tz

    @property
    def channels(self) -> tuple[str, ...]:
        return self._channels

    # --- reads --------------------------------------------------------------------
    async def require(self, item_id: int, owner_id: int) -> dict:
        """The item, or `NotFound` — never "it exists but is not yours"."""
        item = await self._store.get(int(item_id), owner_id)
        if item is None:
            raise NotFound("no such content item")
        return item

    def parse_range(self, start: Any, end: Any) -> tuple[datetime, datetime]:
        """`from`/`to` as the calendar sends them, checked: both present, readable,
        in order, and at most `MAX_RANGE` apart (400 `bad_range`)."""
        if not start or not end:
            raise ContentRejected("bad_range", "Both 'from' and 'to' are required.")
        try:
            lo, hi = parse_instant(start, self._tz), parse_instant(end, self._tz)
        except ValueError as e:
            raise ContentRejected("bad_range", str(e)) from e
        if hi <= lo:
            raise ContentRejected("bad_range", "'to' must be after 'from'.")
        if hi - lo > MAX_RANGE:
            raise ContentRejected("bad_range", "A range may span at most 62 days.")
        return lo, hi

    async def calendar(self, owner_id: int, start: datetime, end: datetime) -> dict:
        """`{"items": dated items with start <= publish_at < end, "backlog": [...]}`."""
        items = await self._store.list_items(owner_id, start_iso=iso_utc(start),
                                             end_iso=iso_utc(end))
        return {"items": items, "backlog": await self._store.backlog(owner_id)}

    async def list_items(self, owner_id: int, *, start: datetime | None = None,
                         end: datetime | None = None, status: str | None = None,
                         channel: str | None = None, limit: int = 100) -> list[dict]:
        return await self._store.list_items(
            owner_id, start_iso=iso_utc(start) if start else None,
            end_iso=iso_utc(end) if end else None, status=status or None,
            channel=channel or None, limit=limit)

    async def media_file(self, owner_id: int, item_id: int, n: int) -> Path:
        """The n-th media file of an item, resolved inside the owner's `agent_home`
        at request time. Missing anything — the item, the index, the file — is 404."""
        item = await self.require(item_id, owner_id)
        media = stored_media(item["media"])
        if not 0 <= n < len(media):
            raise NotFound("no such media file")
        found = await self._media.locate(owner_id, media[n]["path"])
        if found is None:
            raise NotFound("no such media file")
        return found

    def local_time(self, value: str | None) -> str:
        """A stored instant as the owner reads it on a card: `01.10 10:00`."""
        if not value:
            return ""
        return datetime.fromisoformat(value).astimezone(zone(self._tz)).strftime(
            "%d.%m %H:%M")

    def calendar_url(self, item_id: int) -> str:
        """The web calendar with this item's dialog open, or "" with no origin."""
        if not self._calendar_origin:
            return ""
        return f"{self._calendar_origin}/calendar?item={int(item_id)}"

    # --- validation ---------------------------------------------------------------
    async def _fields(self, owner_id: int, body: dict, *,
                      statuses: frozenset[str]) -> dict[str, Any]:
        """The column values for the keys PRESENT in `body`, validated.

        One validator for the routes and the tool, each refusal with its field's
        own code. Media come back unhashed: whether a hash is taken is the caller's
        decision (only an approval, an edit of approved content, or the drafter's
        submit takes one).
        """
        out: dict[str, Any] = {}
        if "channel" in body:
            channel = str(body["channel"] or "").strip().lower()
            if channel not in self._channels:
                raise ContentRejected("bad_channel", (
                    f"Unknown channel '{channel}': use one of "
                    f"{', '.join(self._channels)}."))
            out["channel"] = channel
        if "format" in body:
            out["format"] = str(body["format"] or "").strip().lower()[:FORMAT_MAX_CHARS]
        if "title" in body:
            out["title"] = str(body["title"] or "").strip()[:TITLE_MAX_CHARS]
        if "body" in body:
            out["body"] = str(body["body"] or "")
        if "media" in body:
            raw = body["media"]
            paths = split_paths(raw) if isinstance(raw, str) else [
                str(p) for p in (raw or [])]
            if len(paths) > MAX_MEDIA:
                raise ContentRejected("bad_media",
                                      f"At most {MAX_MEDIA} media files per item.")
            normalized: list[str] = []
            for path in paths:
                try:
                    rel = await self._media.normalize(owner_id, path)
                except ValueError as e:
                    raise ContentRejected("bad_media", f"Refused media: {e}.") from e
                if rel not in normalized:
                    normalized.append(rel)
            out["media"] = [{"path": p, "sha256": None} for p in normalized]
        if "publish_at" in body:
            raw = body["publish_at"]
            if raw is None or str(raw).strip() == "":
                out["publish_at"] = None
            else:
                try:
                    out["publish_at"] = iso_utc(parse_instant(str(raw), self._tz))
                except ValueError as e:
                    raise ContentRejected("bad_time", str(e)) from e
        for key in ("group_key", "source_path"):
            if key in body:
                out[key] = str(body[key] or "").strip() or None
        if "status" in body:
            status = str(body["status"] or "").strip().lower()
            if status not in statuses:
                raise ContentRejected("bad_status", (
                    f"Status '{status}' is not allowed here: use one of "
                    f"{', '.join(s for s in st.STATUSES if s in statuses)}."))
            out["status"] = status
        return out

    async def _hashed(self, owner_id: int, media: Iterable[dict]) -> list[dict]:
        """Each media entry with its current sha256, or 400 `media_missing` naming
        the first file that is not there."""
        out = []
        for entry in media:
            digest = await self._media.sha256(owner_id, entry["path"])
            if digest is None:
                raise ContentRejected("media_missing", (
                    f"{entry['path']}: no such file in the agent's home."))
            out.append({"path": entry["path"], "sha256": digest})
        return out

    @staticmethod
    def _gate(body: Any, fmt: Any, media: Any) -> None:
        """The approval gate (`domain/formats.py`): 400 `empty_body` or
        `media_required`, shared by every approve and the drafter's submit."""
        refusal = approval_refusal(body, fmt, stored_media(media))
        if refusal is not None:
            raise ContentRejected(*refusal)

    # --- the approval card seen from the owner's side ---------------------------
    def _cards_port(self) -> Any:
        """The bound `ApprovalCards`, or None in a process with no Bot."""
        if self._cards is None:
            return None
        return getattr(self._cards, "target", self._cards)

    async def _tell_card(self, item: dict, text: str) -> None:
        """Best-effort: replace a card's text (and drop its buttons). A lost edit is
        cosmetic — the row, not the message, decides what a button can do."""
        cards = self._cards_port()
        chat_id, message_id = item.get("card_chat_id"), item.get("card_message_id")
        if cards is None or not chat_id or not message_id:
            return
        try:
            await cards.edit(int(chat_id), int(message_id), text)
        except Exception:                            # noqa: BLE001 - cosmetic
            log.warning("content card edit failed", item=item.get("id"), exc_info=True)

    async def _after_content_write(self, updated: dict,
                                   text: str = cd.CHANGED_TEXT) -> dict:
        """A content write superseded a card that is in Telegram: say so on it."""
        if updated.get(cd.PREVIOUS_CARD) == cd.CARD_SENT:
            await self._tell_card(updated, text)
        return updated

    # --- the owner: web routes -------------------------------------------------------
    async def create(self, owner_id: int, body: dict, *, via: str = "web",
                     thread_id: str | None = None) -> dict:
        """A new item from the web or the tool. `status` may only be idea, draft or
        review (default draft): approval is an act, never a field."""
        values = await self._fields(owner_id, body, statuses=st.CREATE_STATUSES)
        if "channel" not in values:
            raise ContentRejected("bad_channel", "A channel is required.")
        values.setdefault("status", st.DRAFT)
        values["created_via"] = via
        values["thread_id"] = thread_id
        return await self._store.create(owner_id, values)

    async def edit(self, owner_id: int, item_id: int, body: dict,
                   now: datetime | None = None) -> dict:
        """The owner's partial edit.

        An item the publisher or the drafter holds (or that is published) is 409
        `locked`. Editing a `scheduled` item keeps it scheduled — the owner is the
        approver, so the edit is re-approved in the same act — and re-hashes its
        media when they changed; a `planned` item stays planned the same way.
        Giving either a status leaves it and withdraws the approval. Any edit makes
        a live approval card stale.
        """
        now = now or self._clock()
        item = await self.require(item_id, owner_id)
        if item["status"] in st.WEB_LOCKED:
            raise ContentConflict("locked", (
                f"The item is {item['status']}: a routine holds it, so it cannot be "
                "edited now."))
        values = await self._fields(owner_id, body, statuses=st.WEB_SET_STATUSES)
        approved = item["approved_at"] is not None
        if "status" in values:
            if approved:
                values.update(_unapproved(values.get("media", item["media"])))
            if item["status"] == st.PLANNED:
                values.update(_theme_withdrawn())
        elif item["status"] == st.SCHEDULED:
            publish_at = values.get("publish_at", item["publish_at"])
            if publish_at is None:
                raise ContentRejected("no_publish_at", (
                    "A scheduled item needs a publish time; unapprove it first to "
                    "move it to the backlog."))
            if "media" in values:
                values["media"] = await self._hashed(owner_id, values["media"])
            values.update(approved_at=iso_utc(now), approved_by=owner_id)
        elif item["status"] == st.PLANNED:
            if values.get("publish_at", item["publish_at"]) is None:
                raise ContentRejected("no_publish_at", (
                    "A planned item needs a publish time; unapprove the theme first "
                    "to move it to the backlog."))
        elif approved and "media" in values:
            # A failed or stuck item keeps its approval for a retry; its hashes have
            # to describe the files it would now publish.
            values["media"] = await self._hashed(owner_id, values["media"])
        if not values:
            return item
        return await self._after_content_write(
            await self._write(item, owner_id, values, supersede=True))

    async def _write(self, item: dict, owner_id: int, values: dict, *,
                     supersede: bool = False) -> dict:
        """Apply `values` only if the item is still in the status it was read in —
        the tick may have promoted it since, and an edit must not land on a row a
        routine now holds."""
        updated = await self._store.update(item["id"], owner_id, values,
                                           expect=(item["status"],), supersede=supersede)
        if updated is None:
            current = await self.require(item["id"], owner_id)
            raise ContentConflict("locked", (
                f"The item moved to {current['status']} meanwhile; reload it."))
        return updated

    async def delete(self, owner_id: int, item_id: int) -> None:
        item = await self.require(item_id, owner_id)
        if item["status"] == st.PUBLISHED:
            raise ContentConflict("published_item", (
                "A published item is the record of a post and cannot be deleted."))
        if not await self._store.delete(item["id"], owner_id, forbid=(st.PUBLISHED,)):
            await self.require(item_id, owner_id)
            raise ContentConflict("published_item", (
                "The item was published meanwhile and cannot be deleted."))

    async def approve(self, owner_id: int, item_id: int,
                      now: datetime | None = None) -> dict:
        """The owner's approval: `scheduled`, with every media file hashed.

        Refused for a blank post or media the format cannot publish (`empty_body`,
        `media_required`). The hashes are what `claim` re-checks: a file that
        changes after this moment sends the item back to review instead of being
        published. A card still in Telegram is told it was approved here.
        """
        now = now or self._clock()
        item = await self.require(item_id, owner_id)
        if item["status"] not in st.APPROVABLE:
            raise ContentRejected("not_approvable", (
                f"An item in {item['status']} cannot be approved."))
        if not item["publish_at"]:
            raise ContentRejected("no_publish_at", "Set a publish time before approving.")
        self._gate(item["body"], item["format"], item["media"])
        media = await self._hashed(owner_id, stored_media(item["media"]))
        updated = await self._store.update(item["id"], owner_id, {
            "status": st.SCHEDULED, "media": media, "approved_at": iso_utc(now),
            "approved_by": owner_id, "error": None, "attempts": 0,
            "next_wake_at": None, "claimed_at": None,
        }, expect=st.APPROVABLE, supersede=True)
        if updated is None:
            raise ContentRejected("not_approvable", "The item changed meanwhile; reload it.")
        return await self._after_content_write(
            updated, cd.approved_text(self.local_time(updated["publish_at"])))

    async def unapprove(self, owner_id: int, item_id: int) -> dict:
        item = await self.require(item_id, owner_id)
        if item["status"] != st.SCHEDULED:
            raise ContentRejected("not_approved", (
                f"Only a scheduled item can be unapproved; this one is {item['status']}."))
        updated = await self._store.update(
            item["id"], owner_id, {"status": st.REVIEW, **_unapproved(item["media"])},
            expect=(st.SCHEDULED,), supersede=True)
        if updated is None:
            raise ContentRejected("not_approved", "The item moved on meanwhile; reload it.")
        return await self._after_content_write(updated)

    async def retry(self, owner_id: int, item_id: int, mode: Any, url: Any = None,
                    now: datetime | None = None) -> dict:
        """The owner settles a failed, stuck or publishing item — the one decision
        the system never takes itself, because only a person can know whether the
        post went out: `repost` queues it again (wakes counted from zero),
        `published` records the URL it went out under. A post nobody approved (a
        draft that failed) cannot be re-posted: it was never posted."""
        now = now or self._clock()
        item = await self.require(item_id, owner_id)
        if item["status"] not in st.RETRYABLE:
            raise ContentRejected("not_retryable", (
                f"Only a failed, stuck or publishing item can be retried; this one is "
                f"{item['status']}."))
        if mode == "repost":
            if item["approved_at"] is None:
                raise ContentRejected("not_retryable", (
                    "This post was never approved, so there is nothing to re-post: "
                    "approve it instead."))
            values = {"status": st.QUEUED, "attempts": 0, "error": None,
                      "next_wake_at": iso_utc(now), "claimed_at": None}
        elif mode == "published":
            values = {"status": st.PUBLISHED, "published_url": _https_url(url),
                      "error": None, "next_wake_at": None}
        else:
            raise ContentRejected("bad_mode", (
                "Choose 'repost' (queue it again) or 'published' (with its URL)."))
        updated = await self._store.update(item["id"], owner_id, values,
                                           expect=st.RETRYABLE)
        if updated is None:
            raise ContentRejected("not_retryable", "The item moved on meanwhile; reload it.")
        return updated

    # --- the owner: themes ----------------------------------------------------------
    async def approve_theme(self, owner_id: int, item_id: int,
                            now: datetime | None = None) -> dict:
        """`idea -> planned`: the owner wants this post, on this channel, at this
        time, and the drafter may write it `draft_lead_hours` before. Nothing is
        scheduled: the drafted post waits in `review` for the content approval."""
        now = now or self._clock()
        item = await self.require(item_id, owner_id)
        if item["status"] != st.IDEA:
            raise ContentRejected("not_an_idea", (
                f"Only an idea's theme can be approved; this item is {item['status']}."))
        if not item["publish_at"]:
            raise ContentRejected("no_publish_at", (
                "Set a publish time before approving the theme."))
        if item["publish_at"] <= iso_utc(now):
            raise ContentRejected("no_publish_at", (
                "The publish time has already passed: move it to the future before "
                "approving the theme."))
        updated = await self._store.update(item["id"], owner_id, {
            "status": st.PLANNED, "theme_approved_at": iso_utc(now),
            "draft_wakes": 0, "draft_attempts": 0, "draft_next_wake_at": None,
            "error": None,
        }, expect=(st.IDEA,))
        if updated is None:
            raise ContentRejected("not_an_idea", "The item changed meanwhile; reload it.")
        return updated

    async def unapprove_theme(self, owner_id: int, item_id: int) -> dict:
        """`planned -> idea`, the theme approval withdrawn."""
        item = await self.require(item_id, owner_id)
        if item["status"] != st.PLANNED:
            raise ContentRejected("not_planned", (
                f"Only a planned item's theme can be unapproved; this one is "
                f"{item['status']}."))
        updated = await self._store.update(
            item["id"], owner_id, {"status": st.IDEA, **_theme_withdrawn()},
            expect=(st.PLANNED,))
        if updated is None:
            raise ContentRejected("not_planned", "The item moved on meanwhile; reload it.")
        return updated

    async def approve_themes(self, owner_id: int, start: Any, end: Any,
                             now: datetime | None = None) -> list[dict]:
        """Every future `idea` with `from <= publish_at < to` -> `planned`, in one
        statement ("approve this week's themes"). `bad_range` as the calendar read."""
        now = now or self._clock()
        lo, hi = self.parse_range(start, end)
        return await self._store.approve_themes(owner_id, iso_utc(lo), iso_utc(hi),
                                                iso_utc(now))

    async def retry_drafting(self, owner_id: int, item_id: int) -> dict:
        """A `planned` item the drafter could not take (`error` set), or one that
        `failed` its drafting (theme approved, content never approved): its wakes
        and attempts start over, it is `planned` again, its theme approval stays."""
        item = await self.require(item_id, owner_id)
        if not _draft_retryable(item):
            raise ContentRejected("not_retryable", (
                "Only a planned item that needs attention, or one whose drafting "
                "failed, can be retried."))
        updated = await self._store.update(item["id"], owner_id, {
            "status": st.PLANNED, "draft_wakes": 0, "draft_attempts": 0,
            "draft_next_wake_at": None, "error": None,
        }, expect=(item["status"],))
        if updated is None:
            raise ContentRejected("not_retryable", "The item moved on meanwhile; reload it.")
        return updated

    # --- the two routines ------------------------------------------------------------
    async def _routine_job_id(self, key: str) -> int | None:
        raw = await self._settings.get(key)
        try:
            return int(raw) if raw else None
        except (TypeError, ValueError):
            return None

    async def _publisher_job_id(self) -> int | None:
        return await self._routine_job_id(PUBLISHER_KEY)

    def _port(self, key: str) -> Any:
        return self._drafter if key == DRAFTER_KEY else self._publisher

    async def _describe(self, job_id: int, owner_id: int, key: str = PUBLISHER_KEY
                        ) -> dict | None:
        result = self._port(key).describe(job_id, owner_id)
        return await result if inspect.isawaitable(result) else result

    async def _routine(self, owner_id: int, key: str) -> dict:
        job_id = await self._routine_job_id(key)
        desc = await self._describe(job_id, owner_id, key) if job_id is not None else None
        return {"job_id": job_id,
                "name": desc.get("name") if desc else None,
                "state": desc.get("state") if desc else None}

    async def _set_routine(self, owner_id: int, key: str, job_id: Any) -> dict:
        """Point the plan at one of the owner's on-demand (`manual`) routines. Any
        other kind would fire on its own clock, and the tick's wakes would be
        either lost or doubled. The publisher and the drafter are two routines:
        one run cannot both write a post and be the one that publishes it."""
        role, template, _verb = _ROLES[key]
        code = f"bad_{role}"
        other = DRAFTER_KEY if key == PUBLISHER_KEY else PUBLISHER_KEY
        try:
            jid = int(job_id)
        except (TypeError, ValueError):
            jid = 0
        desc = await self._describe(jid, owner_id, key) if jid > 0 else None
        if desc is None or desc.get("kind") != "manual":
            raise ContentRejected(code, (
                f"The {role} must be one of your on-demand routines "
                f"(schedule 'On demand', like the '{template}' template)."))
        if jid == await self._routine_job_id(other):
            raise ContentRejected(code, (
                f"That routine is already the {_ROLES[other][0]}: the {role} must be "
                "another routine."))
        await self._settings.set(key, str(jid))
        return await self._routine(owner_id, key)

    async def publisher(self, owner_id: int) -> dict:
        """`{"job_id", "name", "state"}` — the id alone (name and state null) when
        the routine it names is gone, so the calendar can say so."""
        return await self._routine(owner_id, PUBLISHER_KEY)

    async def set_publisher(self, owner_id: int, job_id: Any) -> dict:
        return await self._set_routine(owner_id, PUBLISHER_KEY, job_id)

    async def drafter(self, owner_id: int) -> dict:
        """The drafter routine, in `publisher`'s shape."""
        return await self._routine(owner_id, DRAFTER_KEY)

    async def set_drafter(self, owner_id: int, job_id: Any) -> dict:
        return await self._set_routine(owner_id, DRAFTER_KEY, job_id)

    async def is_publisher(self, origin: str) -> bool:
        """Whether a tool call comes from a run of the configured publisher routine.
        The one gate on `due`/`claim`/`published`/`failed`: from chat, or from any
        other routine, a prompt-injected turn could otherwise take an item."""
        job_id = await self._publisher_job_id()
        return job_id is not None and origin == f"cron:{job_id}"

    async def is_drafter(self, origin: str) -> bool:
        """The same gate for `claim_draft`/`submit`/`draft_failed`."""
        job_id = await self._routine_job_id(DRAFTER_KEY)
        return job_id is not None and origin == f"cron:{job_id}"

    # --- the agent: the content_plan tool -------------------------------------------
    async def agent_create(self, owner_id: int, body: dict, *, origin: str,
                           thread_id: str | None) -> dict:
        return await self.create(owner_id, body, via=origin, thread_id=thread_id)

    async def agent_update(self, owner_id: int, item_id: int, body: dict) -> dict:
        """The agent's edit. An item a routine holds, or that is history, is
        refused outright. A content edit of APPROVED content (a `scheduled` item, or
        a failed one awaiting the owner's retry) lands in `review` with the approval
        cleared: what the owner approved is no longer what would be published. An
        edit of a `planned` item's theme (title, channel, format, time) lands in
        `idea` with the theme approval cleared, for the same reason."""
        item = await self.require(item_id, owner_id)
        if item["status"] in st.AGENT_LOCKED:
            holder = "drafter routine" if item["status"] == st.DRAFTING \
                else "publisher routine and the owner"
            raise ContentRejected("locked", (
                f"Refused: the item is {item['status']}; only the {holder} move it "
                "from here."))
        values = await self._fields(owner_id, body,
                                    statuses=frozenset(st.STATUSES))
        if "status" in values:
            refusal = st.agent_refusal(item["status"], values["status"])
            if refusal:
                raise ContentRejected("bad_status", refusal)
        content_edit = any(k in values for k in st.APPROVED_FIELDS)
        if item["approved_at"] is not None and (content_edit or "status" in values):
            values.setdefault("status", st.REVIEW)
            values.update(_unapproved(values.get("media", item["media"])))
        if item["status"] == st.PLANNED and (
                "status" in values or any(k in values for k in st.THEME_FIELDS)):
            values.setdefault("status", st.IDEA)
            values.update(_theme_withdrawn())
        return await self._after_content_write(
            await self._write(item, owner_id, values, supersede=True))

    async def agent_set_status(self, owner_id: int, item_id: int, status: Any) -> dict:
        item = await self.require(item_id, owner_id)
        target = str(status or "").strip().lower()
        refusal = st.agent_refusal(item["status"], target)
        if refusal:
            raise ContentRejected("bad_status", refusal)
        # Every agent target is before `scheduled` (and is not `planned`), so
        # neither approval survives.
        values = {"status": target, **_unapproved(item["media"])}
        if item["status"] == st.PLANNED:
            values.update(_theme_withdrawn())
        return await self._after_content_write(
            await self._write(item, owner_id, values, supersede=True))

    # --- the drafter routine ---------------------------------------------------------
    @staticmethod
    def draft_folder(item: dict) -> str:
        """Where this attempt writes its media, relative to `agent_home`. Numbered
        per attempt, so a render orphaned by a timeout can never overwrite the
        files of a newer attempt."""
        n = int(item.get("draft_attempts") or 0) + int(item.get("redrafts") or 0) + 1
        return f"content/drafts/{item['id']}/{n}/"

    async def claim_draft(self, owner_id: int, now: datetime | None = None
                          ) -> dict | None:
        """`planned -> drafting` for the ONE earliest item inside its drafting
        window, or None when nothing is due. Refused while another item of this
        owner is drafting: one item per drafter run."""
        now = now or self._clock()
        claimed, busy = await self._store.claim_draft(owner_id, iso_utc(now),
                                                      iso_utc(now + self._lead))
        if busy is not None:
            raise ContentRejected("drafting_busy", (
                f"Refused: #{busy['id']} is still drafting — one item per run. Submit "
                "it or report draft_failed first."))
        return claimed

    async def group_siblings(self, owner_id: int, item: dict) -> list[dict]:
        """The other items of the same idea (`group_key`): one video cut for an IG
        reel and a YT short is rendered once, and the drafter of the second one
        reuses what the first one produced."""
        key = item.get("group_key")
        if not key:
            return []
        rows = await self._store.list_items(owner_id, group_key=key, limit=20)
        return [r for r in rows if r["id"] != item["id"]]

    async def submit(self, owner_id: int, item_id: int, body: dict,
                     now: datetime | None = None) -> dict:
        """The drafter's result: `drafting -> review`, media hashed, and — unless
        the publish time has passed — an approval card queued (`pending`) for the
        tick to send. `body` may carry `body`, `media` and `title`; what it leaves
        out stays. The approval gate applies here too: a post the owner could not
        approve is refused, so the drafter can fix it or report `draft_failed`."""
        now = now or self._clock()
        item = await self.require(item_id, owner_id)
        if item["status"] != st.DRAFTING:
            raise ContentRejected("not_drafting", (
                f"#{item['id']} is {item['status']}, not drafting: only the item you "
                "claimed can be submitted."))
        values = await self._fields(
            owner_id, {k: body[k] for k in ("body", "media", "title") if k in body},
            statuses=frozenset())
        text = values.get("body", item["body"])
        media = values.get("media", _cleared(stored_media(item["media"])))
        self._gate(text, item["format"], media)
        hashed = await self._hashed(owner_id, media)
        future = bool(item["publish_at"]) and item["publish_at"] > iso_utc(now)
        values.update(
            status=st.REVIEW, body=text, media=hashed,
            card_state=cd.CARD_PENDING if future else cd.CARD_NONE,
            card_revision=cd.card_revision(text, hashed), card_attempts=0,
            card_chat_id=None, card_message_id=None, card_claimed_at=None,
            draft_comment=None, drafting_since=None, error=None, approved_at=None,
            approved_by=None)
        updated = await self._store.update(item["id"], owner_id, values,
                                           expect=(st.DRAFTING,))
        if updated is None:
            raise ContentRejected("not_drafting", (
                f"#{item['id']} stopped drafting meanwhile (timed out?): do not "
                "submit it again."))
        return updated

    async def draft_failed(self, owner_id: int, item_id: int, error: Any,
                           now: datetime | None = None) -> dict:
        """`drafting -> planned` with one attempt counted; the third is `failed`
        with the drafter's reason."""
        now = now or self._clock()
        text = str(error or "").strip()
        if not text:
            raise ContentRejected("no_error", "Say what went wrong in 'error'.")
        updated = await self._store.return_draft(int(item_id), owner_id, iso_utc(now),
                                                 MAX_DRAFT_ATTEMPTS,
                                                 text[:ERROR_MAX_CHARS])
        if updated is None:
            item = await self.require(item_id, owner_id)
            raise ContentRejected("not_drafting", (
                f"#{item['id']} is {item['status']}, not drafting: only the item you "
                "claimed can be reported."))
        return updated

    # --- the owner in Telegram: the card's decisions ---------------------------------
    async def approve_card(self, owner_id: int, item_id: int, revision: str,
                           now: datetime | None = None) -> dict:
        """The card's approve button, owner-only (another user's id finds no item).

        The files are hashed FRESH and the revision recomputed: the button approves
        exactly the content it was sent with, or nothing (`stale_card`). The write
        is one statement that re-checks status, card state and revision.
        """
        now = now or self._clock()
        stale = ContentRejected("stale_card", "This card is out of date.")
        item = await self._store.get(int(item_id), owner_id)
        if (item is None or item["status"] != st.REVIEW
                or item["card_state"] != cd.CARD_SENT
                or item["card_revision"] != revision):
            raise stale
        if not item["publish_at"] or item["publish_at"] <= iso_utc(now):
            raise ContentRejected("too_late", "The publish time has passed.")
        self._gate(item["body"], item["format"], item["media"])
        fresh = []
        for entry in stored_media(item["media"]):
            digest = await self._media.sha256(owner_id, entry["path"])
            if digest is None:
                raise stale
            fresh.append({"path": entry["path"], "sha256": digest})
        if cd.card_revision(item["body"], fresh) != revision:
            raise stale
        approved = await self._store.approve_card(item["id"], owner_id, revision, fresh,
                                                  iso_utc(now))
        if approved is None:
            raise stale
        await self._tell_card(approved,
                              cd.approved_text(self.local_time(approved["publish_at"])))
        return approved

    async def live_card(self, owner_id: int, chat_id: int,
                        message_id: int) -> dict | None:
        """The item whose LIVE card (`sent`, still in `review`) is exactly this
        message of this owner's chat, or None. In a forum topic every message
        replies to the topic's root, so "is a reply" alone never counts."""
        item = await self._store.card_by_message(owner_id, int(chat_id), int(message_id))
        if item is None or item["status"] != st.REVIEW \
                or item["card_state"] != cd.CARD_SENT:
            return None
        return item

    async def redraft_card(self, owner_id: int, chat_id: int, message_id: int,
                           comment: str, now: datetime | None = None) -> dict | None:
        """The owner's reply to a live card: back to `planned` with the comment,
        the card told, the drafter woken. None when `message_id` is not a live card
        of this owner in this chat — the message is then an ordinary one."""
        now = now or self._clock()
        item = await self.live_card(owner_id, chat_id, message_id)
        if item is None:
            return None
        text = str(comment or "").strip()[:COMMENT_MAX_CHARS]
        if not text:
            raise ContentRejected("no_comment", "Say what to change.")
        if item["redrafts"] >= MAX_REDRAFTS:
            raise ContentRejected("redraft_limit", (
                f"This post was redone {MAX_REDRAFTS} times already."))
        if not item["publish_at"] or item["publish_at"] <= iso_utc(now):
            raise ContentRejected("too_late", "The publish time has passed.")
        updated = await self._store.redraft(item["id"], owner_id, text, MAX_REDRAFTS,
                                            iso_utc(now))
        if updated is None:
            raise ContentRejected("stale_card", "This card is out of date.")
        await self._tell_card(updated, cd.REDRAFTING_TEXT)
        await self._guarded("redraft wake", self._wake_drafter_now(owner_id, now))
        return updated

    async def is_card_topic(self, chat_id: int, thread_id: int | None) -> bool:
        """Whether `(chat_id, thread_id)` is the card topic: the stored thread id,
        in the private chat of a plan owner (a thread id is only unique per chat)."""
        if thread_id is None:
            return False
        if _topic_id(await self._settings.get(TOPIC_KEY)) != int(thread_id):
            return False
        return await self._store.owns_items(int(chat_id))

    # --- the publisher routine --------------------------------------------------
    async def due(self, owner_id: int) -> list[dict]:
        return await self._store.list_items(owner_id, status=st.QUEUED)

    async def claim(self, owner_id: int, item_id: int,
                    now: datetime | None = None) -> dict:
        """`queued -> publishing` for one publisher run, then the approval check.

        The claim is one statement (a second claimer matches nothing). The media
        hashes are re-checked AFTER it, while the item is exclusively this run's: a
        file that changed since approval — or is gone — sends the item back to
        `review` with the approval cleared and `error` naming the file, and the
        claim is refused. This binds intent; a file can still change between this
        check and the script reading it.
        """
        now = now or self._clock()
        claimed = await self._store.claim(int(item_id), owner_id, iso_utc(now))
        if claimed is None:
            item = await self.require(item_id, owner_id)
            raise ContentRejected("not_queued", (
                f"#{item['id']} is {item['status']}: only a queued item can be claimed. "
                "Do not publish it."))
        for entry in stored_media(claimed["media"]):
            digest = await self._media.sha256(owner_id, entry["path"])
            if entry["sha256"] is None or digest != entry["sha256"]:
                error = (f"{entry['path']} changed (or disappeared) after approval; "
                         "review it and approve again.")
                await self._store.update(claimed["id"], owner_id, {
                    "status": st.REVIEW, "error": error, "claimed_at": None,
                    **_unapproved(claimed["media"]),
                }, expect=(st.PUBLISHING,))
                raise ContentRejected("media_changed", (
                    f"Refused: {error} The item is back in review. Do not publish it."))
        return claimed

    async def mark_published(self, owner_id: int, item_id: int, url: Any) -> dict:
        values = {"status": st.PUBLISHED, "published_url": _https_url(url),
                  "error": None}
        return await self._finish(owner_id, item_id, values)

    async def mark_failed(self, owner_id: int, item_id: int, error: Any) -> dict:
        text = str(error or "").strip()
        if not text:
            raise ContentRejected("no_error", "Say what went wrong in 'error'.")
        return await self._finish(owner_id, item_id,
                                  {"status": st.FAILED, "error": text[:ERROR_MAX_CHARS]})

    async def _finish(self, owner_id: int, item_id: int, values: dict) -> dict:
        updated = await self._store.update(int(item_id), owner_id, values,
                                           expect=(st.PUBLISHING,))
        if updated is None:
            item = await self.require(item_id, owner_id)
            raise ContentRejected("not_publishing", (
                f"#{item['id']} is {item['status']}, not publishing: only an item "
                "you claimed can be reported."))
        return updated

    # --- the tick -------------------------------------------------------------------
    async def tick(self, now: datetime) -> None:
        """Late -> failed, blank -> failed, due -> queued, wake the publisher,
        unstick; then, each in its own guarded step: the missed posts' cards, the
        drafter's wakes and timeouts, and one approval card.

        Every step is one conditional statement per owner, so N ticks at once (every
        worker runs `scheduler_tick` in redis mode) can neither queue an item twice,
        count a wake twice, wake a routine twice, nor send a card twice — the wakes
        are also the scheduler's own `wake_if_idle` compare-and-set. No LLM call
        happens here.
        """
        now_iso = iso_utc(now)
        cutoff = iso_utc(now - LATE_WINDOW)
        late = await self._store.fail_late(cutoff, now_iso, MISSED_WINDOW_ERROR)
        empty = await self._store.fail_empty(now_iso, NOTHING_TO_PUBLISH_ERROR)
        promoted = await self._store.promote_due(now_iso, cutoff)
        if late or empty or promoted:
            log.info("content tick", late=len(late), empty=len(empty),
                     queued=len(promoted))
        job_id = await self._publisher_job_id()
        for owner_id in await self._store.wake_owners(now_iso):
            await self._wake(owner_id, job_id, now, now_iso)
        for owner_id in await self._store.publishing_owners():
            await self._unstick(owner_id, job_id, now, now_iso)
        await self._guarded("missed cards", self._missed(now_iso))
        await self._guarded("drafter", self._drafting(now, now_iso))
        await self._guarded("card outbox", self._card_outbox(now, now_iso))

    @staticmethod
    async def _guarded(step: str, work: Any) -> None:
        """One tick step whose failure must not stop the others: logged, dropped,
        tried again on the next tick."""
        try:
            await work
        except Exception:                            # noqa: BLE001 - next tick retries
            log.warning("content tick step failed", step=step, exc_info=True)

    async def _usable_routine(self, owner_id: int, job_id: int | None,
                              key: str = PUBLISHER_KEY
                              ) -> tuple[dict | None, str | None]:
        """The routine's description, or why there is none to wake."""
        role, template, verb = _ROLES[key]
        if job_id is None:
            return None, (f"No {role} routine is set: choose one in the calendar "
                          f"(an on-demand routine from the '{template}' template).")
        desc = await self._describe(job_id, owner_id, key)
        if desc is None:
            return None, (f"The {role} routine #{job_id} no longer exists: choose "
                          "another in the calendar.")
        if desc.get("kind") != "manual":
            return None, (f"The {role} routine #{job_id} is not an on-demand "
                          "routine: choose one scheduled 'On demand'.")
        if desc.get("state") != "active":
            return None, (f"The {role} routine #{job_id} is paused: resume it to "
                          f"{verb}.")
        return desc, None

    @staticmethod
    def _busy(desc: dict, now_iso: str) -> bool:
        """A live lease (a run in progress) or a pending wake (`next_run_at` set):
        either way a run that may take the item is already coming."""
        lease = desc.get("lease_until")
        return desc.get("next_run_at") is not None or bool(lease and lease > now_iso)

    async def _wake_port(self, key: str, job_id: int, now_iso: str) -> bool:
        result = self._port(key).wake_if_idle(job_id, now_iso)
        return bool(await result if inspect.isawaitable(result) else result)

    async def _wake(self, owner_id: int, job_id: int | None, now: datetime,
                    now_iso: str) -> None:
        desc, problem = await self._usable_routine(owner_id, job_id)
        if problem is not None:
            await self._store.note_queued(owner_id, now_iso, problem)
            return
        after = [iso_utc(now + timedelta(minutes=m)) for m in WAKE_BACKOFF_MINUTES]
        if self._busy(desc, now_iso):
            await self._store.postpone_wakes(owner_id, now_iso, after)
            return
        failed = await self._store.fail_unclaimed(owner_id, now_iso, MAX_WAKES,
                                                  NEVER_PICKED_ERROR)
        if failed:
            log.warning("content items never picked up by the publisher",
                        items=[i["id"] for i in failed], job_id=job_id)
        counted = await self._store.take_wakes(owner_id, now_iso, after, MAX_WAKES)
        if not counted:
            return
        if not await self._wake_port(PUBLISHER_KEY, job_id, now_iso):
            # Turned busy between the check and the wake (a run started by hand, a
            # wake from elsewhere): nothing was sent, so nothing is counted.
            await self._store.release_wakes(counted, now_iso)
            return
        log.info("content publisher woken", job_id=job_id, items=counted)

    async def _unstick(self, owner_id: int, job_id: int | None, now: datetime,
                       now_iso: str) -> None:
        desc = await self._describe(job_id, owner_id) if job_id is not None else None
        seconds = float((desc or {}).get("timeout_seconds") or self._fallback_timeout)
        cutoff = iso_utc(now - timedelta(seconds=seconds) - STUCK_GRACE)
        stuck = await self._store.mark_stuck(owner_id, cutoff, now_iso, STUCK_ERROR)
        if stuck:
            log.warning("content items stuck in publishing",
                        items=[i["id"] for i in stuck])

    # --- the tick: missed posts --------------------------------------------------------
    async def _missed(self, now_iso: str) -> None:
        """A post still `planned`, `drafting` or unapproved in `review` when its time
        came is published by nobody (only `scheduled` is ever queued); its badge is
        `needs_attention`'s. Here its card stops being live and says so."""
        for row in await self._store.supersede_missed(now_iso):
            if row.get(cd.PREVIOUS_CARD) == cd.CARD_SENT:
                await self._tell_card(row, cd.MISSED_TEXT)

    # --- the tick: the drafter ---------------------------------------------------------
    async def _drafting(self, now: datetime, now_iso: str) -> None:
        """Timed-out drafts back to `planned` first, then the wakes: an item the
        sweep returned may be the very one the next run should take."""
        job_id = await self._routine_job_id(DRAFTER_KEY)
        horizon = iso_utc(now + self._lead)
        for owner_id in await self._store.drafting_owners():
            await self._sweep_drafting(owner_id, job_id, now, now_iso)
        holding = set(await self._store.drafting_owners())
        for owner_id in await self._store.draft_owners(now_iso, horizon):
            await self._wake_drafter(owner_id, job_id, now, now_iso, horizon,
                                     holding=owner_id in holding)

    async def _wake_drafter_now(self, owner_id: int, now: datetime) -> None:
        """The drafter step for one owner, outside the tick (after a redraft)."""
        job_id = await self._routine_job_id(DRAFTER_KEY)
        holding = owner_id in await self._store.drafting_owners()
        await self._wake_drafter(owner_id, job_id, now, iso_utc(now),
                                 iso_utc(now + self._lead), holding=holding)

    async def _wake_drafter(self, owner_id: int, job_id: int | None, now: datetime,
                            now_iso: str, horizon: str, *, holding: bool) -> None:
        """The publisher's wake budget, for the drafter and the ONE item its next
        run will claim: missing/paused -> explained on the due items, nothing
        counted; busy -> postponed, nothing counted; idle -> the head item's wake
        counted by compare-and-set and `wake_if_idle` called once; three wakes
        for nothing -> the item stays `planned` with an error until the owner
        retries.

        `holding` (an item of this owner is `drafting`) is busy too: a run's lease
        can lapse before the drafting timeout returns its item, and a wake then
        would only meet `claim_draft`'s one-per-run refusal and be counted."""
        desc, problem = await self._usable_routine(owner_id, job_id, DRAFTER_KEY)
        if problem is not None:
            await self._store.note_planned(owner_id, now_iso, horizon, MAX_WAKES,
                                           problem)
            return
        await self._store.clear_planned_notes(owner_id, now_iso, MAX_WAKES)
        after = [iso_utc(now + timedelta(minutes=m)) for m in WAKE_BACKOFF_MINUTES]
        if holding or self._busy(desc, now_iso):
            await self._store.postpone_draft_wakes(owner_id, now_iso, horizon, after,
                                                   MAX_WAKES)
            return
        ignored = await self._store.exhaust_draft_wakes(owner_id, now_iso, horizon,
                                                        MAX_WAKES, DRAFTER_IGNORED_ERROR)
        if ignored:
            log.warning("content items never claimed by the drafter",
                        items=[i["id"] for i in ignored], job_id=job_id)
        counted = await self._store.take_draft_wake(owner_id, now_iso, horizon, after,
                                                    MAX_WAKES)
        if counted is None:
            return
        if not await self._wake_port(DRAFTER_KEY, job_id, now_iso):
            await self._store.release_draft_wake(counted, now_iso)
            return
        log.info("content drafter woken", job_id=job_id, item=counted)

    async def _sweep_drafting(self, owner_id: int, job_id: int | None, now: datetime,
                              now_iso: str) -> None:
        desc = (await self._describe(job_id, owner_id, DRAFTER_KEY)
                if job_id is not None else None)
        seconds = float((desc or {}).get("timeout_seconds") or self._fallback_timeout)
        cutoff = iso_utc(now - timedelta(seconds=seconds) - STUCK_GRACE)
        swept = await self._store.sweep_drafting(owner_id, cutoff, now_iso,
                                                 MAX_DRAFT_ATTEMPTS, DRAFT_TIMEOUT_ERROR)
        if swept:
            log.warning("content drafts timed out",
                        items=[i["id"] for i in swept])

    # --- the tick: the approval card outbox --------------------------------------------
    async def _card_outbox(self, now: datetime, now_iso: str) -> None:
        """At most ONE card per tick: recover a dead worker's `sending`, take one
        `pending` card by compare-and-set, send it, record the outcome. A process
        with no Bot touches nothing — its card waits for one that has."""
        cards = self._cards_port()
        if cards is None:
            return
        await self._store.recover_cards(iso_utc(now - cd.SENDING_STALE), now_iso)
        item = await self._store.claim_card(now_iso)
        if item is None:
            return
        try:
            await self._deliver_card(cards, item, now, now_iso)
        except Exception as e:                        # noqa: BLE001 - counted, retried
            log.warning("content card send failed", item=item["id"], exc_info=True)
            await self._store.card_unsent(item["id"], now_iso,
                                          error=f"{type(e).__name__}: {e}",
                                          max_attempts=cd.MAX_CARD_ATTEMPTS)

    async def _deliver_card(self, cards: Any, item: dict, now: datetime,
                            now_iso: str) -> None:
        owner_id = int(item["owner_id"])
        paths = []
        for entry in stored_media(item["media"]):
            found = await self._media.locate(owner_id, entry["path"])
            if found is None:
                await self._store.card_unsent(
                    item["id"], now_iso, max_attempts=cd.MAX_CARD_ATTEMPTS,
                    error=f"{entry['path']}: no such file in the agent's home.")
                return
            paths.append(str(found))
        # The card goes to the owner's private chat with the bot: a user id is the
        # Telegram user id.
        chat_id = owner_id
        thread = await self._card_topic(cards, chat_id, now)
        if thread is _WAIT:
            await self._store.card_unsent(item["id"], now_iso, error=None,
                                          max_attempts=cd.MAX_CARD_ATTEMPTS)
            return
        card = {
            "item_id": item["id"], "chat_id": chat_id, "thread_id": thread,
            "title": item["title"], "channel": item["channel"],
            "format": item["format"], "body": item["body"],
            "publish_at_local": self.local_time(item["publish_at"]), "media": paths,
            "media_kind": card_media_kind(item["media"]),
            "revision": item["card_revision"],
            "calendar_url": self.calendar_url(item["id"]),
        }
        result = await self._send(cards, card)
        if result.get("topic_gone"):
            # The topic was deleted in Telegram: re-create it once, then give up.
            thread = await self._card_topic(cards, chat_id, now, gone=thread)
            if thread is _WAIT:
                await self._store.card_unsent(item["id"], now_iso, error=None,
                                              max_attempts=cd.MAX_CARD_ATTEMPTS)
                return
            result = await self._send(cards, {**card, "thread_id": thread})
            if result.get("topic_gone"):
                await self._store.card_unsent(
                    item["id"], now_iso, max_attempts=1,
                    error=(f"The '{cd.TOPIC_NAME}' topic is gone and re-creating it "
                           "did not help: approve this post in the calendar."))
                return
        message_id = result.get("message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            error = str(result.get("error") or "Telegram answered without a message id.")
            await self._store.card_unsent(item["id"], now_iso,
                                          error=error[:ERROR_MAX_CHARS],
                                          max_attempts=cd.MAX_CARD_ATTEMPTS)
            return
        sent = await self._store.card_sent(item["id"], item["card_revision"], chat_id,
                                           message_id, now_iso)
        if sent is None:
            # Edited (or missed) while the card was on its way: it is stale already.
            await self._tell_card({"id": item["id"], "card_chat_id": chat_id,
                                   "card_message_id": message_id}, cd.CHANGED_TEXT)

    @staticmethod
    async def _send(cards: Any, card: dict) -> dict:
        """`ApprovalCards.send`, held to its contract: a dict, never an exception."""
        try:
            result = await cards.send(card)
        except Exception as e:                        # noqa: BLE001 - the port's bug
            return {"error": f"{type(e).__name__}: {e}"}
        return result if isinstance(result, dict) else {
            "error": "the card sender answered nothing usable"}

    async def _card_topic(self, cards: Any, chat_id: int, now: datetime,
                          gone: int | None = None) -> Any:
        """The card topic's thread id; None to send flat (no topics in this chat);
        `_WAIT` while another tick creates it.

        Creating is reserved first — `TOPIC_KEY` goes from absent (or the `gone`
        id, or a reservation older than `SENDING_STALE`) to `creating:<now>` by
        compare-and-set — so concurrent ticks never create two topics. The winner
        stores the new id, again by compare-and-set; a chat without topics
        releases the reservation, and the next card asks again.
        """
        raw = await self._settings.get(TOPIC_KEY)
        current = _topic_id(raw)
        if current is not None and current != gone:
            return current
        if raw and raw.startswith(TOPIC_RESERVED):
            try:
                since = datetime.fromisoformat(raw[len(TOPIC_RESERVED):])
            except ValueError:
                since = None
            if since is not None and now - since < cd.SENDING_STALE:
                return _WAIT
        token = f"{TOPIC_RESERVED}{iso_utc(now)}"
        if not await self._store.meta_swap(TOPIC_KEY, raw, token):
            return _WAIT
        try:
            topic = await cards.ensure_topic(chat_id, cd.TOPIC_NAME)
        except Exception:                             # noqa: BLE001 - send flat
            log.warning("content card topic not created", exc_info=True)
            topic = None
        if isinstance(topic, bool) or not isinstance(topic, int) or topic <= 0:
            await self._store.meta_swap(TOPIC_KEY, token, None)
            return None
        await self._store.meta_swap(TOPIC_KEY, token, str(topic))
        return topic

"""The wire shapes of a content item: the row the calendar reads, and the bodies it
posts.

`to_wire` is the `ContentItemWire` contract the SPA is written against — exactly
those keys, no more: `owner_id`, `approved_by` and the tick's bookkeeping
(`next_wake_at`, `claimed_at`, `draft_wakes`, `draft_next_wake_at`,
`drafting_since`, the card's revision, ids, attempts and claim) stay server-side.

The request models are lenient on purpose, like the Routines router's: every field
is `Any` with a default, because `ContentService` owns the validation and answers
with the field's own code (`bad_channel`, `bad_media`, `bad_time`, `bad_status`) in
a sentence the item dialog shows verbatim. A Pydantic 422 in front of that would
replace the message with a schema error. Both are dumped with `exclude_unset=True`:
the service validates the keys PRESENT, so an absent key must stay absent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from claude_hermes.modules.content.domain.media import stored_media
from claude_hermes.modules.content.domain.status import item_needs_attention


class ContentBody(BaseModel):
    """`POST /api/content` and `PATCH /api/content/{id}` (any subset)."""

    model_config = ConfigDict(extra="ignore")

    channel: Any = None
    format: Any = None
    title: Any = None
    body: Any = None
    # A list of paths relative to the owner's agent_home; hashes are the server's.
    media: Any = None
    publish_at: Any = None
    group_key: Any = None
    source_path: Any = None
    status: Any = None


class RetryBody(BaseModel):
    """`POST /api/content/{id}/retry`: `{"mode": "repost"}` or
    `{"mode": "published", "url": "https://…"}`."""

    model_config = ConfigDict(extra="ignore")

    mode: Any = None
    url: Any = None


class PublisherBody(BaseModel):
    """`PUT /api/content/publisher` and `PUT /api/content/drafter`."""

    model_config = ConfigDict(extra="ignore")

    job_id: Any = None


class ThemesBody(BaseModel):
    """`POST /api/content/approve-themes`: `{"from": iso, "to": iso}`."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    start: Any = Field(None, alias="from")
    end: Any = Field(None, alias="to")


def to_wire(item: dict, now_iso: str | None = None) -> dict:
    """The row as `ContentItemWire`. Times stay ISO-8601 UTC, as stored: the
    browser renders them in the device's zone. `needs_attention` is computed
    against `now_iso` (the request's clock when not given): a post whose time
    passed before anybody approved it needs the owner, and no row says so."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    return {
        "id": item["id"], "channel": item["channel"], "format": item["format"],
        "title": item["title"], "body": item["body"],
        "media": stored_media(item["media"]),
        "publish_at": item["publish_at"], "status": item["status"],
        "group_key": item["group_key"], "source_path": item["source_path"],
        "thread_id": item["thread_id"], "created_via": item["created_via"],
        "approved_at": item["approved_at"], "attempts": item["attempts"],
        "published_url": item["published_url"], "error": item["error"],
        "needs_attention": item_needs_attention(item, now_iso),
        "created_at": item["created_at"], "updated_at": item["updated_at"],
        "theme_approved_at": item["theme_approved_at"],
        "draft_attempts": item["draft_attempts"], "redrafts": item["redrafts"],
        "draft_comment": item["draft_comment"], "card_state": item["card_state"],
    }

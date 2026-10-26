"""`mcp__hermes__content_plan` — the content module's one agent-facing tool.

This file is the tool's declaration (name, description, argument schema) AND its
text answers. It is declared the `ext_manage` way: `ContentPlanTool` carries
`name`/`description`/`schema` as attributes, turns' `hermes_server.declared_spec`
reads them off the port, and `routine_server.py` builds the very same object into
the `hermes` server of the deploy owner's routine runs — one tool, spelled once,
with one name in both places so one skill text works in both.

The description is prompt-cache surface: the boot fingerprint covers every hermes
tool's name, description and schema, so a changed byte here resets every lane's
session once, on the next boot. Change it deliberately, never in passing. It names no channel list on purpose — channels are
config, and a config edit must not rewrite the prompt.

The body is `ContentService`; this file turns arguments into calls and rows into
sentences. `origin` is `"chat"` or `"cron:<job_id>"`, and it decides two things:
the item's `created_via`, and whether the publisher-only and the drafter-only
actions answer at all.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.content.domain import status as st
from claude_hermes.modules.content.domain.media import stored_media
from claude_hermes.modules.content.domain.time import local_iso, parse_instant

TOOL_NAME = "content_plan"

TOOL_DESCRIPTION = (
    "The owner's content plan: what is published on which channel, and when. "
    "action=list|get|create|update|set_status|due|claim|published|failed|"
    "claim_draft|submit|draft_failed. Lifecycle: idea -> planned (theme approved by "
    "the owner) -> drafting -> review -> scheduled -> queued -> publishing -> "
    "published (draft is the hand-written way to review), or failed/stuck/skipped. "
    "Approval is the owner's alone: only they "
    "move an item from review to scheduled, in the calendar — you never can, so put "
    "a finished item in 'review' and tell them. One item is one post on one channel "
    "(channel such as instagram or linkedin; format such as post, reel, carousel); "
    "variants of one idea share a group_key. body is exactly what gets published. "
    "media is file paths relative to your home, one per line (png, jpg, jpeg, webp, "
    "gif, mp4, mov, webm). publish_at is ISO-8601: a time without an offset is read "
    "in the configured timezone, and every time in an answer is in that timezone. "
    "list filters by from/to/status/channel; get, update and set_status need id. In "
    "update an empty field is left unchanged and publish_at='none' moves the item to "
    "the backlog; changing body, media, channel, format or publish_at of a scheduled "
    "item sends it back to review, and changing title, channel, format or "
    "publish_at of a planned item sends it back to idea. set_status takes idea, "
    "draft, review or skipped. Items that are drafting, queued, publishing, stuck "
    "or published cannot be changed. due, claim, published (needs url) and "
    "failed (needs error) work only inside the "
    "publisher routine: claim an item before publishing it, and never publish an "
    "item you did not claim. claim_draft, submit (id, body, media, optional title) "
    "and draft_failed (id, error): the drafter routine only."
)

TOOL_SCHEMA = {
    "action": str, "id": int, "channel": str, "format": str, "title": str,
    "body": str, "media": str, "publish_at": str, "status": str, "group_key": str,
    "source_path": str, "url": str, "error": str, "from": str, "to": str,
}

# The fields `create`/`update` read. Everything else in the schema is an action
# argument or a filter.
_FIELDS = ("channel", "format", "title", "body", "media", "publish_at", "status",
           "group_key", "source_path")
_BACKLOG_WORDS = frozenset({"none", "null", "backlog"})
_PUBLISHER_ONLY = frozenset({"due", "claim", "published", "failed"})
_DRAFTER_ONLY = frozenset({"claim_draft", "submit", "draft_failed"})
_LIST_LIMIT = 100


def _reply(text: str, *, error: bool = False) -> dict:
    out: dict = {"content": [{"type": "text", "text": text}]}
    if error:
        out["is_error"] = True
    return out


def _given(args: Mapping) -> dict:
    """The item fields the model actually filled in.

    The SDK marks every schema key required, so a model sends `""` (or `0`) for
    the ones it means to leave alone: empty is "not given", never "clear it". The
    one field that can be cleared is `publish_at`, by name ('none').
    """
    out: dict = {}
    for key in _FIELDS:
        value = args.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if key == "publish_at" and str(value).strip().lower() in _BACKLOG_WORDS:
            out[key] = None
            continue
        out[key] = value
    return out


class ContentPlanTool:
    """turns' `ContentPlanPort`, and what `routine_server` lends a routine run:
    `name`/`description`/`schema` plus
    `handle(args, *, owner_id, origin, thread_id) -> MCP result dict`."""

    name = TOOL_NAME
    description = TOOL_DESCRIPTION
    schema = TOOL_SCHEMA

    def __init__(self, service: Any):
        self._service = service

    @property
    def service(self) -> Any:
        return self._service

    # --- rendering -----------------------------------------------------------------
    def _when(self, iso: str | None) -> str:
        return local_iso(iso, self._service.timezone) or "backlog (no time)"

    def _line(self, item: dict) -> str:
        fmt = f"/{item['format']}" if item["format"] else ""
        title = item["title"] or (item["body"][:60] + ("…" if len(item["body"]) > 60 else ""))
        now_iso = datetime.now(timezone.utc).isoformat()
        flag = f" ⚠ {item['error'] or 'needs the owner'}" \
            if st.item_needs_attention(item, now_iso) else ""
        return (f"#{item['id']} [{item['status']}] {item['channel']}{fmt} "
                f"{self._when(item['publish_at'])} — {title}{flag}")

    def _full(self, item: dict) -> str:
        lines = [self._line(item)]
        for label, key in (("group_key", "group_key"), ("source_path", "source_path"),
                           ("thread", "thread_id"), ("created_via", "created_via"),
                           ("published_url", "published_url"), ("error", "error")):
            if item.get(key):
                lines.append(f"{label}: {item[key]}")
        if item.get("theme_approved_at"):
            lines.append(f"theme approved: {self._when(item['theme_approved_at'])}")
        if item["approved_at"]:
            lines.append(f"approved: {self._when(item['approved_at'])}")
        if item.get("draft_comment"):
            lines.append(f"owner's comment on the last draft: {item['draft_comment']}")
        media = stored_media(item["media"])
        if media:
            lines.append("media (relative to your home):")
            lines += [f"- {m['path']}" for m in media]
        lines.append("body:")
        lines.append(item["body"])
        return "\n".join(lines)

    def _siblings(self, siblings: list[dict]) -> str:
        """The same idea on other channels, with the media they already have: a
        finished sibling's video is copied, not rendered again."""
        if not siblings:
            return ""
        lines = ["", "", "Other items of this idea (same group_key) — reuse a "
                 "sibling's finished media (status review, scheduled, queued, "
                 "publishing or published) by copying it into your folder instead "
                 "of rendering again, unless the owner's comment asks for a new one:"]
        for s in siblings:
            lines.append(self._line(s))
            lines += [f"  - {m['path']}" for m in stored_media(s["media"])]
        return "\n".join(lines)

    # --- the port ------------------------------------------------------------------
    async def handle(self, args: Mapping, *, owner_id: int, origin: str,
                     thread_id: str | None) -> dict:
        """One call, answered as the MCP result dict (`is_error` on a refusal)."""
        if int(owner_id or 0) <= 0:
            return _reply("Refused: the content plan belongs to a user, and this "
                          "conversation has none.", error=True)
        action = str(args.get("action") or "list").strip().lower()
        try:
            if action in _PUBLISHER_ONLY and not await self._service.is_publisher(origin):
                return _reply(
                    f"Refused: '{action}' works only inside the publisher routine's own "
                    "run. The owner publishes by approving items in the calendar.",
                    error=True)
            if action in _DRAFTER_ONLY and not await self._service.is_drafter(origin):
                return _reply(
                    f"Refused: '{action}' works only inside the drafter routine's own "
                    "run. Draft in chat with create/update and leave the item in "
                    "'review' for the owner.", error=True)
            return _reply(await self._dispatch(action, args, owner_id=owner_id,
                                               origin=origin, thread_id=thread_id))
        except ApplicationException as e:
            return _reply(e.message or e.code, error=True)

    async def _dispatch(self, action: str, args: Mapping, *, owner_id: int,
                        origin: str, thread_id: str | None) -> str:
        svc = self._service
        try:
            item_id = int(args.get("id") or 0)
        except (TypeError, ValueError):
            item_id = 0
        if action == "list":
            return await self._list(args, owner_id)
        if action == "create":
            item = await svc.agent_create(owner_id, _given(args), origin=origin,
                                          thread_id=thread_id if origin == "chat" else None)
            return f"Created {self._line(item)}"
        if action == "claim_draft":
            item = await svc.claim_draft(owner_id)
            if item is None:
                return "Nothing is due for drafting."
            text = ("Claimed for drafting — write its media under "
                    f"{svc.draft_folder(item)} (relative to your home), then submit "
                    "it with id, body and media, or report draft_failed with the "
                    "error:\n" + self._full(item))
            return text + self._siblings(await svc.group_siblings(owner_id, item))
        if action == "due":
            items = await svc.due(owner_id)
            if not items:
                return "Nothing is due."
            return "\n\n".join(self._full(i) for i in items)
        if not item_id:
            return f"'{action}' needs the item's id."
        if action == "get":
            return self._full(await svc.require(item_id, owner_id))
        if action == "update":
            fields = _given(args)
            if not fields:
                return "Nothing to update: give at least one field."
            item = await svc.agent_update(owner_id, item_id, fields)
            return f"Updated {self._line(item)}"
        if action == "set_status":
            item = await svc.agent_set_status(owner_id, item_id, args.get("status"))
            return f"Now {self._line(item)}"
        if action == "submit":
            fields = {k: v for k, v in _given(args).items()
                      if k in ("body", "media", "title")}
            item = await svc.submit(owner_id, item_id, fields)
            return (f"Submitted for the owner's approval: {self._line(item)}. Stop "
                    "here: do not publish it and do not approve it.")
        if action == "draft_failed":
            item = await svc.draft_failed(owner_id, item_id, args.get("error"))
            return f"Recorded the failed draft: {self._line(item)}"
        if action == "claim":
            item = await svc.claim(owner_id, item_id)
            return ("Claimed — publish exactly this, then report 'published' with the "
                    "post's url or 'failed' with the error:\n" + self._full(item))
        if action == "published":
            item = await svc.mark_published(owner_id, item_id, args.get("url"))
            return f"Recorded as published: {self._line(item)} {item['published_url']}"
        if action == "failed":
            item = await svc.mark_failed(owner_id, item_id, args.get("error"))
            return f"Recorded as failed: {self._line(item)}"
        return f"Unknown action '{action}'."

    async def _list(self, args: Mapping, owner_id: int) -> str:
        tz = self._service.timezone
        bounds = {}
        for key in ("from", "to"):
            raw = str(args.get(key) or "").strip()
            if raw:
                try:
                    bounds[key] = parse_instant(raw, tz)
                except ValueError as e:
                    return str(e)
        status = str(args.get("status") or "").strip().lower() or None
        channel = str(args.get("channel") or "").strip().lower() or None
        items = await self._service.list_items(
            owner_id, start=bounds.get("from"), end=bounds.get("to"), status=status,
            channel=channel, limit=500)
        if not (bounds or status):
            # The whole history is not a plan: with no range and no status, what is
            # done (published, skipped) stays out of the answer.
            items = [i for i in items if i["status"] not in st.BACKLOG_EXCLUDED]
        if not items:
            return "No items."
        shown = items[:_LIST_LIMIT]
        more = f"\n…and {len(items) - len(shown)} more; narrow with from/to." \
            if len(items) > len(shown) else ""
        return f"Times in {tz}.\n" + "\n".join(self._line(i) for i in shown) + more

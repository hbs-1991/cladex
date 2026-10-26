"""What the content plan needs from the outside world, stated on the consumer side.

`ContentStore` and `MediaFiles` are this module's own infrastructure. `Publisher` is
the scheduler seen from here — the composition root satisfies it with an adapter
over `SchedulerService` (`describe` via `get(job_id, owner_id)`, `wake_if_idle`
via `SchedulerService.wake_if_idle`), so neither module imports the other.
`Drafter` is the same port for the drafter routine, satisfied by the same adapter
instance: it takes the job id on every call. `ApprovalCards` is the Telegram card
sender, late-bound in the processes that have a Bot. `Settings` is
`core.meta.MetaStore`, read for the routine ids and the card topic.
"""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContentStore(Protocol):
    """Persistence for `content_items` — the only place its SQL lives
    (`infrastructure.repository.ContentRepository`).

    Every read and write names the OWNER: an item of somebody else is
    indistinguishable from one that does not exist. The tick's statements are the
    exception, and on purpose — one clock serves every owner's items, exactly as
    `claim_due` claims every owner's jobs.

    Every state change is a conditional UPDATE (`expect=` the statuses it may leave)
    so a request and the tick racing on one row cannot both win.
    """

    async def create(self, owner_id: int, values: dict[str, Any]) -> dict: ...

    async def get(self, item_id: int, owner_id: int) -> dict | None: ...

    async def list_items(self, owner_id: int, *, start_iso: str | None = ...,
                         end_iso: str | None = ..., status: str | None = ...,
                         channel: str | None = ..., group_key: str | None = ...,
                         limit: int = ...) -> list[dict]: ...

    async def backlog(self, owner_id: int) -> list[dict]: ...

    async def update(self, item_id: int, owner_id: int, values: dict[str, Any], *,
                     expect: Collection[str] | None = ...) -> dict | None: ...

    async def delete(self, item_id: int, owner_id: int, *,
                     forbid: Collection[str] = ...) -> bool: ...

    async def claim(self, item_id: int, owner_id: int, now_iso: str) -> dict | None: ...

    # --- the tick -------------------------------------------------------------
    async def fail_late(self, cutoff_iso: str, now_iso: str, error: str) -> list[dict]: ...

    async def promote_due(self, now_iso: str, cutoff_iso: str) -> list[dict]: ...

    async def wake_owners(self, now_iso: str) -> list[int]: ...

    async def note_queued(self, owner_id: int, now_iso: str, error: str) -> int: ...

    async def postpone_wakes(self, owner_id: int, now_iso: str,
                             after_wake: list[str]) -> int: ...

    async def take_wakes(self, owner_id: int, now_iso: str, after_wake: list[str],
                         max_wakes: int) -> list[int]: ...

    async def release_wakes(self, item_ids: list[int], now_iso: str) -> None: ...

    async def fail_unclaimed(self, owner_id: int, now_iso: str, max_wakes: int,
                             error: str) -> list[dict]: ...

    async def publishing_owners(self) -> list[int]: ...

    async def mark_stuck(self, owner_id: int, cutoff_iso: str, now_iso: str,
                         error: str) -> list[dict]: ...

    async def fail_empty(self, now_iso: str, error: str) -> list[dict]: ...

    # --- themes and the drafter ------------------------------------------------
    async def approve_themes(self, owner_id: int, start_iso: str, end_iso: str,
                             now_iso: str) -> list[dict]: ...

    async def claim_draft(self, owner_id: int, now_iso: str,
                          horizon_iso: str) -> tuple[dict | None, dict | None]: ...

    async def return_draft(self, item_id: int, owner_id: int, now_iso: str,
                           max_attempts: int, error: str) -> dict | None: ...

    async def drafting_owners(self) -> list[int]: ...

    async def sweep_drafting(self, owner_id: int, cutoff_iso: str, now_iso: str,
                             max_attempts: int, error: str) -> list[dict]: ...

    async def draft_owners(self, now_iso: str, horizon_iso: str) -> list[int]: ...

    async def note_planned(self, owner_id: int, now_iso: str, horizon_iso: str,
                           max_wakes: int, error: str) -> int: ...

    async def clear_planned_notes(self, owner_id: int, now_iso: str,
                                  max_wakes: int) -> int: ...

    async def postpone_draft_wakes(self, owner_id: int, now_iso: str,
                                   horizon_iso: str, after_wake: list[str],
                                   max_wakes: int) -> int: ...

    async def take_draft_wake(self, owner_id: int, now_iso: str, horizon_iso: str,
                              after_wake: list[str], max_wakes: int) -> int | None: ...

    async def release_draft_wake(self, item_id: int, now_iso: str) -> None: ...

    async def exhaust_draft_wakes(self, owner_id: int, now_iso: str, horizon_iso: str,
                                  max_wakes: int, error: str) -> list[dict]: ...

    async def redraft(self, item_id: int, owner_id: int, comment: str,
                      max_redrafts: int, now_iso: str) -> dict | None: ...

    # --- the approval card outbox ---------------------------------------------
    async def card_by_message(self, owner_id: int, chat_id: int,
                              message_id: int) -> dict | None: ...

    async def owns_items(self, owner_id: int) -> bool: ...

    async def recover_cards(self, stale_iso: str, now_iso: str) -> int: ...

    async def claim_card(self, now_iso: str) -> dict | None: ...

    async def card_sent(self, item_id: int, revision: str, chat_id: int,
                        message_id: int, now_iso: str) -> dict | None: ...

    async def card_unsent(self, item_id: int, now_iso: str, *, error: str | None,
                          max_attempts: int) -> dict | None: ...

    async def approve_card(self, item_id: int, owner_id: int, revision: str,
                           media: list[dict], now_iso: str) -> dict | None: ...

    async def supersede_missed(self, now_iso: str) -> list[dict]: ...

    async def meta_swap(self, key: str, expected: str | None,
                        value: str | None) -> bool: ...


@runtime_checkable
class MediaFiles(Protocol):
    """A plan item's media, resolved inside the OWNER's `agent_home`.

    `normalize` is the create/edit check (syntax and containment, not existence — a
    draft may name the file it is about to render); `sha256` and `locate` answer
    `None` for anything that is not an existing, admissible file.
    """

    async def normalize(self, owner_id: int, path: str) -> str: ...

    async def sha256(self, owner_id: int, path: str) -> str | None: ...

    async def locate(self, owner_id: int, path: str) -> Path | None: ...


@runtime_checkable
class Publisher(Protocol):
    """The publisher routine, seen from the content plan.

    `describe` answers `{"name", "kind", "state", "next_run_at", "lease_until",
    "timeout_seconds"}` for a job of `owner_id`, or `None` (gone, or not theirs).
    `wake_if_idle` is the scheduler's ONE compare-and-set wake — never `run_now`,
    which clears a live lease — and is `True` only when it set the wake.

    Either may be sync or async; the service awaits whatever comes back.
    """

    def describe(self, job_id: int, owner_id: int) -> Any: ...

    def wake_if_idle(self, job_id: int, now_iso: str) -> Any: ...


@runtime_checkable
class Drafter(Protocol):
    """The drafter routine, seen from the content plan: `Publisher`'s exact shape
    (`describe`, `wake_if_idle`), and in `main.py` the very same adapter instance —
    the job id on every call is what tells the two routines apart."""

    def describe(self, job_id: int, owner_id: int) -> Any: ...

    def wake_if_idle(self, job_id: int, now_iso: str) -> Any: ...


@runtime_checkable
class ApprovalCards(Protocol):
    """The Telegram approval card sender (telegram's `TelegramApprovalCards`, bound
    in `build_delivery`). Never raises across the port.

    `send(card)` takes `{"item_id", "chat_id", "thread_id" | None, "title",
    "channel", "format", "body", "publish_at_local", "media": [absolute paths],
    "media_kind": "images" | "video" | "none", "revision", "calendar_url"}` and
    answers `{"message_id": int, "thread_id": int | None}` (the text message that
    carries the buttons), `{"topic_gone": True}` when the thread is missing, or
    `{"error": str}` for any other refusal.
    `edit(chat_id, message_id, text)` replaces the card's text and drops its
    buttons. `ensure_topic(chat_id, name)` creates a forum topic: its thread id, or
    None when the chat has no topics (Threaded Mode off) — the card then goes flat.
    """

    async def send(self, card: dict) -> dict: ...

    async def edit(self, chat_id: int, message_id: int, text: str) -> None: ...

    async def ensure_topic(self, chat_id: int, name: str) -> int | None: ...


@runtime_checkable
class Settings(Protocol):
    """`core.meta.MetaStore`: the publisher is `meta['content_publisher_job_id']`,
    the drafter `meta['content_drafter_job_id']`, the card topic
    `meta['content_card_topic']` (read here, compare-and-set by
    `ContentStore.meta_swap`)."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...

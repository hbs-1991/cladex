"""What the telegram module needs from the rest of the system, stated here.

Consumer-side ports (Wave 2 locked decision): every one of these is satisfied at
composition by an object from another module — `turns` runs the turn, `identity`
answers the access question, `extensions` owns secrets and the catalog, `files`
knows where a lane's inbox is. None of them is imported here; structural typing
does the matching, which is what keeps `telegram` independent of all of them.

Ports are named for the capability, not the provider.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from claude_hermes.core.contracts import Attachment

# The raw Bot API call. aiogram does not type Rich Messages and its `make_request`
# expects a TelegramMethod, so the rich sender, the topic manager and the question
# keyboard all go through this one callable instead.
BotApiCall = Callable[[str, dict], Awaitable[dict]]


@runtime_checkable
class BotApiPort(Protocol):
    async def call(self, method: str, payload: dict) -> dict: ...


@runtime_checkable
class SenderPort(Protocol):
    """Delivers a finished reply into a chat. `send_draft` is the ephemeral
    status line; a sender that cannot draft may no-op it."""

    async def send(self, chat_id: int, thread_id: int | None, text: str) -> None: ...

    async def send_draft(self, chat_id: int, thread_id: int | None, status: str,
                         draft_id: int = 1) -> None: ...


@runtime_checkable
class AclPort(Protocol):
    """`check` answers "allow" or "deny"; `is_owner` gates the two commands the
    gateway intercepts before the model can see them.

    `profile` is optional and positional-or-keyword so an older implementation
    with a two-argument `check` still satisfies this structurally — it is news
    about the sender, never part of the decision. What is passed is telegram's own
    `SenderProfile`; identity reads it by attribute name, so neither module has to
    import the other's type.
    """

    async def check(self, user_id: int, chat_type: str, profile: Any = None) -> str: ...

    async def is_owner(self, user_id: int) -> bool: ...


@runtime_checkable
class TurnRunnerPort(Protocol):
    async def run_turn(self, session_key: str, text: str, *, user_id: int = 0,
                       attachments: list | None = None,
                       channel: str = "telegram") -> str: ...

    async def reset(self, session_key: str) -> None: ...


@runtime_checkable
class TurnEventBusPort(Protocol):
    """`subscribe` returns the unsubscribe callable — the presenter is registered
    for exactly the length of one turn.

    `ready` is the half that matters since Wave 3b: `subscribe` only *schedules*
    the Redis SUBSCRIBE, the worker can claim the row and emit `turn.start` before
    the confirmation arrives, and `Presenter.on_turn_event` binds its turn on
    `turn.start` alone — one missed event means no status line for the whole turn,
    silently. So the dispatcher awaits `ready(key)` before it hands the message to
    the router.
    """

    def subscribe(self, session_key: str, listener: Callable[[Any], Awaitable[None]]
                  ) -> Callable[[], None]: ...

    async def ready(self, session_key: str) -> None: ...


@runtime_checkable
class SecretStorePort(Protocol):
    """Upsert one secret. Raises `domain.exceptions.SecretNameError` when the name
    or the value would corrupt the store.

    Async since Wave 3a: the provider seals the value and writes a row, where it
    used to rewrite a file on the volume.

    `owner_id` is required and keyword-only since per-user connectors: a secret
    belongs to the person who sent it, and a store called without one would seal
    a credential into somebody else's row. `/secret` is owner-gated, so the id
    the command passes is the owner's — but the port says so explicitly rather
    than letting the composition root bind a constant nobody can see.
    """

    async def put(self, name: str, value: str, *, owner_id: int) -> None: ...


@runtime_checkable
class ApprovalPort(Protocol):
    """The two-step draft approval. `begin` returns (manifest, surface, token) or
    None when no draft by that name is queued; `confirm` returns (manifest, raw)
    or None when the token no longer matches the draft.

    Async since Wave 3a, and no `home` anywhere in it: the draft queue and the
    snapshots are tables, so the provider is `extensions.ApprovalService` itself
    rather than an adapter with a workspace bound into it.
    """

    async def begin(self, name: str) -> tuple[Any, str, str] | None: ...

    async def confirm(self, name: str, token: str) -> tuple[Any, str] | None: ...

    async def clear(self, kind: str, name: str) -> None: ...


@runtime_checkable
class CatalogPort(Protocol):
    """The reader side of the catalog. `is_repo_entry` is the anti-downgrade
    check; `get` is the post-write verification."""

    def is_repo_entry(self, name: str) -> bool: ...

    def get(self, name: str) -> Any | None: ...


@runtime_checkable
class CatalogWriterPort(Protocol):
    def promote(self, manifest: Any, raw: str) -> None: ...


@runtime_checkable
class LearnPromptPort(Protocol):
    def build(self, text: str) -> str: ...


@runtime_checkable
class ThreadTitlerPort(Protocol):
    """Names a freshly opened forum topic. `is_new_thread` must be asked BEFORE
    the turn runs — the turn writes the session row that makes it stop being new."""

    async def is_new_thread(self, session_key: str, thread_id: int | None) -> bool: ...

    def spawn(self, chat_id: int, thread_id: int, text: str,
              session_key: str | None = None) -> None: ...


# The three collaborators `infrastructure.titler.Titler` — the one provider of
# `ThreadTitlerPort` — takes from other modules. Stated here so the titler imports
# nothing from `sessions`: the model call is sessions' `SdkTitleGenerator`, the
# "is this lane bound yet?" question is its `SessionRouter`, and the title write is
# its `ThreadTitleService`; the composition root hands the three over.


@runtime_checkable
class TitleGeneratorPort(Protocol):
    """A title for a first message; `""` when the model gave nothing usable."""

    async def generate(self, text: str) -> str: ...


@runtime_checkable
class LaneBindingPort(Protocol):
    """Has this lane run a turn yet? A thread is new exactly while it has no
    session row — which is why the titler asks before the turn runs."""

    async def is_unbound(self, session_key: str) -> bool: ...


@runtime_checkable
class ThreadTitleWriterPort(Protocol):
    """Record a generated title for the lane, and tell an open web tab. A title
    the person typed themselves is never overwritten — the writer's rule."""

    async def apply(self, session_key: str, title: str) -> None: ...


@runtime_checkable
class VoicePort(Protocol):
    async def transcribe(self, audio: bytes, filename: str) -> str: ...

    async def synthesize(self, text: str) -> bytes: ...


@runtime_checkable
class AskBrokerPort(Protocol):
    """Delivers one button press. False means "this question is no longer yours
    to answer" — a stale button, a second tap, or a stranger's tap.

    Async since Wave 3b: the question may have been opened by a worker in another
    process, so resolving it is a compare-and-delete in Redis rather than a dict
    pop here.
    """

    async def resolve(self, qid: str, token: str, user_id: int) -> bool: ...


@runtime_checkable
class ContentDecisionsPort(Protocol):
    """The content plan's answers to what the owner did with an approval card.

    Satisfied at composition by the content module's decisions object; nothing
    here imports it. The gateway checks the sender is the deploy owner BEFORE it
    asks any of these, and the provider refuses a non-owner again on its own —
    two locks, because an approval schedules a public post.

    `approve_from_card` answers the popup text whatever happened (approved, a
    stale revision, a second tap, a wrong status), so a button is always
    answered. `redraft_from_reply` answers `None` when the replied-to message is
    not a live card in that chat — the message then proceeds as an ordinary
    turn; a string is the reply to send, and the message stops there.
    `is_card_topic` tells the titler to keep its hands off the cards' topic.
    """

    async def approve_from_card(self, item_id: int, revision: str,
                                user_id: int) -> str: ...

    async def redraft_from_reply(self, chat_id: int, reply_to_message_id: int,
                                 user_id: int, comment: str) -> str | None: ...

    async def is_card_topic(self, chat_id: int, thread_id: int | None) -> bool: ...


@runtime_checkable
class LaneModelsPort(Protocol):
    """Which model a lane runs, and the one way to change it.

    Satisfied by sessions' `SessionService`. `effective` is the shared gatherer —
    the thread's explicit pair, then the live session's, then the lane owner's
    default, then the deploy default — and returns `core.model_catalog.Choice`;
    `set_model` writes the lane's explicit pair (`model=None` clears it, and with
    it the session's recorded pair) after validating it, and emits `thread.model`
    so an open web tab sees the switch.

    `owner_id` is the LANE owner, not the tapper: a group lane's default is the
    deploy owner's and a private chat's is the chat's, which is `core.session_keys
    .lane_owner`'s rule and why the caller passes it rather than the sender id.

    A lane with no `threads` row cannot hold a pair. `set_model` says so by
    raising `core.exceptions.NotFound`; `effective` may either raise the same or
    answer `None`, and the menu turns both into "send a message here first"
    (AC #8) — it never invents a row.
    """

    async def effective(self, session_key: str, owner_id: int) -> Any: ...

    async def set_model(self, session_key: str, model: str | None,
                        effort: str | None) -> Any: ...


@runtime_checkable
class ModelDefaultsPort(Protocol):
    """One person's default (model, effort) for their NEW conversations.

    Satisfied by identity's users service. `default_of` answers the pair —
    `(None, None)` when they have never set one — and never resolves it against
    the deploy default: "not set" is a state the menu shows. A changed default
    never moves a live session (Locked Decisions), which is why it is a separate
    port from `LaneModelsPort` and why the menu offers "Also use it in this chat"
    as an explicit second step.
    """

    async def default_of(self, user_id: int) -> tuple[str | None, str | None]: ...

    async def set_default(self, user_id: int, model: str | None,
                          effort: str | None) -> Any: ...


@runtime_checkable
class ModelMenuPort(Protocol):
    """The `/model` keyboard, as `MessageRouter` sees it.

    Satisfied by this module's own `infrastructure/model_menu.TelegramModelMenu`,
    and a port rather than an import for the same reason `ThreadTitlerPort` is:
    `application` and `infrastructure` are independent siblings under the
    `telegram layers` contract. It returns the text to reply with, or `None` when
    it already put a keyboard in the chat itself.
    """

    async def open(self, event: Any, scope: str = "t") -> str | None: ...


@runtime_checkable
class WorkspacePort(Protocol):
    """Where one session lane's attachments land — in the LANE OWNER's tree.

    `lane_dir` is where the bytes go and `lane_owner` whose `files` row they
    become; the intake service asks both and registers the attachment through
    `LaneFilesPort`.

    Async and lane-aware since Wave 4a: the destination is decided by
    `lane_owner(session_key, channel, user_id, ...)` against the workspace table,
    because with one bound `home` a non-owner's photo landed in the deploy owner's
    `inbox/`, where the sender's own turn could not read it (advisor blocker B5).
    """

    async def lane_dir(self, session_key: str, channel: str = "telegram",
                       user_id: int = 0) -> Path: ...

    async def lane_owner(self, session_key: str, channel: str = "telegram",
                         user_id: int = 0) -> int: ...


@runtime_checkable
class LaneFilesPort(Protocol):
    """The `files` module, for an attachment arriving from Telegram.

    Wave 3a: a downloaded photo or document is registered exactly like a browser
    upload, so it gets a row, an `/api/files/{id}` URL and — on the S3 backend —
    a place in the bucket, instead of being a loose file in `inbox/` that only
    this host knows about. Satisfied by `modules.files.application.FileService`
    as it stands; the ceiling stays Telegram's (`MAX_ATTACHMENT_BYTES`), passed
    per call so the web upload limit cannot silently refuse a chat attachment.
    """

    async def put_upload(self, session_key: str, filename: str, data: bytes,
                         media_type: str | None = None, *,
                         max_bytes: int | None = None) -> Any: ...

    async def resolve(self, record: Any) -> Path: ...


@runtime_checkable
class AttachmentDownloaderPort(Protocol):
    """What is downloadable on a Telegram message, and how to fetch it.

    A spec exposes `.original` (the name the user sees) and `.size` (from the
    Telegram object, so the size refusal costs no bytes).
    """

    def specs(self, message: Any) -> list[Any]: ...

    async def download_all(self, bot: Any, items: list[Any],
                           dest: Path) -> list[Attachment]: ...


__all__ = [
    "AclPort", "AskBrokerPort", "AttachmentDownloaderPort", "BotApiCall",
    "BotApiPort", "CatalogPort", "CatalogWriterPort", "ContentDecisionsPort",
    "LaneBindingPort",
    "LaneFilesPort", "LaneModelsPort", "LearnPromptPort", "ModelDefaultsPort",
    "ModelMenuPort", "SecretStorePort", "SenderPort",
    "ThreadTitleWriterPort", "ThreadTitlerPort", "TitleGeneratorPort",
    "TurnEventBusPort", "TurnRunnerPort", "VoicePort", "WorkspacePort",
]

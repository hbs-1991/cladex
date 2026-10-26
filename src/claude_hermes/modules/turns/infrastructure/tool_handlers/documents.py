"""`send_document`: deliver a file the agent wrote to the lane it is talking on.

Every check in here is a security check and moved verbatim: the containment test
against the agent's home, the `secrets.env` refusal, and the fact that the chat is
taken from the turn context and never from the model's arguments.

Since Wave 4b `agent_home` is the LANE OWNER's `agent_home` — the turn's cwd —
rather than the workspace root (`make_handlers(workspace=...)`, D7). A relative
`path` is joined onto it before `resolve()`, so the model can name a file the way
the seeded skills name one (`outbox/x.png`, cwd-relative); an absolute path must
resolve inside it. The boundary is `agent_home` and not the root because for the
deploy owner `root == CH_HOME`, and a group member on the owner's lane could name
`/data/users/7/…` by absolute path and have it sent (S-5). Resolve first, then
`is_relative_to`, so `..` and a planted symlink are judged by where they land.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from claude_hermes.modules.turns.application.ports import (
    DocumentDeliveryPort,
    FilePublisher,
)
from claude_hermes.integrations.claude_sdk.guards import is_absolute_path
from claude_hermes.modules.turns.domain import policies

log = structlog.get_logger(__name__)


def make_send_document(*, ctx, agent_home: Path | None,
                       deliver_document: DocumentDeliveryPort | None = None,
                       file_store: FilePublisher | None = None,
                       max_bytes: int | None = None):
    async def send_document(args: dict) -> dict:
        def reply(text: str) -> dict:
            return {"content": [{"type": "text", "text": text}]}

        limit = policies.MAX_DOCUMENT_BYTES if max_bytes is None else max_bytes()
        # Delivery is bound to THIS chat (ctx.chat_id), never a chat the model
        # names — the tool is a delivery channel, not a way to message arbitrary
        # people. The upload runs in-process here, so the bot token never reaches
        # the agent's Bash (that separation is the whole security model).
        # A web lane has no chat (chat_id 0): there the file store is the channel,
        # so the tool still works as long as one is wired.
        if ctx is None or (not ctx.chat_id and file_store is None):
            return reply("No chat to deliver to.")
        raw = args.get("path", "")
        if not raw:
            return reply("send_document needs a 'path'.")
        try:
            path = Path(raw)
            # Relative to the turn's cwd, never to the PROCESS cwd: the worker
            # runs from wherever it was started, and "outbox/x.png" means the
            # lane owner's outbox. With no boundary the process cwd is all there
            # is, which is what it always was.
            # The guards' notion of "absolute", not the host's: on Windows
            # `PurePath("/data/x").is_absolute()` is False, and the tool and the
            # read guard must not disagree about one string.
            if not is_absolute_path(raw) and agent_home is not None:
                path = agent_home / path
            path = path.resolve()
        except (OSError, ValueError):
            return reply(f"Bad path: {raw!r}.")
        boundary = agent_home.resolve() if agent_home is not None else None
        if boundary is not None and not path.is_relative_to(boundary):
            return reply(f"Refused: {path} is outside the agent's home ({boundary}).")
        if path.name == "secrets.env":
            return reply("Refused: secrets.env is not sendable.")
        if not path.is_file():
            return reply(f"No such file: {path}.")
        size = path.stat().st_size
        if size == 0:
            return reply(f"Refused: {path.name} is empty.")
        if size > limit:
            mb = size / (1024 * 1024)
            return reply(
                f"Refused: {path.name} is {mb:.1f}MB; Telegram bots cap uploads "
                "at 50MB. Split it or send a link instead."
            )
        caption = (args.get("caption") or "").strip() or None
        telegram_sent = False
        if ctx.chat_id:                       # a Telegram lane; web lanes have chat_id 0
            try:
                await deliver_document(ctx.chat_id, ctx.thread_id, str(path), caption)
                telegram_sent = True
            except Exception as e:
                log.exception("send_document delivery failed")
                return reply(f"Delivery failed: {e}")
        if file_store is not None and ctx.emit is not None:
            # Wave 3a: `publish` may upload to the bucket, so this can fail on a
            # network the Telegram send already survived. It stays inside its own
            # try — a delivered document must not be reported as an error because
            # the copy into the outbox did not land.
            try:
                # The LANE's owner, so the outbox copy lands in the tree the
                # turn is running in — the one whose `Read` the model is allowed.
                rec = await file_store.publish(
                    ctx.session_key, path,
                    owner_id=getattr(ctx, "owner_id", 0) or None)
                await ctx.emit("file", {**file_store.to_wire(rec),
                                        "telegram_sent": telegram_sent})
            except Exception:
                log.exception("send_document could not publish to the file store")
        where = "the chat" if telegram_sent else "the conversation"
        return reply(f"Sent {path.name} ({size} bytes) to {where}.")

    return send_document

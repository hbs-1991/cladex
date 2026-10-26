"""The `cron_manage` agent tool: handler + its definition.

Presentation, not application: this is one of the three channels onto
`SchedulerService` (the others are the Routines router and the ticker). It returns
the definition as a plain dict rather than an SDK-decorated callable so the turns
module's registry stays the single place that talks to `claude_agent_sdk.tool` and
keeps the registration order the prompt cache depends on.

The description and the schema below are prompt-cache surface: changing a byte
invalidates every cached prefix. They are copied verbatim from tools.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

CRON_TOOL_NAME = "cron_manage"

CRON_TOOL_DESCRIPTION = (
    "Manage scheduled jobs. action=create|list|pause|resume|delete|run_now. "
    "For create: normalize the user's wording into schedule='30m'/'every 2h', "
    "a 5-field cron ('0 8 * * *'), or an ISO timestamp for one-shots. The "
    "prompt must be SELF-CONTAINED: cron runs have no memory and no chat "
    "context. Results are delivered to this chat unless deliver_chat_id is set. "
    "timeout_seconds sets this job's own time limit (30-1800); omit it to "
    "inherit the global one — only set it for jobs that genuinely need longer, "
    "like a research sweep over many sources."
)

CRON_TOOL_SCHEMA = {"action": str, "name": str, "schedule": str, "prompt": str,
                    "deliver_chat_id": int, "job_id": int, "timeout_seconds": int}

# Not prompt-cache surface: a tool RESULT, sent only when the model asks for it.
MANUAL_REFUSAL = "On-demand routines are created in the Tasks tab."


async def handle(service, args: dict, ctx=None) -> str:
    """One `cron_manage` call, as plain text. The MCP envelope is put on by
    `build_cron_tool` below; the turns module's `SchedulerPort` wants the text."""
    action = args.get("action", "list")
    # The turn's LANE owner (D6): a routine created from a lane belongs to whoever
    # owns that lane, which is whose workspace each unattended run then happens in.
    # Not `ctx.user_id` — in a group lane the sender is not the owner. `None` is a
    # fork with no lane, which writes no owner and sees every routine, the pre-4a
    # behaviour.
    owner_id = getattr(ctx, "owner_id", 0) or None
    if action == "create":
        # An on-demand routine is the Tasks form's and the template's, never the
        # model's: the description does not advertise `manual` (prompt-cache
        # surface, not reopened for it), and a publisher-shaped routine the agent
        # made for itself is exactly what the owner's choice of publisher guards.
        if str(args.get("schedule", "")).strip().lower() == "manual":
            return MANUAL_REFUSAL
        chat = int(args.get("deliver_chat_id") or (ctx.chat_id if ctx else 0))
        if not chat:
            return "Rejected: no delivery chat available."
        name = args.get("name", "job")
        # Shared with the web Routines router: one validate-and-insert path.
        jid, err = await service.create_job(
            name=name,
            schedule=args.get("schedule", ""), prompt=args.get("prompt", ""),
            deliver_chat_id=chat, origin_session_key=ctx.session_key if ctx else "",
            timeout_seconds=args.get("timeout_seconds"),
            owner_id=owner_id,
        )
        if err:
            return err
        job = await service.get(jid)
        limit = f", limit {job['timeout_seconds']}s" if job["timeout_seconds"] else ""
        return f"Created job #{jid} '{name}', next run {job['next_run_at']}{limit}."
    if action == "list":
        jobs = await service.list_jobs(owner_id)
        if not jobs:
            return "No jobs."
        return "\n".join(
            f"#{j['id']} {j['name']} [{j['state']}] {j['kind']} {j['value']} "
            f"→ {j['next_run_at']}, last: {j['last_status'] or '—'}"
            + (f", limit {j['timeout_seconds']}s" if j["timeout_seconds"] else "")
            for j in jobs
        )
    jid = int(args.get("job_id", 0))
    if action in ("pause", "resume", "delete", "run_now"):
        # Resolve the id against the lane's owner FIRST: without it a model that
        # guessed `job_id=3` would pause, or delete, somebody else's routine. The
        # miss is phrased as "no such job", never "not yours".
        if owner_id is not None and await service.get(jid, owner_id) is None:
            return f"No such job #{jid}."
    if action == "pause":
        await service.set_state(jid, "paused")
        return f"Paused #{jid}."
    if action == "resume":
        await service.set_state(jid, "active")
        return f"Resumed #{jid}."
    if action == "delete":
        await service.delete(jid)
        return f"Deleted #{jid}."
    if action == "run_now":
        await service.run_now(jid, datetime.now(timezone.utc))
        return f"Job #{jid} queued for the next tick."
    return f"Unknown action '{action}'."


class CronToolPort:
    """The turns module's `SchedulerPort`: `handle(args, *, ctx) -> str`.

    `ctx` arrives per call rather than per composition — one service serves every
    lane, and the chat a job is created for comes from the turn asking.
    """

    def __init__(self, service):
        self._service = service

    async def handle(self, args: dict, *, ctx=None) -> str:
        return await handle(self._service, args, ctx)


def build_cron_tool(service, ctx=None):
    """`(handler, definition)` for one turn. `ctx` carries the turn's session key and
    chat id; None in a fork that has neither (the tool is then not registered)."""

    async def cron_manage(args: dict) -> dict:
        text = await handle(service, args, ctx)
        return {"content": [{"type": "text", "text": text}]}

    definition = {"name": CRON_TOOL_NAME, "description": CRON_TOOL_DESCRIPTION,
                  "schema": CRON_TOOL_SCHEMA}
    return cron_manage, definition

"""The four tools whose BODY belongs to another module.

`turns` owns the tool surface (the agent reaches the scheduler, the skill library,
the extension installer and the content plan through MCP tools, and the
descriptions are part of the cached prompt prefix), but not the behaviour. Each
handler here is the MCP envelope and nothing else; the reply text comes from the
port — and for `content_plan` the whole envelope does, because the content module
answers routine runs with the same body.
"""
from __future__ import annotations

from claude_hermes.modules.turns.application.ports import (
    ContentPlanPort,
    ExtensionAdminPort,
    SchedulerPort,
    SkillAdminPort,
)


def _reply(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def make_cron_manage(scheduler: SchedulerPort, ctx):
    async def cron_manage(args: dict) -> dict:
        return _reply(await scheduler.handle(args, ctx=ctx))

    return cron_manage


def make_skill_admin(skills: SkillAdminPort, ctx=None):
    # `ctx` since Wave 4b: the port picks the LANE OWNER's library off
    # `ctx.owner_id`, so a private lane administers its own tree and never the
    # deploy owner's. `None` is a handler with no turn (the owner's).
    async def skill_admin(args: dict) -> dict:
        return _reply(await skills.handle(args, ctx=ctx))

    return skill_admin


def make_ext_manage(ext_admin: ExtensionAdminPort, ctx):
    async def ext_manage(args: dict) -> dict:
        text, resets_lane = await ext_admin.handle(args, ctx=ctx)
        if resets_lane and ctx is not None:
            # An extension changed the toolset; tool schemas are part of the
            # cached prefix, so this lane's session must restart after the turn.
            ctx.flags.reset_lane = True
        return _reply(text)

    return ext_manage


def make_content_plan(content: ContentPlanPort, ctx):
    # Everything the body needs from the turn is read HERE, off the context, and
    # passed as plain values: the port also serves routine runs, which have no
    # `TurnContext`. `owner_id` is the LANE's owner — decided once by the runner,
    # never the sender — and `0` (a fork, or no context) is what the port refuses.
    # `origin` is always "chat": only a routine run says `cron:<job_id>`, and the
    # publisher-only actions check exactly that, so a chat turn cannot claim.
    owner_id = int(getattr(ctx, "owner_id", 0) or 0)
    thread_id = getattr(ctx, "session_key", None) or None

    async def content_plan(args: dict) -> dict:
        return await content.handle(args, owner_id=owner_id, origin="chat",
                                    thread_id=thread_id)

    return content_plan

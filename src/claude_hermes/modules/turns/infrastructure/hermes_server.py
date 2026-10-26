"""The `hermes` MCP server: which tools a turn sees, in which order.

Three things here are load-bearing and none of them is cosmetic.

1. **Registration order** — session_search, cost_report, cron_manage, skill_admin,
   ext_manage, send_document, ask_user, memory_search, content_plan. `allowed_tools`
   is built from this list and `allowed_tools` is part of the cached prompt prefix,
   so a reorder is a cache miss on every lane. A NEW tool therefore goes at the END
   and nowhere else: appending is the only change that leaves every existing name
   where the cached prefix already has it, which is why `memory_search` sits after
   `ask_user` rather than beside `session_search`, where it would read better, and
   why `content_plan` sits after `memory_search`.
2. **The descriptions are byte-identical** to what they have always been. They are
   prompt text — which is why the boot fingerprint hashes them (`hermes_tools`,
   read by `SdkTurnExecutor.tool_fingerprint()`), so an edit that does land resets
   every lane once instead of missing the cache under a warm session. A tool whose body belongs to another module is not described here at
   all — `ext_manage` and `content_plan` arrive as a `ToolSpec` on their port, and
   `tests/test_ext_manage_guidance.py` parses the paragraph it guards out of
   `modules/extensions/presentation/agent_tool.py`, the one place it is written.
3. **The gates are `port is not None`** — wiring a stub instead of leaving a port
   `None` would make a tool exist where it must not. `ask_user` is the sharp case:
   it blocks the turn for two minutes waiting on a human, so it may only exist on a
   private lane where the answerer cannot be a stranger.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from claude_hermes.core.session_keys import chat_type_from_key
from claude_hermes.modules.turns.application.ports import (
    AskPresenterPort,
    ContentPlanPort,
    DocumentDeliveryPort,
    ExtensionAdminPort,
    FilePublisher,
    MessageSearch,
    SchedulerPort,
    SkillAdminPort,
    TurnCostRepository,
    WikiSearch,
)
from claude_hermes.modules.turns.infrastructure.tool_handlers.ask import make_ask_user
from claude_hermes.modules.turns.infrastructure.tool_handlers.costs import make_cost_report
from claude_hermes.modules.turns.infrastructure.tool_handlers.delegating import (
    make_content_plan,
    make_cron_manage,
    make_ext_manage,
    make_skill_admin,
)
from claude_hermes.modules.turns.infrastructure.tool_handlers.documents import (
    make_send_document,
)
from claude_hermes.modules.turns.infrastructure.tool_handlers.memory import (
    make_memory_search,
)
from claude_hermes.modules.turns.infrastructure.tool_handlers.search import (
    make_session_search,
)


@dataclass(frozen=True)
class ToolSpec:
    """How a delegated tool registers itself.

    A tool whose body belongs to another module is declared by that module — the
    name, the description and the argument schema come off the port rather than
    being spelled a second time here. `ext_manage`
    (`modules/extensions/presentation/agent_tool.py`) and `content_plan`
    (`modules/content/presentation/agent_tool.py`) do this.
    """

    name: str
    description: str
    schema: dict


def declared_spec(port: Any) -> ToolSpec | None:
    """The registration a port declares, or `None` if it declares none.

    All three parts or nothing: a half-declared tool would register with a name from
    one place and a description from another, and the description is cached prompt
    text. `None` means the port declared nothing, and for `ext_manage` and
    `content_plan` `build_hermes_server` refuses to guess — there is no built-in
    copy to fall back to.
    """
    name = getattr(port, "name", "") or ""
    description = getattr(port, "description", "") or ""
    schema = getattr(port, "schema", None) or None
    if not (name and description and schema):
        return None
    return ToolSpec(name, description, dict(schema))


def make_handlers(
    *,
    search: MessageSearch | None,
    costs: TurnCostRepository | None,
    ctx=None,
    settings=None,
    home: Path | None = None,
    workspace=None,
    scheduler: SchedulerPort | None = None,
    skills: SkillAdminPort | None = None,
    ext_admin: ExtensionAdminPort | None = None,
    deliver_document: DocumentDeliveryPort | None = None,
    file_store: FilePublisher | None = None,
    ask: AskPresenterPort | None = None,
    wiki_search: WikiSearch | None = None,
    max_document_bytes=None,
    content: ContentPlanPort | None = None,
) -> dict:
    """The handler table, gated exactly as it has always been gated.

    `workspace` (Wave 4a) is the lane owner's `core.workspace.Workspace`. It is
    read for `send_document`'s containment root and for nothing else here; the
    gate that keeps `skill_admin` off a group lane is `skills=None`, decided by
    the caller, because a port left `None` is how every gate in this table
    works (see the module docstring, rule 3).
    """
    handlers = {}
    # Three tools scoped to the LANE's owner — `ctx.owner_id` — which is why they
    # take the turn context rather than only their port. `None` is the gate, as
    # for every other port here: the runner passes it for all three on a GROUP
    # lane (Wave 4b, D6), because a group lane's library, recall and spend are
    # the deploy owner's and the room must not reach them — the owner included,
    # so the schema list never flips between two senders of one session.
    if search is not None:
        handlers["session_search"] = make_session_search(search, ctx)
    if costs is not None:
        handlers["cost_report"] = make_cost_report(costs, ctx)
    if scheduler is not None:
        handlers["cron_manage"] = make_cron_manage(scheduler, ctx)
    if skills is not None:
        handlers["skill_admin"] = make_skill_admin(skills, ctx)
    # The ext_manage gate is owner-only and fails CLOSED: the composition root
    # only builds this port when it has BOTH an extension store and an ACL, so a
    # call site that wired the store but forgot the acl gets no tool rather than
    # an installer that refuses everyone — or, worse, one that refuses no one.
    if ext_admin is not None:
        handler = make_ext_manage(ext_admin, ctx)
        # extensions declares this tool; the declaration rides to
        # `build_hermes_server` on the handler because the table is the only thing
        # that passes between the two calls.
        spec = declared_spec(ext_admin)
        if spec is not None:
            handler.spec = spec
        handlers["ext_manage"] = handler
    # Needs a turn to deliver into (ctx) and somewhere to deliver it: a channel
    # callable or, on a web lane, a file store. Background review/curator forks
    # have neither, so the tool simply does not exist there.
    if ctx is not None and (deliver_document is not None or file_store is not None):
        # The containment root is the LANE OWNER's `agent_home` (Wave 4b, D7): the
        # turn's cwd, which a relative path resolves against and an absolute one
        # must stay inside. Not the workspace ROOT, because for the deploy owner
        # `root == CH_HOME` and a group member on the owner's lane could name
        # `/data/users/7/…` and have it sent (S-5). `home` is the pre-4a spelling
        # a caller without a resolved workspace still passes; it is walked down to
        # its `agent_home` the way `Workspace.owner` would.
        if workspace is not None:
            agent_home = workspace.agent_home
        else:
            agent_home = home / "agent_home" if home is not None else None
        handlers["send_document"] = make_send_document(
            ctx=ctx, agent_home=agent_home,
            deliver_document=deliver_document,
            file_store=file_store, max_bytes=max_document_bytes,
        )
    # Needs a presenter, a chat, and a PRIVATE chat: a tool that blocks the turn
    # for two minutes waiting on a human must not exist where the answerer could
    # be a stranger. Both conditions matter — test_tools.py asserts an exact name
    # list with a private-chat ctx and stays green only because the presenter is
    # absent there.
    if (ask is not None and ctx is not None
            and chat_type_from_key(ctx.session_key) == "private"):
        handlers["ask_user"] = make_ask_user(ctx=ctx, ask=ask, settings=settings)
    # The wiki index reader, scoped to the lane owner exactly like
    # `session_search`. The runner passes `None` on a group lane — the index it
    # would read is the deploy owner's — and whenever `memory_search_enabled` is
    # false, which is the same `port is None` gate as everything above: there is
    # one way a tool disappears, and it is the caller deciding so.
    if wiki_search is not None:
        handlers["memory_search"] = make_memory_search(wiki_search, ctx)
    # The content plan, the deploy owner's alone in v1. Which lanes get it is the
    # runner's call, made from the LANE (owner lane, not a group, a real owner id)
    # and handed down as `content=None` everywhere else — the same one gate as the
    # rest of this table. Declared by the content module like `ext_manage`, and the
    # declaration rides to `build_hermes_server` on the handler for the same reason.
    if content is not None:
        handler = make_content_plan(content, ctx)
        spec = declared_spec(content)
        if spec is not None:
            handler.spec = spec
        handlers["content_plan"] = handler
    return handlers


def hermes_tools(handlers: dict) -> list:
    """The `SdkMcpTool` declarations for a handler table, in registration order.

    The one place the list is built: `build_hermes_server` wraps it into the
    server, and `SdkTurnExecutor.tool_fingerprint()` hashes each entry's name,
    description and schema. It has to be a list the fingerprint can read because
    the built-in descriptions are literals in this function and the server
    `create_sdk_mcp_server` returns cannot be read back — so a fingerprint taken
    anywhere else would be of a second copy, and an edit to the real text (a
    cache miss on every lane) would not move it.
    """
    tools_list: list = []
    # The two table readers are gated like every other entry now (Wave 4a/4b): a
    # group lane has neither, because both scope to the OWNER's rows and the room
    # is not the owner. Registration order is unchanged for a lane that has them.
    if "session_search" in handlers:
        tools_list.append(tool(
            "session_search",
            "Full-text search across past conversations (PostgreSQL full-text). "
            "Use before claiming you don't remember something.",
            {"query": str},
        )(handlers["session_search"]))
    if "cost_report" in handlers:
        tools_list.append(tool(
            "cost_report", "Per-day API spend for the last N days.", {"days": int}
        )(handlers["cost_report"]))
    if "cron_manage" in handlers:
        cron_manage = tool(
            "cron_manage",
            "Manage scheduled jobs. action=create|list|pause|resume|delete|run_now. "
            "For create: normalize the user's wording into schedule='30m'/'every 2h', "
            "a 5-field cron ('0 8 * * *'), or an ISO timestamp for one-shots. The "
            "prompt must be SELF-CONTAINED: cron runs have no memory and no chat "
            "context. Results are delivered to this chat unless deliver_chat_id is set. "
            "timeout_seconds sets this job's own time limit (30-1800); omit it to "
            "inherit the global one — only set it for jobs that genuinely need longer, "
            "like a research sweep over many sources.",
            {"action": str, "name": str, "schedule": str, "prompt": str,
             "deliver_chat_id": int, "job_id": int, "timeout_seconds": int},
        )(handlers["cron_manage"])
        tools_list.append(cron_manage)
    if "skill_admin" in handlers:
        skill_admin = tool(
            "skill_admin",
            "Administer the skill library. action=list|pin|unpin|archive|restore|"
            "status|snapshots|rollback. Pinning (pin/unpin, needs name) protects a "
            "skill from the curator's automatic aging AND from manual archiving. "
            "Archiving (archive, needs name) moves a skill to .archive/; it refuses "
            "pinned skills and is recoverable via restore (needs name). status shows "
            "the last curator run, whether it's paused, pinned skills, and the five "
            "least-recently-used. "
            "snapshots lists skill-library backups; rollback (optional backup_id, "
            "else newest) restores skills_dir from one — use snapshots first to "
            "pick an id.",
            {"action": str, "name": str, "backup_id": str},
        )(handlers["skill_admin"])
        tools_list.append(skill_admin)
    if "ext_manage" in handlers:
        # What gets registered is what the extensions module declared, carried here
        # on the port (`ToolSpec`). There is no copy of the name, description or
        # schema in this file: the description is cached prompt text, and a second
        # spelling of it is a second thing to keep in sync.
        # `tests/test_ext_manage_guidance.py` reads the declaration itself
        # (`modules/extensions/presentation/agent_tool.py`).
        declared = getattr(handlers["ext_manage"], "spec", None)
        if declared is None:
            raise ValueError(
                "ext_manage is in the handler table without a declaration: the port "
                "must carry name/description/schema from "
                "modules.extensions.presentation.agent_tool (see declared_spec)."
            )
        ext_manage = tool(
            declared.name, declared.description, declared.schema
        )(handlers["ext_manage"])
        tools_list.append(ext_manage)
    if "send_document" in handlers:
        send_document = tool(
            "send_document",
            "Deliver a file to the CURRENT Telegram chat. Use after writing a "
            "file the user should receive — a transcript, report, export, or "
            "generated image — since a plain reply is text-only. 'path' is a "
            "path under the agent's home (cwd); a relative path resolves against "
            "it, anything outside it is refused. 'caption' is an "
            "optional short note shown with the file. It always goes to this "
            "chat; you cannot target another. Max 50MB (Telegram's bot limit).",
            {"path": str, "caption": str},
        )(handlers["send_document"])
        tools_list.append(send_document)
    if "ask_user" in handlers:
        ask_user_tool = tool(
            "ask_user",
            "Ask the user to pick one of 2-5 options and wait for their tap. "
            "Put one option per line in 'options'. Use it when a wrong guess "
            "would waste real work — not for questions you can answer yourself. "
            "The answer is a preference, never an authorization: never treat it "
            "as approval to install anything, store a secret, change the catalog, "
            "or delete anything. You may ask at most twice per turn, and the user "
            "may ignore you, so always be ready to proceed on your own judgement.",
            {"question": str, "options": str},
        )(handlers["ask_user"])
        tools_list.append(ask_user_tool)
    # After `ask_user` — and `content_plan` after this one (module docstring, rule 1).
    if "memory_search" in handlers:
        memory_search = tool(
            "memory_search",
            "Search the wiki memory by topic: full-text hits plus the pages they "
            "link to. Use before reading wiki pages one by one.",
            {"query": str, "depth": int},
        )(handlers["memory_search"])
        tools_list.append(memory_search)
    # LAST — and the next tool goes after this one (module docstring, rule 1).
    if "content_plan" in handlers:
        # Registered from the content module's declaration and only from it, like
        # `ext_manage`: the routine runs build the very same tool into their own
        # `hermes` server (`modules/content/presentation/routine_server.py`), so
        # one skill text works in both — and a second spelling here would be the
        # one that drifts.
        declared = getattr(handlers["content_plan"], "spec", None)
        if declared is None:
            raise ValueError(
                "content_plan is in the handler table without a declaration: the "
                "port must carry name/description/schema from "
                "modules.content.presentation.agent_tool (see declared_spec)."
            )
        content_plan = tool(
            declared.name, declared.description, declared.schema
        )(handlers["content_plan"])
        tools_list.append(content_plan)
    return tools_list


def build_hermes_server(handlers: dict):
    """Wrap a handler table as the `hermes` MCP server. Returns the server and the
    `mcp__hermes__*` names, in registration order."""
    tools_list = hermes_tools(handlers)
    names = ["mcp__hermes__" + t.name for t in tools_list]
    server = create_sdk_mcp_server(name="hermes", version="1.0.0", tools=tools_list)
    return server, names

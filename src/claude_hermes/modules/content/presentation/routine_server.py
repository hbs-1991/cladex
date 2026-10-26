"""`content_plan` for an unattended routine run: the scheduler's `RoutineTools` port.

A routine run has no `hermes` server of its own — its toolset is the executor's
fixed list plus the extensions composed for scope `cron` — so the content module
builds one: an in-process `create_sdk_mcp_server(name="hermes")` holding exactly the
one tool. Same server name, same tool name as in chat (`mcp__hermes__content_plan`),
so one skill text works in both places. The executor merges this server AFTER the
composed extension servers, so an extension that happens to be named `hermes`
cannot shadow it.

Presentation, next to `agent_tool.py`, and not infrastructure: the two are
independent siblings under the layers contract, and this file needs the tool's
declaration as much as it needs `claude_agent_sdk`.

Owner-only in v1: `({}, [])` for anybody but the deploy owner, and for an owner id
nobody resolved (`<= 0`). The run's `origin` is `cron:<job_id>` from the port's own
argument — it is what becomes `created_via`, and it is what lets the configured
publisher routine (and no other) use `due`/`claim`/`published`/`failed`.
"""
from __future__ import annotations

from claude_agent_sdk import create_sdk_mcp_server, tool

from .agent_tool import ContentPlanTool

SERVER_NAME = "hermes"


class ContentRoutineTools:
    """`for_owner(owner_id, *, is_deploy_owner, job_id) -> (mcp_servers, names)`.

    Built over the `ContentPlanTool` the chat lanes are handed, so the two channels
    share one object and one declaration.
    """

    def __init__(self, tool_port: ContentPlanTool):
        self._tool = tool_port

    def for_owner(self, owner_id: int, *, is_deploy_owner: bool,
                  job_id: int) -> tuple[dict, list[str]]:
        if int(owner_id or 0) <= 0 or not is_deploy_owner:
            return {}, []
        port = self._tool
        origin = f"cron:{int(job_id)}"

        async def content_plan(args: dict) -> dict:
            # A routine run has no lane to link back to: `thread_id` stays None.
            return await port.handle(args, owner_id=int(owner_id), origin=origin,
                                     thread_id=None)

        registered = tool(port.name, port.description, dict(port.schema))(content_plan)
        server = create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0",
                                       tools=[registered])
        return {SERVER_NAME: server}, [f"mcp__{SERVER_NAME}__{port.name}"]

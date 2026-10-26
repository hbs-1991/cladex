"""The SDK loop: options in, a stream of messages out, turn events on the way.

This is the only file in `turns` that imports `claude_agent_sdk`. `query`,
`build_hermes_server` and `hermes_tools` are read off this module at call time on
purpose — that is the seam every runner test patches, and it only stays patchable
while the names are module-level here rather than imported into the call site.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog
from claude_agent_sdk import (
    AssistantMessage,
    HookMatcher,
    ProcessError,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from claude_hermes.core.events import TurnStats
from claude_hermes.core.model_catalog import cli_id, lookup
from claude_hermes.core.session_keys import is_group_lane
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import (
    PATH_TOOLS,
    WRITE_TOOLS,
    bash_guard,
    path_guard_factory,
    readonly_guard_factory,
)
from claude_hermes.integrations.claude_sdk.options import base_options, workspace_env
from claude_hermes.modules.turns.application.observation import observe_block
from claude_hermes.modules.turns.application.ports import (
    ContentPlanPort,
    ExtensionComposePort,
    TurnCostRepository,
    TurnHooksProvider,
    WikiSearch,
)
from claude_hermes.modules.turns.domain.exceptions import CliCallFailed, ModelRejected
from claude_hermes.modules.turns.domain.policies import (
    _SDK_ERROR_RESULT_PREFIX,
    builtin_tools,
    denied_tools,
)
from claude_hermes.modules.turns.domain.value_objects import (
    ComposedTools,
    TurnContext,
    _compose_text,
)
from claude_hermes.modules.turns.infrastructure.hermes_server import (
    build_hermes_server,
    hermes_tools,
    make_handlers,
)

log = structlog.get_logger(__name__)


def _is_cli_death(e: Exception | None) -> bool:
    """Did the CLI die, so that the stored session may be stale?

    Walks `__cause__` because `execute` re-raises a failed call as
    `CliCallFailed` when the errored `AssistantMessage` said more than the SDK's
    exception does: the wrapper carries a better sentence for `turn.end.error`,
    and it must not change which failures take the reset-and-retry path.
    """
    seen = 0
    while e is not None and seen < 10:       # a bounded walk: a cycle must not hang a turn
        if isinstance(e, ProcessError) or str(e).startswith(_SDK_ERROR_RESULT_PREFIX):
            return True
        e = e.__cause__
        seen += 1
    return False


# The CLI's stand-in when no model ever answered — it names the local process,
# not a model, so it is never "the model that ran" (recorded live beside the
# rejection signature below).
_SYNTHETIC_MODEL = "<synthetic>"

# What the API says when it refuses a model id. Recorded live (SDK 0.2.158 / CLI
# 2.1.280): an `AssistantMessage` with this `error` arrives first, then the call
# raises `ResultError` with `terminal_reason == "api_error"` and a 404. Either
# half is enough — but only when no tool ran in this call, because after a tool
# call a 400/404 is an ordinary API failure of a model that was working a moment
# ago, and re-running the whole turn on another model would repeat its side
# effects.
_MODEL_NOT_FOUND = "model_not_found"
_REJECTION_STATUSES = (400, 404)


def _is_rejection(error: str | None, exc: Exception | None) -> bool:
    """The rejection signature, read off facts the CLI reported — never off the
    exception's text, which can be just the result subtype."""
    if error == _MODEL_NOT_FOUND:
        return True
    return (getattr(exc, "terminal_reason", None) == "api_error"
            and getattr(exc, "api_error_status", None) in _REJECTION_STATUSES)


def _usage_wire(model: str | None, usage: dict | None, model_usage: dict | None) -> dict | None:
    """The `turn.end` usage: what the model's context window holds after this turn.

    The SDK's `ResultMessage.usage` / `model_usage` are SUMS over every API call
    of the turn (each tool round-trip re-sends the whole prompt), so they say
    what the turn cost, not how full the window is. Occupancy is the LAST
    main-loop call's prompt — `input + cache_read + cache_creation` of the last
    `AssistantMessage.usage`, the per-step figure the SDK documents as accurate
    for input and cache tokens. The window size and the turn's output tokens
    (a placeholder on the per-step message) come from `model_usage[model]`.
    Keyed by model, snake_case, None when the SDK reported nothing.

    `model` is what the API answered as (`claude-opus-5`), while the CLI keys
    `model_usage` by the configured id — `claude-opus-5[1m]` when the config asks
    for the 1M window, which the CLI gives Opus 5 only with that suffix. So the
    suffixed entry is the same model; an exact-key-only lookup lost the window
    and the meter vanished. The wire stays keyed by the API name."""
    if not usage or not model:
        return None
    by_model = model_usage or {}
    per_model = by_model.get(model) or by_model.get(f"{model}[1m]") or {}
    tokens = {k: int(usage.get(k) or 0) for k in
              ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    return {model: {
        "context_window": per_model.get("contextWindow"),
        **tokens,
        "output_tokens": int(per_model.get("outputTokens") or usage.get("output_tokens") or 0),
    }}


def _schema_wire(value):
    """A tool's `input_schema` as plain JSON, for the boot fingerprint.

    The built-in tools declare their arguments as `{"query": str}` — Python type
    objects, which `json.dumps` refuses and whose `repr` is not a promise — so a
    type stands in as its name. A declared tool's schema is JSON already and
    passes through. Anything else falls back to `repr`, which for the `Annotated`
    forms the SDK accepts is stable across processes.
    """
    if isinstance(value, type):
        return value.__name__
    if isinstance(value, dict):
        return {str(k): _schema_wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_schema_wire(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def _clip_input(value, limit: int, depth: int = 6):
    """A tool call's arguments with every long string clipped, structure intact.

    `tool.result` has been clipped since the web channel existed; the call that
    produced it was not, and a tool's arguments are the bigger half. A `Write` puts
    the whole file in `input`, an `Edit` puts two copies of a hunk there, and every
    one of them is stored in `turn_events` forever and shipped again on every open
    of that thread — nothing prunes the table, so a lane's history only grows.

    Per string rather than over the serialized whole: the browser renders the
    arguments as a named structure (`ToolRow`, and `fold.ts` stringifies them for
    `argsText`), and the Telegram presenter reads `input["file_path"]` and friends
    to write its status line. Clipping the JSON text would leave both with a
    truncated document they cannot parse, so the keys survive and only the values
    that are too long lose their tail — which is what a reader skims anyway.

    `depth` bounds the walk: a tool's schema is data from outside this process, and
    a deeply nested argument must not turn one event into a recursion error.
    """
    if isinstance(value, str):
        return _clip(value, limit)
    if depth <= 0:
        return value
    if isinstance(value, dict):
        return {k: _clip_input(v, limit, depth - 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip_input(v, limit, depth - 1) for v in value]
    return value


def _result_text(content) -> str:
    """ToolResultBlock.content is a str, a list of content dicts, or None."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif isinstance(item, dict) and item.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


def _build_prompt(user_text: str, attachments):
    """A bare string when nothing is attached — the shape every call site has
    always used, so the cached prefix is untouched and text/voice turns are
    byte-identical to before.

    Otherwise a FRESH async iterator on every call. _execute runs a second time
    on the resume-retry path (see TurnService._run_turn_body); building this once
    above that retry would hand the second attempt an exhausted generator.
    """
    text = _compose_text(user_text, attachments)
    if not attachments:
        return text

    # Images before text: the documented ordering for vision requests. Each is
    # also named by path in the text block, so the agent can re-open or process
    # the file rather than only look at it.
    blocks: list[dict] = [
        {"type": "image", "source": {
            "type": "base64",
            "media_type": a.media_type,
            "data": base64.standard_b64encode(a.path.read_bytes()).decode(),
        }}
        for a in attachments if a.is_image
    ]
    blocks.append({"type": "text", "text": text})

    async def _stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": blocks},
            "parent_tool_use_id": None,
            "session_id": "default",
        }

    return _stream()


class SdkTurnExecutor:
    """Builds the options for one turn, runs the SDK query, and turns the message
    stream into turn events.

    Every collaborator is optional except the ones a turn cannot run without: a
    port left `None` removes the tool it backs (see `hermes_server.make_handlers`).
    """

    def __init__(
        self,
        *,
        settings,
        home: Path,
        search,
        costs: TurnCostRepository,
        hooks: TurnHooksProvider | None = None,
        compose: ExtensionComposePort | None = None,
        catalog_root: Path | None = None,
        scheduler=None,
        skills=None,
        ext_admin=None,
        deliver_document=None,
        file_store=None,
        ask=None,
        wiki_search: WikiSearch | None = None,
        max_document_bytes=None,
        content: ContentPlanPort | None = None,
    ):
        self._settings = settings
        self._home = home
        self._search = search
        self._costs = costs
        self._hooks = hooks
        self._compose = compose
        # The reviewed catalog (`CH_CATALOG`, mounted `:ro`). Read-only code, not
        # state, and every lane may read it — a skill that looks up a manifest
        # works the same for a non-owner. Passed in rather than imported: a module
        # may not reach another module, and the composition root already knows.
        self._catalog_root = catalog_root
        self._scheduler = scheduler
        self._skills = skills
        self._ext_admin = ext_admin
        self._deliver_document = deliver_document
        self._file_store = file_store
        self._ask = ask
        # memory's wiki index, read-only. `None` — or a group lane, or the flag
        # off — is how `memory_search` does not exist at all.
        self._wiki_search = wiki_search
        self._max_document_bytes = max_document_bytes
        # The content plan (`content_enabled`), the deploy owner's alone in v1.
        # `None` — the flag off — is how `content_plan` does not exist anywhere;
        # which lanes get it when it is wired is `_content_for`'s call.
        self._content = content

    def is_cli_death(self, error: Exception) -> bool:
        return _is_cli_death(error)

    def tool_names(self) -> list[str]:
        """The `hermes` MCP tool names an OWNER'S PRIVATE lane gets, in
        registration order — the widest list this process can publish.

        The boot fingerprint covers this list (`tool_fingerprint` hashes it with
        each tool's description and schema): tool schemas are part of
        the cached prompt prefix while `system_append` is frozen on the session
        row, so a deploy that adds or removes a tool (or flips a gate such as
        `memory_search_enabled`) must reset every lane rather than let the new
        toolset appear mid-session (P1). Built exactly as `execute` builds it
        for an owner's private lane: `send_document` and `ask_user` exist only
        with a private-chat context, so a synthetic one stands in — every
        `make_*` factory only closes over it, nothing touches disk or the
        network until a tool is called.
        """
        return list(build_hermes_server(self._owner_private_handlers())[1])

    def tool_fingerprint(self) -> str:
        """sha256 over the name, description and argument schema of every
        `hermes` tool an OWNER'S PRIVATE lane gets, in registration order.

        What the composition root stores at boot (meta `hermes_tools`). Names
        alone were not enough: a description is prompt text in the cached prefix
        too, so rewording one — `content_plan` growing a sentence, or a BUILT-IN
        such as `session_search` — would otherwise miss the cache under every
        warm session without the one clean reset a toolset change gets (P1).
        Read from `hermes_tools`, the list `build_hermes_server` wraps, because
        the built-in descriptions are literals there and the server object cannot
        be read back. Order is kept, not sorted: a reorder is a cache miss too.
        """
        declared = [
            {"name": t.name, "description": t.description,
             "input_schema": _schema_wire(t.input_schema)}
            for t in hermes_tools(self._owner_private_handlers())
        ]
        wire = json.dumps(declared, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))
        return hashlib.sha256(wire.encode("utf-8")).hexdigest()

    def _owner_private_handlers(self) -> dict:
        """The handler table `execute` would build for the deploy owner's private
        lane — the probe behind both `tool_names` and `tool_fingerprint`, so the
        two can never describe different toolsets."""
        probe = TurnContext(session_key="tg:private:0", chat_id=0,
                            workspace=Workspace.owner(self._home))
        return make_handlers(
            search=self._search, costs=self._costs, ctx=probe,
            settings=self._settings, home=self._home,
            workspace=Workspace.owner(self._home), scheduler=self._scheduler,
            skills=self._skills, ext_admin=self._ext_admin,
            deliver_document=self._deliver_document, file_store=self._file_store,
            ask=self._ask,
            wiki_search=None if not getattr(self._settings, "memory_search_enabled", True)
            else self._wiki_search,
            max_document_bytes=self._max_document_bytes,
            # The probe is the owner's private lane, which is exactly where the
            # content plan exists — so flipping `content_enabled` is a name change
            # here, and costs every lane one session reset at boot, as it must.
            content=self._content,
        )

    def _content_for(self, ctx, session_key: str) -> ContentPlanPort | None:
        """The content plan port for this lane, or `None` for no tool.

        Three conditions, every one of them a property of the LANE and none of the
        sender, so the MCP schema list never flips between two turns of one
        session (P1):

        * the lane is the DEPLOY owner's — asked as `is_owner_lane is True`, not
          through the `getattr(ctx, "is_owner_lane", True)` default the builtin
          toolset uses: a context that does not say it is the owner's gets no
          content plan, because the plan is the owner's publishing queue;
        * it is not a group lane — a group lane belongs to the deploy owner too,
          and a room must not edit the owner's plan, the owner included (the
          same rule as `skill_admin`);
        * it has a real owner id — `0` is a fork with no lane, which the port
          would refuse on every call anyway, so the tool is not offered at all.
        """
        if self._content is None:
            return None
        if getattr(ctx, "is_owner_lane", False) is not True:
            return None
        if is_group_lane(session_key):
            return None
        if (getattr(ctx, "owner_id", 0) or 0) <= 0:
            return None
        return self._content

    def _workspace(self, ctx) -> Workspace:
        """Whose tree this turn runs in.

        `TurnRunService` resolved it from the row and it arrived on the context.
        The fallback is the DEPLOY OWNER's, which is what every lane was before
        Wave 4a.
        """
        workspace = getattr(ctx, "workspace", None)
        return workspace if workspace is not None else Workspace.owner(self._home)

    def _allowed_roots(self, ws: Workspace) -> list[Path]:
        """Everything a turn's file tools may touch, and nothing else (D6).

        Per user: `agent_home` (the cwd, the wiki, inbox/outbox, skills, agents)
        `tmp/` (which is also what `TMPDIR` points at, so the shared `/tmp` never
        holds one lane's scratch where another can read it) and `logs/`. Deploy
        wide: the plugin clones — moved out of `agent_home` in this wave precisely
        so a non-owner can load a plugin without their guard admitting the OWNER's
        whole tree (D8) — and the read-only catalog.

        `logs_dir` is not in the plan's list and is here deliberately: it is per
        user like the rest, and the curator writes its reports under the owner's
        `logs/curator/`, so leaving it out would deny the owner a `Read` that
        works today for nothing in return.

        `shared_wiki_root` is the one path outside a non-owner's tree they may
        read: the deploy owner's `wiki/shared/`, the household namespace. The
        OWNER gets no extra entry, because for them it already sits under
        `agent_home` — and a write fence goes with it for everybody else
        (`_build_hooks`), since a read root without one would hand every admitted
        user an editable copy of the owner's pages.
        """
        roots = [ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir]
        if not getattr(ws, "is_owner", True):
            roots.append(ws.shared_wiki_root)
        if self._catalog_root is not None:
            roots.append(self._catalog_root)
        return roots

    def _build_hooks(self, session_key: str, index_seen, ws: Workspace, *,
                     owner_id: int = 0) -> dict:
        # The bash guard is seeded here, not supplied by the hooks provider: it is
        # the one hook that must exist in EVERY composition, including one with no
        # memory module wired. The path guard is seeded for the same reason and one
        # more: it is what confines this turn to `ws`, so a composition that forgot
        # it would be a turn with the run of the volume.
        hooks: dict[str, list] = {
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[bash_guard]),
                HookMatcher(matcher=PATH_TOOLS, hooks=[path_guard_factory(
                    self._allowed_roots(ws), cwd=ws.agent_home)]),
            ],
        }
        # The shared namespace is readable and not writable — for everyone but
        # its owner. BOTH spellings are fenced: the owner's `wiki/shared/`, which
        # the read root above just admitted, and the lane owner's OWN
        # `wiki/shared/`, because a model told a read-only shared wiki exists
        # will try `Write("wiki/shared/x.md")` — a relative path that resolves
        # into its own tree and would quietly succeed as a private page nobody
        # else ever sees. The deny message is what teaches it the difference.
        if not getattr(ws, "is_owner", True):
            hooks["PreToolUse"].append(HookMatcher(
                matcher=WRITE_TOOLS,
                hooks=[readonly_guard_factory(
                    [ws.shared_wiki_root, ws.wiki_root / "shared"],
                    cwd=ws.agent_home)],
            ))
        if self._hooks is not None:
            for event, matchers in self._hooks.build(
                    session_key, index_seen, ws, owner_id=owner_id).items():
                hooks.setdefault(event, []).extend(matchers)
        return hooks

    async def execute(self, *, user_text: str, resume_id: str | None, append: str,
                      session_key: str, ctx, attachments, index_seen,
                      on_slash_commands, on_session_id=None):
        ws = self._workspace(ctx)
        is_owner_lane = bool(getattr(ctx, "is_owner_lane", True))
        # The lane owner's shell GRANT, resolved once by `TurnService` and carried
        # on the context (0009) — never a deploy-wide setting read here, which is
        # what made a change need a restart. Absent (a context from before 0009)
        # reads as no grant: the fail-closed side, and an owner lane still gets
        # the full toolset through `is_owner_lane`.
        shell_enabled = bool(getattr(ctx, "shell_enabled", False))
        # Tool presence is a property of the LANE, never of the sender (Wave 4b,
        # D6). A group lane belongs to the deploy owner, so its library, recall
        # and spend are the owner's — and a room full of people must not
        # administer, search or bill against them. So on a group lane
        # `skill_admin`, `session_search` and `cost_report` exist for NOBODY, the
        # owner included. 4a decided the last two per sender, and a per-sender
        # flip rewrites the MCP schema list between two turns of one session: a
        # cached-prefix miss the lane never recovers from (P1). On every other
        # lane the sender IS the owner structurally, and all three exist, scoped
        # to the lane owner (`skills.handle(args, ctx=ctx)` picks their library).
        group = is_group_lane(session_key)
        handlers = make_handlers(
            search=None if group else self._search,
            costs=None if group else self._costs,
            ctx=ctx, settings=self._settings,
            home=self._home, workspace=ws,
            scheduler=self._scheduler,
            skills=None if group else self._skills,
            ext_admin=self._ext_admin, deliver_document=self._deliver_document,
            file_store=self._file_store, ask=self._ask,
            # The wiki it would read is the LANE owner's, so a group lane has no
            # `memory_search` for the same reason it has no `session_search`.
            # The flag is the deploy-wide gate; flipping it changes the MCP
            # schema list, which every lane pays for once as a session reset.
            wiki_search=None if (group or not getattr(
                self._settings, "memory_search_enabled", True))
            else self._wiki_search,
            max_document_bytes=self._max_document_bytes,
            content=self._content_for(ctx, session_key),
        )
        server, mcp_tool_names = build_hermes_server(handlers)
        # The lane's model, resolved once by `TurnService` and carried on the
        # context; a fork or a direct caller that resolved nothing gets the deploy
        # default. `cli_id` is the ONLY spelling that reaches the CLI — on some CLI
        # versions it carries a `[1m]` suffix the catalog id never has.
        requested = ctx.model or lookup(self._settings.model).id
        # `effort=None` means "send no flag", which is NOT the model's default
        # effort: the CLI picks that itself, and a lane that never chose must keep
        # getting whatever the CLI picks.
        effort_option = {} if getattr(ctx, "effort", None) is None else {"effort": ctx.effort}
        composed = ComposedTools()
        if self._compose is not None:
            # The LANE owner's connectors, for the same reason `cwd` is their
            # tree: a turn runs as whoever owns the lane, never as its sender.
            composed = await self._compose.for_turn(owner_id=ctx.owner_id)
        options = base_options(
            # The LANE OWNER's root, so `cwd` is their `agent_home`. For the
            # deploy owner `root is home` and this is byte-identical to Wave 3b.
            ws.root,
            model=ctx.cli_model or cli_id(self._settings.model),
            **effort_option,
            max_turns=self._settings.max_turns,
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            allowed_tools=builtin_tools(
                is_owner_lane=is_owner_lane, shell_enabled=shell_enabled,
            ) + mcp_tool_names + list(composed.tool_names),
            # The control, not the approve list: under bypassPermissions only
            # `disallowed_tools` takes a tool away (see `policies.denied_tools`).
            disallowed_tools=denied_tools(
                is_owner_lane=is_owner_lane, shell_enabled=shell_enabled,
            ),
            mcp_servers={"hermes": server, **composed.mcp_servers},
            plugins=composed.plugins,
            # `owner_id` is the LANE's owner, decided once by the runner: it is
            # what the Stop-hook index flush stamps the rows it writes with.
            hooks=self._build_hooks(session_key, index_seen, ws,
                                    owner_id=getattr(ctx, "owner_id", 0) or 0),
            # The CLI's own transcript store and its scratch directory, per user:
            # without the first, one user's `--resume` replays another's
            # conversation; without the second, every lane shares `/tmp`.
            env=workspace_env(ws),
            resume=resume_id,
            include_partial_messages=True,   # text.delta / thinking.delta for the web
            # Opus 4.7+ returns thinking as signature-only unless asked; the web
            # thread shows the model's reasoning, so request the summarized text.
            thinking={"type": "adaptive", "display": "summarized"},
        )

        emit = ctx.emit
        limit = getattr(self._settings, "web_tool_output_max_chars", 8000)
        started: dict[str, float] = {}       # tool call id → monotonic start
        final_text: str | None = None
        sdk_session_id: str | None = resume_id
        cost: float | None = None
        usage: dict | None = None
        last_usage: dict | None = None       # the last main-loop call's prompt tokens
        last_model: str | None = None
        stats = TurnStats()
        # Three facts about THIS call, kept in locals rather than on `self`: one
        # executor serves every lane in the process, so an attribute here would be
        # read by whichever turn happened to fail next.
        ran_model = ""            # what actually answered, for `turn.end` and the row
        tool_calls = 0            # a rejection is only a rejection before the first tool
        error_code = None         # `AssistantMessage.error`, e.g. "model_not_found"
        error_text = ""           # what that errored message said, in words
        try:
            async for msg in query(prompt=_build_prompt(user_text, attachments),
                                   options=options):
                if isinstance(msg, StreamEvent):
                    if msg.parent_tool_use_id is None:      # subagent tokens are not ours
                        ev = msg.event
                        delta = ev.get("delta") or {}
                        if ev.get("type") == "content_block_delta":
                            if delta.get("type") == "text_delta":
                                await emit("text.delta", {"delta": delta.get("text", "")})
                            elif delta.get("type") == "thinking_delta":
                                await emit("thinking.delta",
                                           {"delta": delta.get("thinking", "")})
                elif isinstance(msg, AssistantMessage):
                    if msg.parent_tool_use_id is not None:
                        continue
                    if getattr(msg, "usage", None) and msg.model != _SYNTHETIC_MODEL:
                        # The synthetic stand-in is not a model, so it must not
                        # become the key the context meter reads its window from.
                        last_usage, last_model = msg.usage, msg.model
                    # WHICH model answered, which is what every label names — never
                    # the one that was asked for. `<synthetic>` is the CLI's own
                    # stand-in for "nothing answered", and the CLI's Haiku side call
                    # is not a main-loop message, so neither can stand in here.
                    failed = getattr(msg, "error", None)
                    if msg.model and msg.model != _SYNTHETIC_MODEL:
                        ran_model = lookup(msg.model).id
                    parts = []
                    for b in msg.content:
                        if isinstance(b, TextBlock):
                            parts.append(b.text)
                            # An errored message is the CLI reporting a failure
                            # ("API Error: 429 …"), not the assistant talking: it
                            # must never land in the transcript as a reply. Its
                            # text goes into the exception this call raises, and
                            # so into `turn.end.error`, because the SDK's own
                            # exception text can be just the result subtype.
                            if failed is None:
                                await emit("text.block", {"text": b.text})
                        elif isinstance(b, ThinkingBlock):
                            await emit("thinking", {"text": b.thinking})
                        elif isinstance(b, ToolUseBlock):
                            tool_calls += 1
                            observe_block(stats, b)
                            started[b.id] = time.monotonic()
                            await emit("tool.call", {
                                "call_id": b.id, "name": b.name,
                                "input": _clip_input(b.input, limit)})
                    if failed is not None:
                        error_code = str(failed)
                        error_text = "\n".join(parts) or error_text
                    elif parts:
                        final_text = "\n".join(parts)
                elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                    for b in msg.content:
                        if isinstance(b, ToolResultBlock):
                            t0 = started.pop(b.tool_use_id, None)
                            await emit("tool.result", {
                                "call_id": b.tool_use_id,
                                "output": _clip(_result_text(b.content), limit),
                                "is_error": bool(b.is_error),
                                "duration_ms": int((time.monotonic() - t0) * 1000) if t0 else 0,
                            })
                elif isinstance(msg, SystemMessage):
                    if msg.subtype == "init":
                        sid = msg.data.get("session_id")
                        if isinstance(sid, str) and sid:
                            sdk_session_id = sid
                            if on_session_id is not None:
                                on_session_id(sid)
                        raw = msg.data.get("slash_commands")
                        if isinstance(raw, list):
                            on_slash_commands([n for n in raw if isinstance(n, str)])
                elif hasattr(msg, "session_id"):  # ResultMessage (duck-typed for tests)
                    sdk_session_id = msg.session_id or sdk_session_id
                    if getattr(msg, "result", None):
                        final_text = msg.result
                    cost = getattr(msg, "total_cost_usd", None)
                    usage = _usage_wire(last_model, last_usage, getattr(msg, "model_usage", None))
                    if cost:
                        try:
                            await self._costs.record(
                                datetime.now(timezone.utc).isoformat(), session_key, cost,
                                # The LANE's owner (Wave 4a, D7), so `cost_report`
                                # can answer "your spend" and not the deploy's.
                                owner_id=getattr(ctx, "owner_id", 0) or None,
                            )
                        except Exception:
                            log.debug("cost recording failed", exc_info=True)
        except Exception as exc:
            # A failed call says two things the SDK's own exception does not: what
            # the CLI told the user (`error_text` — the exception's text can be
            # nothing but the result subtype) and WHY it failed. Both are decided
            # here, from facts recorded above, never from the exception's text.
            if tool_calls == 0 and _is_rejection(error_code, exc):
                # The API refused this model before any tool ran. `_run_turn_body`
                # re-runs the turn once on the deploy default and says so in the
                # reply; a reset would not help, because nothing about the session
                # is wrong.
                raise ModelRejected(requested, error_text or str(exc)) from exc
            if error_text:
                raise CliCallFailed(error_text) from exc
            raise
        return final_text, sdk_session_id, stats, cost, usage, ran_model

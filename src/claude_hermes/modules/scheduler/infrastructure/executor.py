"""The unattended fork that actually runs a job's prompt.

Everything SDK-shaped lives here and nowhere else in the module: the service only
knows `run(job) -> (status, text)`.

Since Wave 4a the fork runs in the JOB OWNER's workspace (D6): a routine a user
created reads and writes their tree, not the deploy owner's, and its CLI
transcript store and `TMPDIR` are theirs too. A row with no owner — every job
created before this wave — is the deploy owner's, which is where it has been
running all along. Whether that fork has a shell is the owner's per-user grant,
asked of identity at fire time through the `OwnerGrants` port.

Another module may lend the run in-process tools through the `RoutineTools` port
(the content plan's `mcp__hermes__content_plan`, for the deploy owner's routines);
they merge after the composed extensions, so no extension can shadow them.

It also decides which MODEL a routine runs on, with the one resolver every channel
shares: the job's own pair, then `cron_model`, then `model`. A cron lane is shared
(`lane_is_shared("cron:…")`), so nobody's personal default reaches it.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog
from claude_agent_sdk import HookMatcher, query

from claude_hermes.core.config import NO_ADVISOR_ENV
from claude_hermes.core.model_catalog import Choice, is_model_command, resolve
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import (
    PATH_TOOLS,
    bash_guard,
    path_guard_factory,
)
from claude_hermes.integrations.claude_sdk.options import base_options, workspace_env
from claude_hermes.modules.scheduler.application.ports import (
    JobOutcome,
    OwnerGrants,
    RoutineTools,
)

log = structlog.get_logger(__name__)

_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"]

CRON_APPEND = """\
You are running an UNATTENDED scheduled job. There is no user present:
never ask questions; produce the deliverable directly. Keep the final
message self-contained and Telegram-sized — it is delivered to a chat."""

# A stored prompt that starts with `/model` or `/effort` is not a task: the CLI runs
# both as its OWN local commands in non-interactive mode ("Set model to x") and would
# change the model of the `cron:<id>` session with nothing recorded anywhere. The web
# form refuses such a prompt on create and edit; this is the row that got in before
# that, or through `cron_manage`, which takes no model and was not reopened.
MODEL_COMMAND_REFUSAL = (
    "Job '{name}' was not run: its prompt is a /model or /effort command, which "
    "the CLI executes as a local command instead of doing the job. Set the "
    "routine's model on the routine itself and give it a real prompt."
)


@dataclass(frozen=True)
class _NoExtensions:
    """What an `ExtensionComposer`-less deployment contributes: nothing."""
    mcp_servers: dict = field(default_factory=dict)
    tool_names: list = field(default_factory=list)
    plugins: list = field(default_factory=list)


class SdkJobExecutor:
    """`JobExecutor` over the Claude Agent SDK.

    `composer` is the extensions module's `ComposeService` (scope "cron"); `recorder`
    writes the prompt and the outcome into the `cron:<id>` transcript so a later turn
    in the delivery topic can quote them. Both are optional — a deployment with no
    extensions and no transcript still runs jobs.

    `grants` is identity's `AccessControl` (the `OwnerGrants` port): whether a
    non-owner's routine gets a shell is that user's grant at fire time. Without
    one a non-owner's routine gets no shell — failing closed, never falling back
    to a config key only identity is allowed to read.

    `routine_tools` (the `RoutineTools` port) lends in-process tools to the deploy
    owner's routines — `content_plan` today. `None` changes nothing.
    """

    def __init__(self, cfg, home: Path, *, composer=None, recorder=None,
                 workspaces=None, grants: OwnerGrants | None = None,
                 routine_tools: RoutineTools | None = None):
        self._cfg = cfg
        self._home = home
        self._composer = composer
        self._recorder = recorder
        # Wave 4a (D6): a routine runs as the person who created it, in THEIR
        # workspace. `core.workspace.WorkspaceResolver`; without one every job
        # runs where it always did, in the deploy owner's tree.
        self._workspaces = workspaces
        self._grants = grants
        self._routine_tools = routine_tools

    async def _owner_id(self, job: dict) -> int:
        """Whose routine this is.

        `jobs.owner_id` is NULL on every row created before Wave 4b, and the
        honest answer for those is the deploy owner: that is whose tree the job
        has been running in and whose files its prompt names. Resolving `0` would
        raise `UnknownUser` and turn every pre-4b routine into an error. One
        function, because the workspace and the connectors disagreeing about it
        would put a routine in one person's tree with another person's
        credentials.
        """
        if self._workspaces is None:
            return 0
        return int(job.get("owner_id") or 0) or await self._workspaces.owner_id()

    async def _workspace(self, job: dict) -> Workspace:
        """The job owner's tree."""
        if self._workspaces is None:
            return Workspace.owner(self._home)
        return await self._workspaces.for_user(await self._owner_id(job))

    async def _shell_of(self, owner_id: int) -> bool:
        """Whether a NON-owner's routine gets `Bash`/`Agent`/`Task`: that user's
        effective shell grant, read at fire time so a change in Settings → Users
        reaches the next run with no restart.

        Fails closed on both edges. No port wired → no shell (there is no
        `users_shell_enabled` fallback here: consumers never re-implement
        identity's resolution rule). A lookup that raises → logged, no shell. A
        turn would propagate that error instead (a wrong False there resets a
        live session under a notice that lies), but a routine has no session to
        reset, so running it without a shell costs no cached prefix — and the
        run is still recorded and delivered instead of lost before its `try`.
        """
        if self._grants is None:
            return False
        try:
            return bool((await self._grants.grants(owner_id)).shell)
        except Exception:
            log.exception("cron: shell grant lookup for user %s failed; running "
                          "without a shell", owner_id)
            return False

    async def _lent_tools(self, owner_id: int, *, is_deploy_owner: bool,
                          job_id: int) -> tuple[dict, list[str]]:
        """What the `RoutineTools` port lends this run: `(mcp_servers, names)`.

        Never asked for `owner_id <= 0` — a run whose owner nobody resolved (no
        workspace resolver wired) must not receive a tool whose every write is
        stamped with an owner. The port decides the rest (deploy owner only, its
        module switched on). A port that raises is logged and the run goes ahead
        without the tools: the routine's own job is still worth doing, and the
        tool's absence is what its report will show.

        Accepts a coroutine as well as a plain tuple: building an in-process server
        needs no I/O, but a lender that checks a switch in the database may.
        """
        if self._routine_tools is None or owner_id <= 0:
            return {}, []
        try:
            lent = self._routine_tools.for_owner(
                owner_id, is_deploy_owner=is_deploy_owner, job_id=job_id)
            if inspect.isawaitable(lent):
                lent = await lent
            servers, names = lent
            return dict(servers or {}), list(names or [])
        except Exception:
            log.exception("cron #%s: routine tools for user %s failed; running "
                          "without them", job_id, owner_id)
            return {}, []

    def _choice(self, job: dict) -> Choice:
        """Which model this routine runs on (D: `jobs.model/effort` → `cron_model` →
        `model`), through the resolver every channel shares.

        `shared=True` and no owner default: a `cron:` lane is shared by
        `lane_is_shared`, so the creator's personal default never silently moves a
        scheduled run. A job pinned to a model that has since left `models:` falls
        through to the deploy pair rather than failing — `resolve` skips a pair it
        cannot offer — and an effort the model does not take becomes None.
        """
        cfg = self._cfg
        return resolve(
            (job.get("model"), job.get("effort")), None, None,
            shared=True,
            deploy_model=cfg.cron_model or cfg.model,
            offered_ids=getattr(cfg, "models", ()),
        )

    async def run(self, job: dict) -> JobOutcome:
        cfg = self._cfg
        choice = self._choice(job)
        session_key = f"cron:{job['id']}"

        if is_model_command(job["prompt"]):
            # A failed run and NO CLI call: `_consume` hands the prompt straight to
            # `query`, so nothing else on this path would stop it (a foreground turn
            # is caught in `_run_turn_body`, which a routine never reaches).
            out = MODEL_COMMAND_REFUSAL.format(name=job["name"])
            log.warning("cron #%s stores a /model prompt; not run", job["id"])
            await self._record(session_key, job, out)
            return JobOutcome("error", out, choice.model, choice.effort)

        owner_id = await self._owner_id(job)
        workspace = await self._workspace(job)
        now = datetime.now(timezone.utc)

        composed = _NoExtensions()
        if self._composer is not None:
            composed = await self._composer.for_scope("cron", owner_id=owner_id)

        # A routine runs unattended in its owner's tree, and its owner may be a
        # non-owner user: the shell rule is the turn's (D6). The deploy owner's
        # routine always has one and never asks (decided from the resolved
        # workspace, as `sdk_runner` decides `is_owner_lane`); anyone else's
        # follows THEIR per-user shell grant as it stands when the routine fires.
        is_owner_lane = workspace.root == workspace.home
        shell = is_owner_lane or await self._shell_of(owner_id)
        tools = list(_TOOLS) if shell else [t for t in _TOOLS if t != "Bash"]
        denied = [] if shell else ["Bash", "Agent", "Task"]
        # Lent tools go on AFTER the composed extensions, in both lists: a dict
        # merge is last-wins, so an extension someone named `hermes` cannot shadow
        # the lent `hermes` server and with it `mcp__hermes__content_plan`.
        lent_servers, lent_names = await self._lent_tools(
            owner_id, is_deploy_owner=is_owner_lane, job_id=job["id"])

        options = base_options(
            workspace.root,
            # `cli_id` is the only spelling the CLI ever sees (a `[1m]` suffix on the
            # CLI versions that need it for a model's full window); `effort` is passed
            # ONLY when the pair carries one — the option's absence is what "let the
            # model use its own default effort" means.
            model=choice.cli_id,
            **({"effort": choice.effort} if choice.effort else {}),
            max_turns=cfg.max_turns,
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": f"{CRON_APPEND}\n\nToday's date: {now.date().isoformat()}"},
            allowed_tools=tools + composed.tool_names + lent_names,
            # The control, not the approve list (see `turns.domain.policies`):
            # under bypassPermissions only `disallowed_tools` takes a tool away.
            disallowed_tools=denied,
            mcp_servers={**composed.mcp_servers, **lent_servers},
            plugins=composed.plugins,
            hooks={"PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[bash_guard]),
                # The same confinement a foreground turn runs under: without it
                # `cron_manage` from a non-owner lane was a way to schedule an
                # unguarded `Read` over the whole volume (advisor blocker B-1).
                HookMatcher(matcher=PATH_TOOLS, hooks=[path_guard_factory(
                    [workspace.agent_home, workspace.tmp_dir, workspace.logs_dir,
                     workspace.plugins_dir], cwd=workspace.agent_home)]),
            ]},
            # MERGED, never replaced: `env` is one dict the SDK lays over
            # `os.environ`, so `env=workspace_env(...)` alone would drop
            # `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and quietly re-enable the advisor
            # on every scheduled run — at the advisor's rates, with no user
            # present to benefit.
            env={**NO_ADVISOR_ENV, **workspace_env(workspace)},
        )

        # The model that actually answered, collected outside `_consume` so a run that
        # times out still reports it. Only a main-loop `AssistantMessage` counts: a
        # subagent's carries `parent_tool_use_id`, an errored one carries a synthetic
        # name, and the CLI's own haiku side call never appears here at all.
        answered: list[str] = []

        async def _consume() -> str | None:
            final = None
            async for msg in query(prompt=job["prompt"], options=options):
                model = getattr(msg, "model", "")
                if (model and getattr(msg, "parent_tool_use_id", None) is None
                        and not getattr(msg, "error", None)):
                    answered.append(model)
                if getattr(msg, "result", None):
                    final = msg.result
            return final

        # A research job that sweeps a dozen sources cannot finish inside the default
        # three minutes, and raising the global limit would hand the same rope to every
        # other job. NULL means "inherit", so the global knob keeps working for the rest.
        timeout = job.get("timeout_seconds") or cfg.cron_timeout_seconds

        try:
            final = await asyncio.wait_for(_consume(), timeout=timeout)
            status, out = "ok", final or "(job produced no output)"
        except asyncio.TimeoutError:
            status = "timeout"
            out = f"Job '{job['name']}' hit the {timeout:.0f}s time limit."
        except Exception as e:
            log.exception("cron job %s failed", job["id"])
            status, out = "error", f"Job '{job['name']}' failed: {e}"

        ran = answered[-1] if answered else ""
        log.info("cron #%s ran on %s", job["id"], ran or choice.model,
                 requested=choice.model, effort=choice.effort, status=status)
        await self._record(session_key, job, out)
        return JobOutcome(status, out, choice.model, choice.effort, ran)

    async def _record(self, session_key: str, job: dict, out: str) -> None:
        """The prompt and the outcome in the `cron:<id>` transcript, so a later turn
        in the delivery topic can quote them. A refused run is recorded too: the
        reason is what the user needs to see."""
        if self._recorder is None:
            return
        ts = datetime.now(timezone.utc).isoformat()
        # The row's owner is the job's (D7): a routine's transcript is recalled
        # by the person who created it, in one owner-scoped GIN query.
        owner = int(job.get("owner_id") or 0) or None
        await self._recorder.record(session_key, "user", job["prompt"], ts,
                                    owner_id=owner)
        await self._recorder.record(session_key, "assistant", out, ts,
                                    owner_id=owner)

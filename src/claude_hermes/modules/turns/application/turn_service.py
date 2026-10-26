"""One turn, orchestrated: lane locking, the event envelope, the cron-context
bridge, session binding and the post-turn bookkeeping.

The SDK loop itself is behind `TurnExecutor` (infrastructure/sdk_runner.py) — this
layer may not import `claude_agent_sdk`, and the separation is also what lets a
test drive a whole turn with a two-line fake executor.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

import structlog

from claude_hermes.core.model_catalog import Choice, is_model_command, lookup
from claude_hermes.core.session_keys import (
    chat_id_from_key,
    lane_owner,
    thread_id_from_key,
)
from claude_hermes.modules.turns.application.ports import (
    JobLookup,
    LaneGrants,
    LaneModels,
    MessageLog,
    SessionLane,
    SlashCommandsStore,
    ThreadRegistry,
    TurnEventBusPort,
    TurnExecutor,
    TurnObserver,
    TurnSettings,
    WikiPort,
)
from claude_hermes.modules.turns.domain.exceptions import ModelRejected
from claude_hermes.modules.turns.domain.policies import (
    _FALLBACK_REPLY,
    _MODEL_COMMAND_REPLY,
    SHELL_CHANGED_NOTICE,
)
from claude_hermes.modules.turns.domain.prompt import build_system_append
from claude_hermes.modules.turns.domain.value_objects import (
    TurnContext,
    _attachment_wire,
    _compose_text,
)

log = structlog.get_logger(__name__)


def _is_owner_lane(workspace: Any) -> bool:
    """Is this lane the DEPLOY owner's?

    Asked of the workspace rather than of the ACL, because the workspace already
    encodes the answer and a second lookup is a second thing that can disagree:
    `Workspace.for_user(home, "")` is how — and only how — the owner's row is
    built, so `root == home` IS "the owner". `None` is what every lane took
    before this wave.
    """
    return workspace is None or workspace.root == workspace.home


@dataclass
class _ModelRun:
    """What this turn asked for, what answered, and whether those differ.

    Mutable and passed down rather than returned, because `turn.end` carries the
    three keys on EVERY path — `ok`, `error` and `cancelled` — and the two that
    are not `ok` have no return value to read them off. It starts as the resolved
    choice, so a turn that dies before the CLI answered still names the model it
    was going to run.

    `fallback_from` is `null` unless the model that answered is not the model that
    was asked for: the runner's one announced fallback, or a substitution the CLI
    made anyway. It is the only way a reader can tell a deliberate choice from a
    quiet swap.
    """
    model: str
    effort: str | None
    fallback_from: str | None = None

    def wire(self) -> dict:
        return {"model": self.model or None, "effort": self.effort,
                "fallback_from": self.fallback_from}


class TurnService:
    """The runner. Collaborators arrive as constructor arguments — the historical
    `runner._x = y` attribute wiring is what the composition root replaces."""

    def __init__(
        self,
        *,
        settings: TurnSettings,
        sessions: SessionLane,
        messages: MessageLog,
        threads: ThreadRegistry,
        wiki: WikiPort | None = None,
        wiki_for: Callable[[Any], WikiPort] | None = None,
        bus: TurnEventBusPort,
        executor: TurnExecutor,
        observer: TurnObserver | None = None,
        jobs: JobLookup | None = None,
        commands_store: SlashCommandsStore | None = None,
        deploy_owner: Callable[[], Awaitable[int]] | None = None,
        models: LaneModels | None = None,
        grants: LaneGrants | None = None,
    ):
        self._settings = settings
        self._sessions = sessions
        self._messages = messages
        self._threads = threads
        # Wave 4a: NOT one wiki. The index that goes into the system append and the
        # mtime `_index_seen` compares against belong to the LANE OWNER, so the
        # store is built per turn from the turn's workspace (advisor blocker B1).
        # `wiki=` alone is one fixed store, the deploy owner's — what a service
        # built without `wiki_for` gets.
        self._wiki_for = wiki_for or (lambda workspace: wiki)
        self.bus = bus
        self._executor = executor
        self._observer = observer
        self._jobs = jobs
        self._commands_store = commands_store
        # Wave 4a (D3/D7): who owns a GROUP or cron lane. `lane_owner` is pure
        # given this one number, and every other lane names its owner in its own
        # key, so this is the only identity fact the runner needs — which is why
        # it arrives as a callable (the resolver's cached `owner_id`) rather than
        # as an ACL this module would then depend on. `None` is a service built
        # without one (the forks): it gets `0`, i.e. "unscoped", exactly the
        # pre-4a behaviour.
        self._deploy_owner = deploy_owner
        # Which (model, effort) a lane runs, asked ONCE per turn and carried on
        # the context. `None` is a service built without the port — a fork, or a
        # test — and every lane then runs the deploy default, which is what every
        # lane ran before this feature.
        self._models = models
        # What a non-owner lane's owner may do beyond talking — a shell and the
        # learning observer (0009) — asked ONCE per turn, inside the turn's error
        # envelope, and carried on the context. Never cached here: the agent and
        # the worker must both see a grant the owner flips on the lane's very next
        # turn. `None` (a fork, a test) gives non-owner lanes neither.
        self._grants = grants
        # The index mtime each lane's cached prefix was built from. The
        # UserPromptSubmit hook compares against it; if it is not recorded at
        # append-build time the hook re-injects the index on every message.
        #
        # A CACHE, not the truth, since Wave 3b: the value lives on the session
        # row (`sessions.index_mtime`), because with N workers a lane lands on a
        # process that has never seen it on a large fraction of turns, and each
        # of those re-injects the whole wiki index into the user message — a
        # direct hit on the "≥60% of input tokens from cache" target. Seeded from
        # the row below, written back with `bind`/`touch`.
        self._index_seen: dict[str, float] = {}
        # What `/name` can invoke, as the CLI reported it in the last init message;
        # GET /api/commands reads it. Empty until a turn has run. `commands_store`
        # is the durable copy: the worker that ran the last turn is not the
        # process serving the request.
        self.slash_commands: list[str] = []
        self._unobserved_logged = False
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tasks: dict[str, asyncio.Task] = {}   # the task running each lane's turn

    # --- lane control -------------------------------------------------------

    async def reset(self, session_key: str) -> None:
        """Manual context reset (/new). The next turn starts a fresh SDK session."""
        async with self._locks[session_key]:
            await self._sessions.reset(session_key)

    async def reset_all(self) -> None:
        """Drop every lane's session — the mutation that just happened changed the
        cached prompt prefix (a plugin, a skill, a connector's toolset).

        Deliberately takes no lane lock, unlike `reset`. Taking every lock would
        mean waiting behind whatever turn is running; and it would buy nothing,
        because a turn in flight writes its session row back when it ends
        (`_sessions.bind` in `_run_turn_body`) and so keeps its session either way.
        The reset is for idle
        lanes — which is every lane in practice, since the caller is someone
        clicking in Customize.
        """
        await self._sessions.reset_all()

    async def reset_owned(self, owner_id: int, *, include_unowned: bool = False) -> None:
        """Drop every lane of ONE user — a skill they created, edited, archived or
        restored changed the index in their cached prefix and nobody else's.

        `reset_all` here would be correct and expensive: after Wave 4b a user's
        skills reach no other user's prefix, so evicting every other lane's warm
        cache for one person's edit is a cache-hit regression the NFR-1 target
        counts (B-4). Same no-lock rule as `reset_all`, for the same reason.
        `include_unowned` is for the deploy owner: a lane bound while the deploy
        was still unclaimed wrote NULL, and those lanes are theirs.
        """
        await self._sessions.reset_owned(owner_id, include_unowned=include_unowned)

    def is_busy(self, session_key: str) -> bool:
        return self._locks[session_key].locked()

    def cancel(self, session_key: str) -> bool:
        """Cancel the turn running on this lane. True if there was one."""
        task = self._tasks.get(session_key)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # --- the turn -----------------------------------------------------------

    async def run_turn(self, session_key: str, user_text: str, *, user_id: int = 0,
                       attachments=None, channel: str = "telegram",
                       turn_id: str | None = None, workspace: Any = None) -> str:
        """Run this turn here, now.

        `workspace` is the lane owner's tree, resolved by `TurnRunService` from the
        row (Wave 4a, D6) and threaded onto `TurnContext`, which is what the
        executor reads for `cwd`, the CLI's config dir, the toolset and the read
        guard's roots. Omitted, it stays `None` and everything
        below resolves the deploy owner's tree, which is what every lane got
        before this wave.

        `turn_id` arrives from the `turns` row since Wave 3b, so every event the
        turn emits names the row a client was handed in the 202. It is generated
        when omitted, which is what keeps a direct caller unchanged.

        The per-lane `asyncio.Lock` stays: it costs nothing, and it still protects
        those direct callers. The lane lock that matters across processes is the
        unique partial index on the `turns` table.
        """
        async with self._locks[session_key]:
            self._tasks[session_key] = asyncio.current_task()
            try:
                return await self._run_turn_locked(
                    session_key, user_text, user_id, attachments, channel,
                    turn_id or uuid.uuid4().hex, workspace,
                )
            finally:
                self._tasks.pop(session_key, None)

    async def _emit(self, session_key: str, turn_id: str, type: str, payload: dict) -> None:
        # A bus failure must never kill a turn: the reply still reaches the channel.
        try:
            await self.bus.emit(session_key, turn_id, type, payload)
        except Exception:
            log.exception("turn event emit failed (%s)", type)

    async def _lane_owner(self, session_key: str, channel: str, user_id: int) -> int:
        """The one rule (D3), applied once per turn.

        Everything this turn writes is stamped with the answer and everything it
        recalls is scoped to it, so it is resolved here — before the first row —
        rather than by each writer. A failure to learn the deploy owner must not
        kill the turn: `0` means "unscoped", the pre-4a behaviour, which loses the
        row's owner rather than the reply.
        """
        deploy_owner = 0
        if self._deploy_owner is not None:
            try:
                deploy_owner = int(await self._deploy_owner() or 0)
            except Exception:
                log.exception("could not resolve the deploy owner for %s", session_key)
        return lane_owner(session_key, channel, user_id, deploy_owner)

    def _deploy_choice(self) -> Choice:
        """The deployment's model, no effort — the floor every other answer falls
        back to and the model the runner's own fallback re-runs on."""
        info = lookup(self._settings.model)
        return Choice(info.id, info.cli_id, None)

    async def _effective(self, session_key: str, owner_id: int) -> Choice:
        """This lane's (model, effort) for this turn, resolved once.

        Asked before `turn.start`, because that event names the pair and the
        answer then travels on `TurnContext` — deriving it again lower down would
        be a second answer to "which model is this turn".

        A port that fails must not cost the reply: the deploy default is what the
        lane ran before anybody chose anything, and running it is better than
        refusing to run at all. The log line is the only place the miss is
        visible, which is why it carries the lane.
        """
        if self._models is None:
            return self._deploy_choice()
        try:
            choice = await self._models.effective(session_key, owner_id)
        except Exception:
            log.exception("could not resolve the model for %s; "
                          "running the deploy default", session_key)
            return self._deploy_choice()
        return choice if choice is not None else self._deploy_choice()

    async def _run_turn_locked(
        self, session_key: str, user_text: str, user_id: int, attachments, channel: str,
        turn_id: str, workspace: Any = None,
    ) -> str:
        now = datetime.now(timezone.utc)

        async def emit(type: str, payload: dict):
            await self._emit(session_key, turn_id, type, payload)

        owner_id = await self._lane_owner(session_key, channel, user_id)
        choice = await self._effective(session_key, owner_id)
        # What the turn asked for, until the CLI says what answered. Every
        # `turn.end` below reads it, including the two that never reach the SDK.
        ran = _ModelRun(model=choice.model, effort=choice.effort)
        ctx = TurnContext(
            session_key=session_key,
            chat_id=chat_id_from_key(session_key) or 0,
            thread_id=thread_id_from_key(session_key),
            user_id=user_id,
            turn_id=turn_id,
            emit=emit,
            workspace=workspace,
            is_owner_lane=_is_owner_lane(workspace),
            owner_id=owner_id,
            model=choice.model,
            cli_model=choice.cli_id,
            effort=choice.effort,
        )
        # `turn.start` names what was REQUESTED; `turn.end` names what answered.
        # Both, always, because "which model was this" is the one question a
        # priced switch has to answer without a reload.
        await emit("turn.start", {"channel": channel, "user_id": user_id,
                                  "model": choice.model, "effort": choice.effort})
        try:
            await self._threads.touch(
                session_key, channel, owner_id or None,
                now.isoformat(), talked=True,
            )
        except Exception:
            # Not fatal, but it IS the row every later read is scoped by: a lane
            # whose thread row never landed is invisible to its own owner until
            # the next turn writes it.
            log.exception("thread upsert failed")
        await emit("user.message", {
            "text": user_text,
            "attachments": [_attachment_wire(a) for a in (attachments or [])],
        })

        try:
            reply, sdk_session_id, cost, usage = await self._run_turn_body(
                session_key, user_text, user_id, attachments, ctx, now, ran,
            )
        except asyncio.CancelledError:
            await emit("turn.end", {"status": "cancelled", "cost_usd": None,
                                    "sdk_session_id": None, "error": None,
                                    **ran.wire()})
            raise
        except Exception as e:
            await emit("turn.end", {"status": "error", "cost_usd": None,
                                    "sdk_session_id": None, "error": str(e),
                                    **ran.wire()})
            raise
        await emit("turn.end", {"status": "ok", "cost_usd": cost,
                                "sdk_session_id": sdk_session_id, "error": None,
                                "usage": usage, **ran.wire()})
        return reply

    async def _run_turn_body(
        self, session_key: str, user_text: str, user_id: int, attachments,
        ctx: TurnContext, now: datetime, ran: _ModelRun,
    ) -> tuple[str, str | None, float | None, dict | None]:
        if is_model_command(user_text):
            # `/model` and `/effort` are the CLI's OWN local commands in
            # non-interactive mode: it would set the session's model and tell
            # nobody, leaving every row and every label wrong. Every channel
            # refuses such a text before it becomes a turn (the web route, the
            # composer, the Telegram gateway, routine create/edit); this is the
            # backstop for the paths that have none, and it never reaches the SDK.
            # Emitted as well as returned: the web thread renders `text.block`,
            # the returned reply is what Telegram sends. No model answered it, so
            # `turn.end` names none (P5) and the web shows no answer label.
            ran.model, ran.effort, ran.fallback_from = "", None, None
            if ctx.emit is not None:
                await ctx.emit("text.block", {"text": _MODEL_COMMAND_REPLY})
            return _MODEL_COMMAND_REPLY, None, None, None
        # The lane owner's grants, resolved once and carried on the context from
        # here on — the tools, the session stamp and the observer all read it.
        # Inside the envelope on purpose: a lookup that raises ends this turn with
        # `turn.end {status: "error"}` rather than running it on a guess.
        ctx = await self._with_grants(ctx)
        # THIS lane owner's wiki, not the deployment's. `index()` is one file read
        # off a path object, so building it per turn costs nothing — and it is the
        # only thing that keeps user B's byte-stable cached prefix from carrying
        # user A's index.
        wiki = self._wiki_for(ctx.workspace)
        row = await self._sessions.route(session_key, now)
        # The lazy reset (0009): a session built with a different toolset than the
        # lane has now cannot carry on (P1). Here and only here — before the cron
        # report and the append are chosen from the row, so a reset lane gets
        # exactly what a new one gets — and through the store, never
        # `TurnService.reset`, whose lane lock `run_turn` already holds. Nothing
        # resets lanes from the HTTP request that changed the grant, so this
        # cannot race a running turn's write-back.
        shell_notice = ""
        if row is not None and await self._session_shell(row, ctx) != ctx.has_shell:
            log.info("shell_grant_changed", session_key=session_key,
                     owner_id=ctx.owner_id, has_shell=ctx.has_shell)
            await self._sessions.reset(session_key)
            row = None
            # Said, not just done (P5): the user's conversation is starting over.
            # Emitted now so the web shows it before the answer; the returned
            # reply carries the same line for Telegram.
            shell_notice = SHELL_CHANGED_NOTICE
            if ctx.emit is not None:
                await ctx.emit("text.block", {"text": shell_notice})
        try:
            user_text = await self._with_cron_context(ctx, row, user_text)
        except Exception:
            log.exception("cron context injection failed; running the turn without it")
        # The lane's own record of which index version its cached prefix was built
        # from, seeded before `_execute` because the hook reads (and updates) the
        # dict. `max`, not "only when we have no entry": with N workers another
        # process may already have shown this lane a newer index and written the
        # row back, and a stale local copy would re-inject the whole index once
        # per worker per index change — the exact NFR-1 leak the column exists to
        # close. The row never goes backwards, so `max` can only help.
        if row is not None:
            seen = row.get("index_mtime")
            if seen is not None:
                self._index_seen[session_key] = max(
                    self._index_seen.get(session_key, 0.0), float(seen))
        # The slash commands the CLI reports, recorded only when they CHANGED —
        # comparing against the in-process copy costs nothing, and a meta write
        # per turn would be a round trip for a list that moves when a plugin does.
        fresh: list[list[str]] = []

        def on_slash_commands(names: list[str]) -> None:
            if list(names) != self.slash_commands:
                fresh.append(list(names))
            self._set_slash_commands(names)

        if row is not None:
            resume_id = row["sdk_session_id"]
            append = row["system_append"]
        else:
            resume_id = None
            append = build_system_append(wiki.index(), now.date())
            self._index_seen[session_key] = wiki.index_mtime()

        # The id the CLI announced in its `init` message. A stopped turn never
        # reaches its ResultMessage, so without it the first turn of a lane would
        # leave no row behind and the next message would start a NEW session
        # instead of continuing the one the owner interrupted.
        announced: list[str] = []

        def on_session_id(sid: str) -> None:
            announced.append(sid)

        try:
            try:
                (final_text, sdk_session_id, stats, cost, usage,
                 ran_model) = await self._execute(
                    user_text, resume_id, append, session_key, ctx, attachments,
                    on_slash_commands, on_session_id,
                )
            except ModelRejected as rejected:
                deploy = self._deploy_choice()
                if lookup(rejected.requested).id == deploy.model:
                    # A reset cannot fix a rejected model and there is nothing else to
                    # run on, so the session is left exactly as it was and the CLI's
                    # own sentence reaches `turn.end.error`.
                    raise
                log.warning("model_rejected", session_key=session_key,
                            requested=rejected.requested, retrying_on=deploy.model)
                ran.fallback_from = lookup(rejected.requested).id
                # The SAME resume id (or none): the rejected call never reached a
                # model, so the session is where it was and must NOT be reset. The
                # transcript then holds this prompt twice — the rejected call wrote
                # it too — which was measured live and is accepted (the story's
                # Locked Decisions); `--resume-session-at` would avoid it and is out
                # of scope. A second `ModelRejected` propagates from here: at most
                # two executions per turn, and still no reset.
                ctx = replace(ctx, model=deploy.model, cli_model=deploy.cli_id,
                              effort=None)
                (final_text, sdk_session_id, stats, cost, usage,
                 ran_model) = await self._execute(
                    user_text, resume_id, append, session_key, ctx, attachments,
                    on_slash_commands, on_session_id,
                )
            except Exception as e:
                if not self._executor.is_cli_death(e) or resume_id is None:
                    raise
                # Resume failed (e.g. missing/corrupted transcript for the stored
                # sdk_session_id — can happen after a container recreate). Drop the
                # stale session and retry once from scratch instead of leaving the
                # chat stuck erroring on every turn until the idle/daily reset.
                await self._sessions.reset(session_key)
                row = None
                resume_id = None
                append = build_system_append(wiki.index(), now.date())
                self._index_seen[session_key] = wiki.index_mtime()
                (final_text, sdk_session_id, stats, cost, usage,
                 ran_model) = await self._execute(
                    user_text, resume_id, append, session_key, ctx, attachments,
                    on_slash_commands, on_session_id,
                )
        except asyncio.CancelledError:
            if row is None and announced:
                try:
                    await self._sessions.bind(
                        session_key, announced[-1], append,
                        datetime.now(timezone.utc).isoformat(),
                        index_mtime=self._index_seen.get(session_key),
                        owner_id=ctx.owner_id or None, model=ctx.model or None,
                        effort=ctx.effort, shell_enabled=ctx.has_shell)
                except Exception:
                    log.exception("binding a cancelled turn's session failed")
            raise

        # P5: what answered, not what was asked for. A model that differs from the
        # requested one without a failure is the CLI substituting one anyway, and
        # it is announced exactly like the runner's own fallback — a silent swap is
        # what `CLAUDE_CODE_NO_MODEL_FALLBACK=1` and this line exist to prevent.
        if ran_model and lookup(ran_model).id != lookup(ctx.model).id:
            ran.fallback_from = ran.fallback_from or lookup(ctx.model).id
        # Through `lookup` on the way out too: the executor already answers with a
        # catalog id, and a spelling that slipped past it (`claude-opus-5[1m]`)
        # must not become the id a row records and a label shows.
        ran.model = lookup(ran_model).id if ran_model else ctx.model
        ran.effort = ctx.effort
        notice = ""
        if ran.fallback_from:
            notice = (f"{lookup(ran.fallback_from).label} is unavailable right now"
                      f" — {lookup(ran.model).label} answered instead.")
            if ctx.emit is not None:
                await ctx.emit("text.block", {"text": notice})

        now_iso = datetime.now(timezone.utc).isoformat()
        # Written back on the row, not left in this process: the hook may have
        # bumped it mid-turn, and the next turn on this lane may well run
        # somewhere else. `None` means "leave the column alone".
        index_mtime = self._index_seen.get(session_key)
        # The lane's owner, decided once in `_run_turn_locked`; `0` (a fork with no
        # lane) writes NULL rather than claiming user zero.
        owner_id = ctx.owner_id or None
        # The pair that ACTUALLY ran this session, so the resolver can read it back
        # as "what this live session runs": a changed personal default must not
        # move a session whose cached prefix is already scoped to another model.
        # And the `has_shell` its toolset was built from, which the next turn
        # compares with the lane's grant then (the lazy reset above).
        if sdk_session_id:
            if row is None:
                await self._sessions.bind(session_key, sdk_session_id, append, now_iso,
                                          index_mtime=index_mtime, owner_id=owner_id,
                                          model=ran.model or None, effort=ran.effort,
                                          shell_enabled=ctx.has_shell)
            else:
                await self._sessions.touch(session_key, now_iso,
                                           index_mtime=index_mtime, owner_id=owner_id,
                                           model=ran.model or None, effort=ran.effort,
                                           shell_enabled=ctx.has_shell)
        if fresh:
            await self._save_slash_commands(fresh[-1])

        reply = final_text or _FALLBACK_REPLY
        notices = [line for line in (shell_notice, notice) if line]
        if notices:
            # The same lines the `text.block`s above carried, in the same order:
            # the web renders the blocks, Telegram sends this string, and the
            # Telegram presenter ignores `text.block` entirely — so nobody sees
            # either twice.
            reply = "\n\n".join([*notices, reply])
        await self._messages.record(
            session_key, "user", _compose_text(user_text, attachments), now_iso,
            owner_id=owner_id,
        )
        await self._messages.record(session_key, "assistant", reply, now_iso,
                                    owner_id=owner_id)

        # The owner's lane is always observed; anybody else's only with their
        # learning grant, because their review forks run on the owner's account
        # (D3). The observer gets the LANE's owner so the sidecar it bumps and the
        # counters it reads are that user's, not the deploy owner's.
        if self._observer is not None and (ctx.is_owner_lane or ctx.learning_enabled):
            try:
                await self._observer.after_turn(session_key, stats,
                                                owner_id=ctx.owner_id)
            except Exception:
                log.exception("learning after_turn failed")
        elif self._observer is not None:
            self._say_unobserved()

        if ctx.flags.reset_lane:
            # An extension changed the toolset. Tool schemas are part of the cached
            # prefix, so the session must restart — and it must restart AFTER the
            # router has written the row back, or the bind above would resurrect it.
            await self._sessions.reset(session_key)
        return reply, sdk_session_id, cost, usage

    async def _execute(self, user_text, resume_id, append, session_key, ctx, attachments,
                       on_slash_commands=None, on_session_id=None):
        return await self._executor.execute(
            user_text=user_text, resume_id=resume_id, append=append,
            session_key=session_key, ctx=ctx, attachments=attachments,
            index_seen=self._index_seen,
            on_slash_commands=on_slash_commands or self._set_slash_commands,
            on_session_id=on_session_id or (lambda sid: None),
        )

    def _say_unobserved(self) -> None:
        """A lane went unobserved, said once per process rather than once per turn.

        A non-owner's conversation produces no nudges, no review pass and no
        curated skills unless the owner granted them learning (their own choice
        in Settings → Users, else `users_learning_enabled`) — the forks it would
        trigger are spend on the owner's account for somebody else's traffic
        (D12). Their library, `skill_admin` and the Skills page are theirs
        regardless; what is withheld is the background work, and nothing in their
        reply may claim it happened.
        """
        if self._unobserved_logged:
            return
        self._unobserved_logged = True
        log.info("a non-owner lane without a learning grant is not observed "
                 "by the learning loop")

    async def _with_grants(self, ctx: TurnContext) -> TurnContext:
        """The context with the lane owner's grants on it — asked once per turn.

        An owner lane never asks: `is_owner_lane` already gives it the whole
        toolset and the observer, so the two fields keep their False defaults
        and `has_shell` is True anyway. A service without the port (a fork, a
        test) leaves them False too — the fail-closed side. A lookup that raises
        is NOT caught: substituting a default would be a guess, and a wrong False
        would reset the lane under a notice that lies (P5). The caller's envelope
        turns it into `turn.end {status: "error"}`.
        """
        if ctx.is_owner_lane or self._grants is None:
            return ctx
        granted = await self._grants.grants(ctx.owner_id)
        return replace(ctx, shell_enabled=bool(granted.shell),
                       learning_enabled=bool(granted.learning))

    async def _session_shell(self, row, ctx: TurnContext) -> bool:
        """The `has_shell` the stored session's toolset was built with.

        The row's own stamp when it has one. A NULL stamp is a session started
        before 0009 (or by a process that predates it), which ran under the old
        deploy-wide rule — `is_owner_lane or users_shell_enabled` — so that rule
        is what it is compared as; the default is asked only for a non-owner lane,
        and only when there is a port to ask. Equal means the turn resumes it
        (and stamps it); different means the owner has made a choice since.
        """
        stamp = row.get("shell_enabled")
        if stamp is not None:
            return bool(stamp)
        if ctx.is_owner_lane:
            return True
        if self._grants is None:
            return False
        return bool((await self._grants.grant_defaults()).shell)

    def _set_slash_commands(self, names: list[str]) -> None:
        self.slash_commands = names

    async def _save_slash_commands(self, names: list[str]) -> None:
        """Persist what the CLI reported, so a process that ran no turn can serve
        `GET /api/commands`. Best effort: a failed write costs the `/` menu its
        plugin half until the next turn, and must never cost the reply."""
        if self._commands_store is None:
            return
        try:
            await self._commands_store.save(names)
        except Exception:
            log.exception("could not record the CLI's slash commands")

    async def _with_cron_context(self, ctx: TurnContext, row, user_text: str) -> str:
        """A scheduled job's report reaches its topic through RichSender, never
        through a turn, so the topic's SDK session has not seen it. The first turn
        after a delivery quotes the report back in — otherwise the user's reply
        ("3") lands on a model that never saw the brief it answers. Injected into
        the user message, not the system prompt, so the cached prefix is untouched;
        it is also what gets recorded to messages_fts, keeping the searchable
        history equal to what the model saw."""
        if self._jobs is None or ctx.thread_id is None:
            return user_text
        job = await self._jobs.job_for_thread(ctx.chat_id, ctx.thread_id)
        if job is None:
            return user_text
        last = await self._messages.last(f"cron:{job['id']}", role="assistant")
        if last is None:
            return user_text
        if row is not None and row["updated_at"] >= last["ts"]:
            # the lane already ran a turn after this delivery; its transcript has it
            return user_text
        return (
            f'[Context: this topic belongs to the scheduled job "{job["name"]}". '
            f'Its latest report, delivered at {last["ts"]}, is quoted below. '
            "The user's message after the --- divider is a reply to it.]\n\n"
            f"{last['content']}\n\n---\n\n{user_text}"
        )

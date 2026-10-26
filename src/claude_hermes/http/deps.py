"""The dependency bundle every module router is handed.

One dataclass so a router never reaches for a global: `web_deps(ctx)` builds the
real one out of the `AppContext`, tests build one with fakes and the same module
objects. Nothing here imports aiogram — or `app.py`, which imports *this* — so the
package stays acyclic.

Every attribute below is read **by name** by at least one module's providers
(`core.di.provide("<name>")`); each module's README lists the names it reads, and
`http/README.md` holds the union. A field is here because a provider names it and
for no other reason. The three small classes at the bottom are adapters in the
`core/app_context.py` sense: they bridge a port and its provider where the two
disagree about shape, and decide nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..modules.extensions.presentation.dependencies import ExtensionsDeps
from ..modules.extensions.wiring import OAuthOps
from ..modules.turns.application.control import MetaSlashCommands

__all__ = ["WebDeps", "web_deps", "extensions_view", "prefix_invalidator",
           "thread_guard", "PluginSlashSources"]


@dataclass
class WebDeps:
    """Everything the routers read, handed over by `web_deps(ctx)`.

    Nothing here builds anything: the composition root did that.

    A field with a `None` default is not automatically optional: `core.di.provide`
    decides that per provider, and `turns` is `required=True` there even though it
    defaults to `None` here (the defaults are a dataclass ordering rule, not a
    statement about the deployment). A bundle built without `turns` answers
    `503 unavailable` on `POST …/messages` and `POST …/cancel`.
    """

    # --- the shell --------------------------------------------------------
    cfg: Any
    runner: Any                 # TurnService: reset_owned (learning) / reset_all (extensions)
    acl: Any                    # identity AccessControl: admitted / is_owner / check
    bus: Any                    # TurnBus: subscribe / ready / snapshot

    # --- sessions + turns -------------------------------------------------
    threads: Any = None         # ThreadService — the thread list AND both lane guards
    lane_models: Any = None     # SessionService — a lane's model: GET/PUT …/{id}/model
    turns: Any = None           # TurnLauncher — is_busy / start / cancel; REQUIRED
    turn_events: Any = None     # TurnEventRepository — GET …/events and the SSE replay
    owned: Any = None           # async (request, session_key) -> row; raises NotFound
    auto_title: Any = None      # (session_key, first_message) -> None, fire-and-forget
    slash_sources: Any = None   # SlashSourcePort, for GET /api/commands
    sdk_commands: Any = None    # SlashCommandsPort — the names the CLI reported
    file_store: Any = None      # files FileService — uploads, downloads, attachments
    broker: Any = None          # the AskBroker, so a browser can answer an ask.open
    voice: Any = None           # integrations.openai.voice.OpenAIVoice, or None

    # --- the other modules ------------------------------------------------
    scheduler: Any = None       # scheduler SchedulerService
    # content ContentService — the plan's routes. None (`content_enabled` off) ->
    # no content router, and `/api/content*` falls through to the 404 catch-all,
    # which the SPA reads as "content disabled".
    content: Any = None
    extensions: Any = None     # the extensions routers' own ExtensionsDeps view
    catalog_root: Path | None = None   # tests point this at a fixture; None -> repo + user catalog
    # The `LearningScopes` registry — `for_user(uid)` is what the skills router's
    # providers resolve the requesting user's library, sidecar and snapshot store
    # through. None -> no skills router (the shell's mounting condition).
    learning: Any = None
    invalidate: Any = None      # extensions' PromptCacheInvalidator: awaited after an install
    wiki_root: Path | None = None      # WikiStore.root; None -> no wiki router
    telegram: Any = None        # TelegramGateway; only used in webhook mode
    update_dedup: Any = None    # telegram UpdateDedup; None -> the route's own LRU

    # --- identity and the static files ------------------------------------
    login_key: bytes = b""
    webapp_key: bytes = b""     # the Mini App initData check key; b"" -> that door is shut
    bot_username: str = ""      # the Login Widget's data-telegram-login, served at runtime
    home: Path | None = None
    # The `WorkspaceResolver`. `/api/commands` answers with the CALLER's skills
    # and plugins, so the route needs the same "whose tree" rule every turn uses.
    # None -> fall back to `home`, which is the deploy owner's tree.
    workspaces: Any = None
    dist_dir: Path | None = None
    now: Callable[[], int] | None = None  # epoch seconds; injectable for tests

    @property
    def visibility(self):
        """files' `ThreadVisibility` port. The same `ThreadService` the thread list
        uses — one lane-visibility rule, read from one place."""
        return self.threads


# --- port adapters ------------------------------------------------------------


def thread_guard(threads: Any):
    """turns' and files' lane guard: the thread row, or `NotFound`.

    The routers take a callable rather than the service because the check is about
    *this request* — `request.state.user_id` / `.is_owner` are put there by
    `core.auth.CurrentUserMiddleware`, and a service may not read a Request.
    """

    async def owned(request, session_key: str) -> dict:
        return await threads.get_visible(session_key, request.state.user_id,
                                         request.state.is_owner)

    return owned


class PluginSlashSources:
    """turns' `SlashSourcePort` over extensions' `ExtensionsQueryService`.

    Only the pair order differs: the query names the plugin first, the command
    collector reads the root first. An adapter, not a behaviour.
    """

    def __init__(self, query: Any):
        self._query = query

    async def plugin_roots(self, *, owner_id: int) -> list[tuple[Path, str]]:
        return [(root, name)
                for name, root in await self._query.enabled_plugin_roots(
                    owner_id=owner_id)]


def prefix_invalidator(runner: Any, deploy_owner: Any = None):
    """extensions' `PromptCacheInvalidator` for the web channel.

    Two arities, because the routers mean two different things by "the cached
    prompt prefix changed" and both are right. A connector one user enables or
    disables reaches only THEIR prefix, so `invalidate(owner_id)` drops that one
    person's lanes — evicting every warm lane for somebody else's decision is the
    cache-hit regression NFR-1 counts. Approving a draft writes the shared
    catalog, which every lane's prefix describes, so `invalidate()` still drops
    the deployment's.

    `deploy_owner` is the resolver's cached `owner_id`, and it only decides
    `include_unowned`: a group lane, and a lane bound while the deploy was still
    unclaimed, carries a NULL owner and is the deploy owner's.
    """
    async def invalidate(owner_id: int | None = None) -> None:
        if owner_id is None:
            await runner.reset_all()
            return
        owner = await deploy_owner() if deploy_owner is not None else 0
        await runner.reset_owned(owner_id, include_unowned=owner_id == owner)

    return invalidate


def extensions_view(bundle: Any, *, cfg: Any, catalog_root: Path | None,
                    invalidate: Any) -> ExtensionsDeps | None:
    """What the three extensions routers read, over the bundle the root already built.

    The composition root's own objects pass straight through, so an install and the
    turn that has to forget its prompt prefix are talking about the same services.
    """
    if bundle is None:
        return None
    return ExtensionsDeps(
        cfg=cfg, home=bundle.home, catalog_root=catalog_root,
        catalog=bundle.catalog, secrets=bundle.secrets, store=bundle.store,
        tokens=bundle.tokens, writer=bundle.writer, pending=bundle.pending,
        drafts=bundle.drafts, approvals=bundle.approvals,
        extensions=bundle.extensions, plugins=bundle.plugins, reader=bundle.reader,
        probe_fn=bundle.probe_fn, installer=bundle.installer, oauth=OAuthOps(),
        invalidate=invalidate)


def web_deps(ctx: Any, *, bot_username: str = "", dist_dir: Path | None = None,
             now: Callable[[], int] | None = None) -> WebDeps:
    """The web channel's view of an `AppContext`. No construction but the adapters."""
    # One invalidator, shared by the extensions routers and the skills router's
    # own: a connector enabled in the browser drops the caller's lanes, and an
    # approved draft — the shared catalog — still drops every lane.
    invalidate = prefix_invalidator(ctx.runner, getattr(ctx.workspaces, "owner_id", None))
    return WebDeps(
        cfg=ctx.cfg, runner=ctx.runner, acl=ctx.acl, bus=ctx.bus,
        threads=ctx.threads, lane_models=ctx.lane_models,
        turns=ctx.turns, turn_events=ctx.turn_events,
        owned=thread_guard(ctx.threads), auto_title=ctx.titles.autotitle,
        slash_sources=PluginSlashSources(ctx.extensions.query),
        # The `/` menu's third source, read from `meta` rather than off `ctx.runner`:
        # the worker that heard the CLI report its commands is not this process.
        sdk_commands=MetaSlashCommands(ctx.meta),
        file_store=ctx.files, broker=ctx.ask_broker, voice=ctx.voice,
        scheduler=ctx.scheduler, content=ctx.content,
        extensions=extensions_view(ctx.extensions, cfg=ctx.cfg, catalog_root=None,
                                   invalidate=invalidate),
        learning=ctx.learning.scopes,
        # The EXTENSIONS invalidator only. A skill edit changes one user's prefix
        # too, and the skills router builds its own `reset_owned` per request
        # (learning's `get_invalidate`).
        invalidate=invalidate,
        wiki_root=ctx.memory.wiki_root, telegram=ctx.telegram,
        update_dedup=ctx.update_dedup,
        login_key=ctx.web_login_key, webapp_key=ctx.web_webapp_key,
        bot_username=bot_username, home=ctx.workspace.home,
        # The same resolver every turn uses, so `/api/commands` reads the caller's
        # tree rather than `home`, which is the deploy owner's.
        workspaces=ctx.workspaces, dist_dir=dist_dir,
        now=now or (lambda: int(time.time())),
    )

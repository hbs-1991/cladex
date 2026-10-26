"""The CladeX HTTP app: the modules' routes, the auth gate, and the SPA static files.

Runs inside the hermes process (main.py starts uvicorn as a task). This file owns
nothing but assembly: every route comes from a module's `presentation/router.py`,
identity owns the session cookie, `core.auth.CurrentUserMiddleware` owns the current
user, and the policies left here are transport — an Origin matching
`web_public_origin` and a per-route content type on every mutation — and
compression, which is on the shell because nothing in front of this process does it.

One error shape for the whole API: `{"error": {"code", "message"}}`. `error_response`
is what the shell answers with before any handler runs — the transport policy's
403/415 and the `/api/*` catch-all's 404 — and what the two exception handlers at the
bottom render an `ApplicationException` or a validation failure as, so the frontend
never has to guess whether a failure came back as HTML, a bare string, or a Starlette
default page.
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..core.auth import CurrentUserMiddleware
from ..core.exceptions import ApplicationException
from ..modules.identity.domain.session_token import COOKIE, read_session
from .deps import WebDeps, web_deps

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Routes whose body is not JSON. Everything else that mutates must send JSON.
CONTENT_TYPES = {
    "/api/uploads": "multipart/form-data",
    "/api/voice/transcribe": "audio/",
}
# A missing cookie on these paths belongs to a person's browser, not a fetch(): the
# OAuth callback is a top-level navigation back from the provider, and a JSON error
# body is all the owner would see. The path is `connectors.CALLBACK_PATH`, spelled
# out rather than imported so app.py still loads with a router stubbed out.
REDIRECT_ON_401 = {"/api/connectors/oauth/callback": "/customize/connectors?error=forbidden"}


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class TransportPolicyMiddleware(BaseHTTPMiddleware):
    """The half of the old `ApiMiddleware` that is not identity.

    A mutation must carry the exact `Origin` (this, and not SameSite, is what stops
    a forged POST from another site — a Mini App session cookie is SameSite=None)
    and the content type its route expects. Who the caller *is* is
    `core.auth.CurrentUserMiddleware`'s question, and it runs inside this one.
    """

    def __init__(self, app, deps: WebDeps):
        super().__init__(app)
        self._deps = deps

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or request.method not in MUTATING:
            return await call_next(request)
        cfg = self._deps.cfg
        origin = request.headers.get("origin", "")
        if not cfg.web_public_origin or origin != cfg.web_public_origin:
            return error_response(403, "bad_origin", "origin not allowed")
        ctype = request.headers.get("content-type", "")
        wanted = next((v for k, v in CONTENT_TYPES.items() if path.startswith(k)),
                      "application/json")
        if not ctype.startswith(wanted):
            return error_response(415, "unsupported_media_type", f"expected {wanted}")
        return await call_next(request)


class SpaStaticFiles(StaticFiles):
    """Vite build: real files as-is, every other path gets index.html so the
    client router owns /routines/3 and friends."""

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except Exception:
            response = FileResponse(Path(str(self.directory)) / "index.html")
        # Vite hashes every asset name, so assets may be cached forever — but
        # index.html is what names them. Without an explicit policy browsers
        # cache it heuristically and keep loading the previous deploy's bundle.
        if str(getattr(response, "path", "")).endswith("index.html"):
            response.headers["cache-control"] = "no-cache"
        return response


async def api_not_found(request: Request) -> Response:
    return error_response(404, "not_found", "no such endpoint")


async def healthz(request: Request) -> Response:
    """`GET /healthz` -> `200 {"ok": true}`. Public, and deliberately shallow.

    "Is this process still serving HTTP?" and nothing else: a compose healthcheck
    or a proxy probe calls it, and it must not become a liveness check that fails
    when the database is slow — a probe that restarts the container because
    Postgres is busy turns a hiccup into an outage. The deeper "can it reach the
    database" question is `docker-compose.prod.yml`'s asyncpg probe.

    Outside `/api/`, so `CurrentUserMiddleware` (which guards `/api/*` only) never
    looks for a cookie and `TransportPolicyMiddleware` never asks for an Origin.
    It answers nothing about this deployment beyond "there is one".
    """
    return JSONResponse({"ok": True})


def validation_message(exc: RequestValidationError) -> str:
    """A `RequestValidationError` as one sentence: `"body.title: Field required"`.

    Only the first error, and never the input value: the hand-written handlers this
    wave replaces answered one message for a malformed body, and echoing
    `exc.errors()` wholesale would put a rejected payload (a login hash, a secret)
    into a response body and into whatever logs it.
    """
    errors = exc.errors()
    if not errors:
        return "invalid request"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    msg = str(first.get("msg", "invalid request"))
    return f"{loc}: {msg}" if loc else msg


def build_web_app(ctx_or_deps, extra_routes: list[Route] | None = None,
                  **kwargs) -> FastAPI:
    """Assemble the app from an `AppContext` (or a ready `WebDeps`, as tests pass).

    `kwargs` (`bot_username`, `dist_dir`, `now`) are the things a context does not
    carry: the bot's username comes from `getMe` and the SPA directory from the
    image layout, and neither belongs in `build_app()`.
    """
    deps = ctx_or_deps if isinstance(ctx_or_deps, WebDeps) else web_deps(ctx_or_deps,
                                                                        **kwargs)
    if deps.now is None:
        deps.now = lambda: int(time.time())
    # Imported here, not at module scope: nothing else in this package imports a
    # module router, so the graph stays acyclic either way — but a local import also
    # keeps `app` importable while a router is a stub. The modules themselves, not
    # their routers: `mount` reads `presentation.router` off each one, and telegram
    # is also asked whether it wants to be mounted at all.
    from ..modules.content.presentation import router as content
    from ..modules.extensions.presentation.routers import (
        connectors,
        drafts,
        plugins,
        secrets,
    )
    from ..modules.files.presentation import router as files
    from ..modules.identity.presentation import router as identity
    from ..modules.identity.presentation import users_router as users
    from ..modules.identity.presentation.router import PUBLIC_PATHS
    from ..modules.learning.presentation import router as skills
    from ..modules.memory.presentation import router as wiki
    from ..modules.scheduler.presentation import router as routines
    from ..modules.sessions.presentation import router as threads
    from ..modules.telegram.presentation import webhook as telegram
    from ..modules.turns.presentation import commands_router as commands
    from ..modules.turns.presentation import models_router as models
    from ..modules.turns.presentation import router as turns
    from ..modules.turns.presentation import voice_router as voice

    ext = deps.extensions
    app = FastAPI(openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None)
    # The bundle every module's providers read (`core.di.bundle`). Stored before the
    # first router is mounted: a provider built at import time resolves it per request,
    # and a router mounted on an app without it would 500 on the first hit.
    app.state.deps = deps

    def mount(module, *, enabled: bool = True) -> None:
        """Mount one module's `presentation.router`, if this deployment has it.

        `enabled` is the whole of the mounting policy: a module states what it needs
        (its README lists the bundle attributes its providers read) and the shell
        decides whether those exist here. A router that is not mounted leaves its
        paths to the `/api/*` catch-all below, which answers the 404 the tests pin —
        `core.di.provide`'s 503 is for the disagreement between these two, not for a
        deployment without a wiki.
        """
        if enabled:
            app.include_router(module.router)

    # turns before threads: `/api/threads/{id}/messages` and friends must match
    # before the generic `/api/threads/{id}` routes the threads router registers.
    # Each Customize router orders its own fixed segments ahead of its `{name}`
    # routes; the four of them share no prefix, so their order here is free.
    mount(identity)
    mount(users)
    mount(turns)
    mount(threads, enabled=deps.threads is not None)
    mount(files, enabled=deps.file_store is not None)
    mount(routines, enabled=deps.scheduler is not None)
    # `content_enabled` is the condition: with it off the service is None and
    # `/api/content*` falls through to the catch-all's 404, which is exactly the
    # SPA's "content disabled" signal. `/api/content/publisher` is safe from the
    # `{id}` routes by their `int` converter, not by order.
    mount(content, enabled=deps.content is not None)
    mount(connectors, enabled=ext is not None)
    mount(plugins, enabled=ext is not None)
    mount(drafts, enabled=ext is not None)
    mount(secrets, enabled=ext is not None)
    # The `LearningScopes` registry: the skills router resolves the requesting
    # user's library through it, so no registry means no skills routes. A mounting
    # condition like the five above it, so it belongs with them.
    mount(skills, enabled=deps.learning is not None)
    # The condition the wiki router used to hold itself: no wiki on disk, no routes,
    # and the catch-all below answers the 404. It lives here now because mounting is
    # the shell's decision — a module states what it needs, not whether it exists.
    mount(wiki, enabled=deps.wiki_root is not None)
    mount(voice)
    mount(commands)
    # `GET /api/models` reads nothing but `cfg`, which every bundle carries.
    mount(models)
    # Not mounted in polling mode, which is every deployment today: the module's own
    # `webhook_enabled` is the condition (and it refuses to boot in webhook mode
    # without a secret rather than publishing a door anyone may POST to).
    mount(telegram, enabled=telegram.webhook_enabled(deps.cfg))
    app.router.routes.extend(extra_routes or [])
    app.router.routes.append(Route("/api/{rest:path}", api_not_found))
    # A plain Starlette route, and registered BEFORE the SPA mount below: `/` is a
    # catch-all that answers index.html for anything it does not recognise, so a
    # probe added after it would get 200 and an HTML body and report a healthy
    # process forever. Not in the OpenAPI document on purpose — it is a probe for
    # compose and the proxy, not part of the `/api/*` contract `client.ts` is
    # written against.
    app.router.routes.append(Route("/healthz", healthz, methods=["GET"]))
    if deps.dist_dir is not None:
        app.router.routes.append(
            Mount("/", app=SpaStaticFiles(directory=str(deps.dist_dir), html=True)))

    # Re-checked per request (Wave 4b, R-8), not only at login: a cookie outlives
    # the allowlist entry or the `disabled_at` that should have ended it, and
    # after 4b a logged-in user writes prompt text into a tree. `admitted` is
    # `check`'s answer without `check`'s writes — one SELECT, never an UPDATE of
    # `last_seen_at` or a materialised row per API call.
    admitted = deps.acl.admitted

    # Added first, so it ends up INSIDE the transport check: a request with a forged
    # Origin is refused before anyone asks whose session it carries.
    # The union of every router's public set — the paths that must answer before a
    # cookie exists. identity owns the only one today.
    app.add_middleware(
        CurrentUserMiddleware,
        read_session=lambda r: read_session(r.cookies.get(COOKIE, ""),
                                            deps.cfg.web_secret, deps.now()),
        is_owner=deps.acl.is_owner,
        admitted=admitted,
        public_paths=frozenset().union(PUBLIC_PATHS),
        redirect_on_401=REDIRECT_ON_401,
    )
    app.add_middleware(TransportPolicyMiddleware, deps=deps)

    # Added last, so it is the OUTERMOST middleware: it has to see the SPA bundle
    # the static mount serves and the refusals the two middlewares above answer
    # with, not just what a handler returns.
    #
    # Nothing else in the chain compresses. Caddy's `encode` is not in the
    # Caddyfile and a deployment behind a tunnel or on a bare port has no proxy at
    # all, so without this every byte goes out raw: the SPA entry chunk is 1.4 MB
    # against 430 KB gzipped, a Shiki grammar is 785 KB against 52 KB, and a
    # thread's `GET …/events` history — which is JSON and compresses about
    # tenfold — is the one a person waits on when they open a thread.
    #
    # `compresslevel=6` and not the default 9: the difference in ratio is a couple
    # of percent and the difference in CPU is several times, on a body that can be
    # megabytes and is built per request.
    #
    # Starlette does not compress `text/event-stream` (its
    # `DEFAULT_EXCLUDED_CONTENT_TYPES`), which is what keeps the SSE thread tail
    # unbuffered — a gzip stream would hold each frame until its window filled and
    # the browser would receive a turn in one lump at the end. That exclusion is
    # load-bearing rather than incidental, so `tests/test_web_app.py` pins it.
    app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)

    async def _application_error(request: Request, exc: ApplicationException):
        return error_response(exc.status, exc.code, exc.message)

    async def _validation_error(request: Request, exc: RequestValidationError):
        # 400, not FastAPI's 422: every hand-written handler this wave replaces
        # answered `400 bad_request` for a malformed body, and the frontend branches
        # on that code. A schema that started answering 422 would be a wire break.
        return error_response(400, "bad_request", validation_message(exc))

    app.add_exception_handler(ApplicationException, _application_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    return app

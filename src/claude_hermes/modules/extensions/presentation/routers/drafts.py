"""Drafts router: the browser as the approval screen for a drafted catalog entry.

The agent may write a manifest into `catalog-drafts/`, and so may the owner — `POST
/api/drafts` is how a plugin that is not in the catalog gets in, since a plugin is a
repo at a pinned ref and there is nothing to dial or probe. Either way nothing loads it
until a human approves it: filing a draft and approving one are two requests, and the
second is the only one that writes to the catalog. Until now the only two places to read
that manifest's executable surface were a Telegram message and a terminal on the host.
This is the third and the best of them — and it keeps chat's guarantee exactly: `review`
snapshots the bytes and returns a token bound to them, `approve` sends the token back and
the server promotes the *snapshot*, so a draft rewritten in between cannot ride in on an
approval that was given for different code.

Owner-only and browser-only by construction: these are routes, not tools, so a
prompt-injected turn still cannot enact its own draft. The gate is
`Depends(require_owner)` on the route, which is solved before `draft_name` — the order
`owner_only(request)` had as the first line of every handler here.
"""
from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, Response

from claude_hermes.core.auth import CurrentUser, require_owner
from claude_hermes.core.exceptions import ApplicationException as ApiError

from ...domain.drafts import describe, safe
from ...domain.exceptions import DraftError, ManifestError
from ...domain.manifest import MANIFEST_VERSION, VALID_SCOPES
from ..dependencies import ExtensionsDeps, draft_name, get_extensions
from ..schemas import RawJsonBody, draft_to_wire

KINDS = ("mcp", "plugin")

router = APIRouter(tags=["extensions"])


@router.get("/api/drafts", operation_id="extensions_list_drafts")
async def list_drafts(ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """The queue, and what could not be parsed. Readable by any logged-in user."""
    return JSONResponse({
        "drafts": [draft_to_wire(m) for m in await ext.drafts.pending()],
        # `pending` drops these silently, which is right for the agent and wrong
        # for the operator: a draft that just vanishes looks like nothing was
        # ever filed, and the human waits forever for an approval to appear.
        # `file` keeps the `<name>.yaml` spelling the queue had on disk: the SPA
        # prints it, and the wire shape is frozen (spec req. 36).
        "rejected": [{"file": f"{name}.yaml", "reason": safe(reason)}
                     for _kind, name, reason in await ext.drafts.rejected()],
    })


@router.post("/api/drafts", operation_id="extensions_create_draft", status_code=201,
             dependencies=[Depends(require_owner)])
async def create(body: RawJsonBody = None,
                 user: CurrentUser = Depends(require_owner),
                 ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """File a plugin draft from the browser. Installs nothing, approves nothing.

    A connector can be added outright (`POST /api/connectors`) because a URL is
    inert until something dials it and the probe says whether it answers. A plugin
    is code that loads inside the agent, so it takes the same road a chat-drafted
    entry takes: the fields become a manifest, `drafts.save` runs the real validator
    on it — the ref must be a tag or a sha, the repo an https URL — and the result
    joins the queue above the grid. What is approved is the snapshot `review` takes,
    so the bytes the owner reads are the bytes that land.

    Only `kind: plugin`. The MCP half of the catalog has its own create route, and a
    second way to author the same thing is a second set of rules to keep in step.
    """
    if not isinstance(body, dict):
        raise ApiError(400, "bad_manifest", "expected a JSON object")
    kind = str(body.get("kind") or "plugin")
    if kind != "plugin":
        raise ApiError(400, "bad_kind",
                       "only a plugin can be drafted here; a connector is added on "
                       "the Connectors tab")

    def text(key: str) -> str:
        return str(body.get(key) or "").strip()

    # The anti-downgrade rule, checked here as well as in `approve`: filing a draft
    # that can never be approved wastes the owner's next click, and the message here
    # can say why while the form is still open.
    if ext.catalog.is_repo_entry(text("name")):
        raise ApiError(409, "shadowed",
                       f"'{text('name')}' is a reviewed catalog entry and cannot be replaced")

    data: dict = {
        "manifest_version": MANIFEST_VERSION,
        "name": text("name"),
        "label": text("label") or text("name"),
        "description": text("description"),
        "version": text("version") or "1",
        "repo": text("repo"),
        "ref": text("ref"),
    }
    if text("subdir"):
        data["subdir"] = text("subdir")
    scopes = body.get("scopes")
    data["scopes"] = ([str(x) for x in scopes] if isinstance(scopes, list) and scopes
                      else list(VALID_SCOPES))
    try:
        m = await ext.drafts.save(yaml.safe_dump(data, sort_keys=False,
                                                 allow_unicode=True),
                                  # Who proposed it (Wave 4a, audit only). The
                                  # route is `require_owner`, so this is the owner.
                                  owner_id=user.user_id)
    except (DraftError, ManifestError) as e:
        # The validator's own words. They name the field and say why it was refused,
        # which is exactly what the person filling the form needs to read.
        raise ApiError(400, "bad_manifest", str(e)) from e
    return JSONResponse(draft_to_wire(m), status_code=201)


@router.post("/api/drafts/{name}/review", operation_id="extensions_review_draft",
             dependencies=[Depends(require_owner)])
async def review(name: str = Depends(draft_name),
                 ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """The executable surface of the queued draft, and a token bound to its bytes."""
    started = await ext.approvals.begin(name)
    if started is None:
        raise ApiError(404, "no_draft", f"no draft named '{name}' is queued")
    m, surface, token = started
    return JSONResponse({
        "name": m.name, "kind": m.kind, "label": safe(m.label),
        "surface": surface, "token": token,
        # Whether `describe_with_banner` prepended the LOCAL COMMAND banner,
        # as a flag — so the dialog draws a warning strip instead of parsing
        # the text. Derived from the two functions rather than restated, so
        # the strip cannot disagree with the banner.
        "local_command": surface != describe(m),
    })


@router.post("/api/drafts/{name}/approve", operation_id="extensions_approve_draft",
             dependencies=[Depends(require_owner)])
async def approve(body: RawJsonBody = None, name: str = Depends(draft_name),
                  ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Promote the snapshot the token was issued for, and nothing else."""
    token = str(body.get("token") or "") if isinstance(body, dict) else ""
    # The anti-downgrade rule: a chat-authored entry may only fill a name the
    # reviewed catalog does not define, never replace one.
    if ext.catalog.is_repo_entry(name):
        raise ApiError(409, "shadowed",
                       f"'{name}' is a reviewed catalog entry and cannot be replaced")
    result = await ext.approvals.confirm(name, token)
    if result is None:
        # No snapshot, or the draft changed under it. Either way the bytes this
        # token was issued for are not the bytes on disk: review it again.
        raise ApiError(409, "stale_draft",
                       f"'{name}' changed since it was reviewed; read it again")
    m, raw = result
    ext.writer.promote(m, raw)
    # P5: do not claim it landed until the reader can see it. The draft stays
    # queued so a retry is free — clearing it here would strand the manifest.
    if ext.catalog.get(m.name, ext.catalog_root) is None:
        raise ApiError(500, "unverified",
                       "the manifest was written but the catalog cannot see it")
    await ext.approvals.clear(m.kind, name)
    # The catalog is in the cached prompt prefix: a lane resuming an old session
    # still believes this draft is queued and that installing is not its step.
    await ext.invalidate()
    return JSONResponse({"name": m.name, "kind": m.kind})


@router.delete("/api/drafts/{name}", status_code=204,
               operation_id="extensions_discard_draft",
               dependencies=[Depends(require_owner)])
async def discard(name: str = Depends(draft_name),
                  ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """Drop the draft and any snapshot. The kind is not in the URL and a
    rejected draft has no parseable manifest to ask, so both spellings are
    cleared — `clear` is a DELETE that matches nothing, so the miss is free."""
    for kind in KINDS:
        await ext.approvals.clear(kind, name)
    return Response(status_code=204)

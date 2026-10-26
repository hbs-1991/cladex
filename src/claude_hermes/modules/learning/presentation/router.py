"""Skills router: the agent's procedural memory, made visible and editable.

Every read and every file write goes through `SkillLibraryService`, the same
service `skill_admin` uses, so the browser and the chat tool cannot drift on what
"stale", "bundled" or "archived" mean. What the service deliberately leaves to
this layer is the bookkeeping the two channels do differently: the pre-edit
snapshot, the patch count, and the session reset.

Two rules worth knowing before reading the handlers:

* **An edit carries an etag.** The review pass writes these same files after most
  turns. Without the precondition an owner's Save would silently discard whatever
  the agent had just learned, so a mismatch is a 409 that hands back the current text.
* **Nothing here deletes.** Archive moves a directory into `.archive/` and Restore
  moves it back (PRD P4). The absence of a delete route is the feature.

**Every route is the requesting user's own library** (Wave 4b, D6). There is no
owner gate any more: the providers resolve the caller's `LearningScope`, so a read
lists their skills, a mutation edits their skills, and a name from somebody else's
library is a plain 404 — the scope is the authorization, and no route can name
another user's tree. `require_owner` used to sit on the mutations because the one
library was the owner's; that library is now one of N.

Dependencies: `get_library`, `get_usage`, `get_skills_dir`, `get_logs_dir`,
`get_backup_keep`, `get_invalidate` — see `dependencies.py`. The **shell** decides
whether this router is mounted at all (`deps.library is not None`); the router used
to hold that condition itself.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from claude_hermes.core import snapshots
from claude_hermes.core.exceptions import Conflict

from ..application.services.library import SkillLibraryService
from ..domain.constants import SNAPSHOT_KIND
from ..domain.exceptions import SkillError, StaleEdit
from .dependencies import (
    get_backup_keep,
    get_invalidate,
    get_library,
    get_logs_dir,
    get_skills_dir,
    get_usage,
)
from .schemas import SkillCreate, SkillSave, row_of, stale_edit_body

# Fixed segments are registered first so '{name}' can never swallow one. There is no
# DELETE on '/api/skills/{name}': the app answers 405 there, and that is deliberate.
router = APIRouter(tags=["learning"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def detail_or_404(library: SkillLibraryService, name: str) -> dict:
    """The detail, or the right refusal — and the containment check for every
    mutation below: `SkillRepository.resolve` proves the name stays inside the
    library, so what follows may hand the raw name to the archive."""
    detail = library.read_skill(name)
    if detail is None:
        raise SkillError("not_found", f"no skill named '{name}'")
    return detail


def set_pinned(usage: Any, name: str, pinned: bool) -> None:
    """`usage.set_pinned` only writes for a skill the sidecar already knows, and
    a bundled skill has no entry until the model first uses one — so a pin would
    silently do nothing on exactly the skills a new owner reaches for first.
    Recording it first costs one honest inaccuracy: `created_at` becomes the moment
    we noticed the directory rather than the moment it appeared. `created_by` does
    not lie — an untracked directory already reads as "user" in the listing.
    """
    if usage.get(name) is None:
        usage.mark_created(name, "user", now_iso())
    usage.set_pinned(name, pinned)


@router.get("/api/skills", operation_id="learning_list_skills")
async def list_skills(
        library: SkillLibraryService = Depends(get_library)) -> JSONResponse:
    return JSONResponse({"skills": library.list_skills()})


@router.post("/api/skills", operation_id="learning_create_skill", status_code=201)
async def create(body: SkillCreate,
                 library: SkillLibraryService = Depends(get_library),
                 invalidate: Callable = Depends(get_invalidate)) -> JSONResponse:
    created = library.create_skill(str(body.name or ""),
                                   str(body.content or ""), now_iso())
    # A new skill's description joins the index in the cached prompt prefix.
    await invalidate()
    return JSONResponse(created, status_code=201)


@router.post("/api/skills/{name}/pin", operation_id="learning_pin_skill")
async def pin(name: str,
              library: SkillLibraryService = Depends(get_library),
              usage: Any = Depends(get_usage)) -> JSONResponse:
    detail_or_404(library, name)
    set_pinned(usage, name, True)
    # No reset: pinning changes no text the model sees, only what the curator and
    # the archive action are allowed to do to it.
    return JSONResponse(row_of(detail_or_404(library, name)))


@router.post("/api/skills/{name}/unpin", operation_id="learning_unpin_skill")
async def unpin(name: str,
                library: SkillLibraryService = Depends(get_library),
                usage: Any = Depends(get_usage)) -> JSONResponse:
    detail_or_404(library, name)
    set_pinned(usage, name, False)
    return JSONResponse(row_of(detail_or_404(library, name)))


@router.post("/api/skills/{name}/archive", operation_id="learning_archive_skill")
async def archive_one(name: str,
                      library: SkillLibraryService = Depends(get_library),
                      invalidate: Callable = Depends(get_invalidate)) -> JSONResponse:
    current = detail_or_404(library, name)
    if current["pinned"]:
        # Pinning protects a skill from a manual archive too, not just the curator.
        raise Conflict(f"'{name}' is pinned; unpin it before archiving", code="pinned")
    if current["state"] == "archived":
        raise SkillError("not_found", f"'{name}' has no live directory to archive")
    library.archive_skill(name, now_iso())
    await invalidate()
    return JSONResponse(row_of(detail_or_404(library, name)))


@router.post("/api/skills/{name}/restore", operation_id="learning_restore_skill")
async def restore_one(name: str,
                      library: SkillLibraryService = Depends(get_library),
                      invalidate: Callable = Depends(get_invalidate)) -> JSONResponse:
    detail_or_404(library, name)
    if not library.restore_skill(name, now_iso()):
        raise Conflict(f"'{name}' is not in .archive/, or a live copy already exists",
                       code="restore_failed")
    await invalidate()
    return JSONResponse(row_of(detail_or_404(library, name)))


@router.get("/api/skills/{name}", operation_id="learning_get_skill")
async def detail(name: str,
                 library: SkillLibraryService = Depends(get_library)) -> JSONResponse:
    return JSONResponse(detail_or_404(library, name))


@router.put("/api/skills/{name}", operation_id="learning_save_skill")
async def save(name: str, body: SkillSave,
               library: SkillLibraryService = Depends(get_library),
               usage: Any = Depends(get_usage),
               skills_dir: Path = Depends(get_skills_dir),
               logs_dir: Path = Depends(get_logs_dir),
               backup_keep: int = Depends(get_backup_keep),
               invalidate: Callable = Depends(get_invalidate)) -> JSONResponse:
    detail_or_404(library, name)           # 404 and containment before any write

    def snapshot() -> None:
        """Runs inside `write_skill`, after the etag and the content are known good
        and before the replace. Snapshotting around the whole call instead would
        charge every refusal — a stale_edit, a bad frontmatter — a slot in a
        keep=N store, and five rejected retries would rotate out the snapshot
        holding the state before the last edit that actually happened."""
        snapshots.snapshot(skills_dir, logs_dir, reason=f"pre-web-edit:{name}",
                           keep=backup_keep, kind=SNAPSHOT_KIND)

    try:
        library.write_skill(name, str(body.content or ""),
                            str(body.etag or ""), before_write=snapshot)
    except StaleEdit as e:
        # Not the shared `ApplicationException` handler: this body carries the current
        # text and etag as well, and that handler only knows `{code, message}`.
        return JSONResponse(stale_edit_body(e.content, e.etag), status_code=409)
    usage.bump_patch(name, now_iso())
    await invalidate()
    # Re-read rather than assemble: the page gets the new etag, the new text and
    # the bumped counter in one round trip, all from the same source of truth.
    return JSONResponse(detail_or_404(library, name))

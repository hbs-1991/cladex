"""Uploads into the lane inbox, and downloads by opaque file id.

Both directions go through `FileService`, so a browser never names a path: an
upload lands in the lane's inbox under a sanitized name, and a download is served
only after the visibility check says the caller may see that lane. Text and
images render in the browser; anything else is sent as an attachment.

Dependencies: `get_files`, `get_lane` — see `dependencies.py`.
The **shell** decides whether this router is mounted (`deps.file_store is not None`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from starlette.responses import FileResponse, JSONResponse

from claude_hermes.core.exceptions import ApplicationException, NotFound

from ..application.service import FileService
from .dependencies import LaneGuard, get_files, get_lane, lane_owner_of

INLINE_PREFIXES = ("image/", "text/", "application/pdf", "application/json")

router = APIRouter(tags=["files"])


@router.post("/api/uploads", operation_id="files_create_upload", status_code=201)
async def upload(thread_id: str = Form(""),
                 file: UploadFile | None = File(None),
                 files: FileService = Depends(get_files),
                 lane: LaneGuard = Depends(get_lane)) -> JSONResponse:
    """A browser upload into the lane's inbox. 201 with the `to_wire` shape.

    `File(None)` rather than `File(...)`: a missing part used to reach the handler and
    be answered `400 bad_request` with its own message, and a Pydantic 422 in its place
    would be a wire break. The lane check runs first, as it did — a foreign lane is a
    404 whether or not the form was well formed.
    """
    t = await lane(thread_id)
    if file is None or not file.filename:
        raise ApplicationException(400, "bad_request", "file is required")
    data = await file.read()
    # The service's own ceiling applies (`web_upload_max_mb`, bound by the
    # composition root): one number, one place.
    rec = await files.put_upload(t["session_key"], file.filename, data,
                                 file.content_type or None,
                                 # The LANE's owner off the thread row, not the
                                 # uploader: `LaneGuard` has already refused a lane
                                 # this user may not post to, and a group lane's
                                 # uploads belong to the deploy owner whoever sent
                                 # them. One rule, written once (D3/D7).
                                 owner_id=lane_owner_of(t))
    return JSONResponse(files.to_wire(rec), status_code=201)


@router.get("/api/files/{id}", operation_id="files_get_file")
async def get_file(id: str,
                   files: FileService = Depends(get_files),
                   lane: LaneGuard = Depends(get_lane)) -> FileResponse:
    """The bytes behind one file id, inline or as an attachment."""
    rec = await files.get(id)
    if rec is None:
        raise NotFound("no such file")
    # 404 unless the lane is visible: a file id must never confirm the
    # existence of someone else's lane.
    await lane(rec["session_key"])
    # `resolve` is awaited now: on the S3 backend it materializes the object into
    # the local mirror first, so `FileResponse` still streams a real file.
    try:
        path = await files.resolve(rec)
    except PermissionError:
        raise NotFound("no such file")
    if not path.is_file():
        raise NotFound("file is gone")
    disposition = "inline" if rec["media_type"].startswith(INLINE_PREFIXES) else "attachment"
    return FileResponse(str(path), media_type=rec["media_type"], filename=rec["name"],
                        content_disposition_type=disposition)

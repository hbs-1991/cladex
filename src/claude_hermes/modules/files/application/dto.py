"""The wire shape of a file record.

`/api/files/{id}` is spelled here as a literal and stays one: the browser, the
SSE `file` event and the Telegram caption all point at the same URL, and it is
an *ownership-checked* endpoint — which is why Wave 3 will not replace it with a
presigned object URL (that would hand out the bytes without the check).
"""
from __future__ import annotations

__all__ = ["to_wire"]


def to_wire(record: dict) -> dict:
    return {
        "file_id": record["id"], "name": record["name"],
        "media_type": record["media_type"], "size": record["size"],
        "url": f"/api/files/{record['id']}",
    }

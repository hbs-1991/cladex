"""What a post of each format must carry before the owner may approve it.

v1 approved whatever was in the row, so an empty post could be scheduled. The gate
is here, pure, so the web approve, the Telegram approve and the drafter's `submit`
refuse the same things in the same words:

* `body` non-empty after strip — every channel publishes text, even under a video;
* media by format (`MEDIA_RULES`): a carousel is 2-10 images, a reel/short/video
  exactly one video, a photo exactly one image; any other format (post, thread,
  story, …) takes media or none.

A file's kind is read from its extension (`domain.media.MEDIA_EXTENSIONS` split in
two); whether the file exists is the approval's hashing, not this table.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
VIDEO_EXTENSIONS = frozenset({"mp4", "mov", "webm"})

IMAGE = "image"
VIDEO = "video"

# format -> (the kind every file must be, fewest, most). A format not listed here
# takes any media, or none.
MEDIA_RULES: dict[str, tuple[str, int, int]] = {
    "carousel": (IMAGE, 2, 10),
    "reel": (VIDEO, 1, 1),
    "short": (VIDEO, 1, 1),
    "video": (VIDEO, 1, 1),
    "photo": (IMAGE, 1, 1),
}


def media_kind(path: str) -> str | None:
    """`"image"`, `"video"`, or None for a path that is neither."""
    suffix = PurePosixPath(str(path or "").replace("\\", "/")).suffix.lower().lstrip(".")
    if suffix in IMAGE_EXTENSIONS:
        return IMAGE
    if suffix in VIDEO_EXTENSIONS:
        return VIDEO
    return None


def _paths(media: Iterable) -> list[str]:
    """Paths out of stored entries (`{"path", …}`) or plain strings alike."""
    out = []
    for entry in media or []:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if path:
            out.append(str(path))
    return out


def _need(kind: str, low: int, high: int) -> str:
    noun = "image" if kind == IMAGE else "video"
    if low == high:
        return f"exactly {low} {noun}{'' if low == 1 else 's'}"
    return f"{low}-{high} {noun}s"


def approval_refusal(body: str | None, fmt: str | None,
                     media: Iterable) -> tuple[str, str] | None:
    """`(code, message)` when this content may not be approved, or None.

    `empty_body` for a blank post; `media_required` naming what the format needs
    and what it has. The message is shown verbatim in the item dialog, in the
    Telegram popup and to the drafter.
    """
    if not str(body or "").strip():
        return "empty_body", "The post has no text: write the body before approving it."
    fmt = str(fmt or "").strip().lower()
    rule = MEDIA_RULES.get(fmt)
    if rule is None:
        return None
    kind, low, high = rule
    paths = _paths(media)
    wrong = [p for p in paths if media_kind(p) != kind]
    if wrong or not low <= len(paths) <= high:
        have = f"{len(paths)} file{'' if len(paths) == 1 else 's'}"
        if wrong:
            have += f", {wrong[0]} is not a{'n' if kind == IMAGE else ''} {kind}"
        return "media_required", (f"A {fmt} needs {_need(kind, low, high)}; this one "
                                  f"has {have}.")
    return None


def card_media_kind(media: Iterable) -> str:
    """How the approval card shows the media: `"video"` for one video, `"none"`
    for no file, `"images"` otherwise (a photo, or a media group)."""
    paths = _paths(media)
    if not paths:
        return "none"
    if len(paths) == 1 and media_kind(paths[0]) == VIDEO:
        return "video"
    return "images"

#!/usr/bin/env python3
"""Extract a generated image from a Codex CLI session rollout JSONL.

argv[1] = output image path to write
argv[2] = a file listing newline-separated paths to new rollout-*.jsonl files

Exit 0 if an image was decoded and written; non-zero otherwise.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re
import sys

IMAGE_MAGIC_PREFIXES: dict[str, str] = {
    "iVBORw0KGgo": "png",   # PNG
    "/9j/": "jpg",          # JPEG
    "UklGR": "webp",        # WEBP
}

MIN_BLOB_LENGTH = 200
BASE64_BLOB_PATTERN = re.compile(r'"([A-Za-z0-9+/=]{' + str(MIN_BLOB_LENGTH) + r',})"')


def find_best_image_blob(session_paths: list[pathlib.Path]) -> tuple[str, str] | None:
    """Return the largest (base64, ext) image payload found across given files."""
    best: tuple[str, str, int] | None = None
    for session_path in session_paths:
        try:
            text = session_path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            flat = json.dumps(obj)
            for match in BASE64_BLOB_PATTERN.finditer(flat):
                blob = match.group(1)
                for magic, ext in IMAGE_MAGIC_PREFIXES.items():
                    if blob.startswith(magic):
                        if best is None or len(blob) > best[2]:
                            best = (blob, ext, len(blob))
    if best is None:
        return None
    return best[0], best[1]


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: extract_image.py <out-path> <new-sessions-list-file>",
              file=sys.stderr)
        return 2
    out_path = pathlib.Path(sys.argv[1])
    list_file = pathlib.Path(sys.argv[2])
    try:
        paths = [pathlib.Path(p) for p in list_file.read_text().splitlines() if p.strip()]
    except OSError as e:
        print(f"cannot read session list: {e}", file=sys.stderr)
        return 2

    found = find_best_image_blob(paths)
    if found is None:
        return 1
    blob, _ext = found
    try:
        data = base64.b64decode(blob)
    except (ValueError, base64.binascii.Error) as e:
        print(f"base64 decode failed: {e}", file=sys.stderr)
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""What a value in `files.path` means.

A stored address is the only thing that survives a restart, a backend swap or a
restore from backup, so its grammar is a domain rule and not a detail of
whichever store happens to be wired:

* `local:<key>`        — the filesystem store, key relative to `agent_home`
* `s3://<bucket>/<key>` — an object in the bucket
* anything else        — a row written before Wave 3a, which held an absolute
  filesystem path. `normalize` reads one as `local:` so those rows keep
  resolving; see the README's compat note.

Pure string work on purpose: `application` may not import `infrastructure`
(the "files layers" contract), and both stores need the same reading of a row.
"""
from __future__ import annotations

__all__ = ["LOCAL_PREFIX", "S3_PREFIX", "normalize", "s3_address", "split", "split_s3"]

LOCAL_PREFIX = "local:"
S3_PREFIX = "s3://"


def normalize(stored: str) -> str:
    """A stored address with its scheme made explicit.

    The compat rule lives here and nowhere else: a bare path is a `local:`
    address. Rows written before Wave 3a hold `<home>/agent_home/outbox/...`,
    and turning them into a miss would lose every file the agent ever sent.
    """
    if stored.startswith(LOCAL_PREFIX) or stored.startswith(S3_PREFIX):
        return stored
    return LOCAL_PREFIX + stored


def split(stored: str) -> tuple[str, str]:
    """`("local", key-or-path)` or `("s3", "bucket/key")`."""
    s = normalize(stored)
    if s.startswith(S3_PREFIX):
        return "s3", s[len(S3_PREFIX):]
    return "local", s[len(LOCAL_PREFIX):]


def split_s3(rest: str) -> tuple[str, str]:
    """`"bucket/inbox/lane/a.png"` -> `("bucket", "inbox/lane/a.png")`."""
    bucket, _, key = rest.partition("/")
    return bucket, key


def s3_address(bucket: str, key: str) -> str:
    return f"{S3_PREFIX}{bucket}/{key}"

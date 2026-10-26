"""One root per owner, for both blob stores.

Before Wave 4a a store froze `blob_root(home)` in `__init__`, which was right
while there was one tree. With a tree per user a frozen root means the write path
and the read path disagree: a file written into user B's `inbox/` is looked up
under the owner's `agent_home`, so `resolve_within` either raises or — worse —
hands the model a path its own `Read` is denied (advisor blocker B4).

So a store takes a `RootLookup` instead: `async (owner_id) -> Path`, asked on
every call with **the row's** `owner_id`, never the requester's and never a
process global. The download route re-resolves against whoever the row says owns
the file, which is what stops a guessed or forged file id reading out of somebody
else's tree.

`fixed(path)` is the other half of the seam: a `Path` where a lookup is expected
(every test, `filestore.LocalFileStore`, and any deployment with exactly one
tree) becomes a lookup that ignores the owner and answers that path. One
representation inside the stores, two spellings at the door.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

__all__ = ["RootLookup", "as_lookup", "fixed"]

RootLookup = Callable[[int], Awaitable[Path]]


def fixed(root: Path) -> RootLookup:
    """A `RootLookup` that answers `root` for every owner."""
    resolved = Path(root).resolve()

    async def lookup(owner_id: int = 0) -> Path:
        return resolved

    return lookup


def as_lookup(roots) -> RootLookup:
    """Accept either spelling at a store's constructor.

    A `Path` (or anything `Path()` swallows) becomes `fixed(...)`; a callable is
    taken as the lookup it already is. This is what lets `LocalBlobStore(tmp_path)`
    keep working in every test while the composition root hands in
    `resolver.mirror_root`.
    """
    if callable(roots):
        return roots
    return fixed(Path(roots))

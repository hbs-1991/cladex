"""`python -m claude_hermes.main drafts list|show <name>|delete <name>`.

The operator's window into the draft queue now that it is a table. Before Wave 3a
`scripts/approve_draft.py` read `catalog-drafts/` straight off the host filesystem;
the queue has moved into PostgreSQL, so the host script reads it *through* the app
(`docker compose exec -T agent …`) and still writes `catalog/` itself. That split is
the security model, not a detail: the agent can write anything it likes into the
queue, and the one thing it cannot do is make a queued manifest installable.

Presentation, not application: this is a channel like the routers and the Telegram
commands, and the only one whose transport is a terminal. `print` is the transport
here — the no-`print` rule exists to keep output out of the *server's* logs, and
these three subcommands have no server.

**The `list` format is a contract.** `scripts/approve_draft.py` parses it, so it is
tab-separated, one record per line, ASCII field names first:

    pending\\t<kind>\\t<name>\\t<label>
    rejected\\t<kind>\\t<name>\\t<reason>

`show` writes the draft's bytes and nothing else — no header, no trailing newline of
its own — because what it prints is copied into `catalog/` verbatim.
"""
from __future__ import annotations

import sys
from typing import TextIO

from ..application.ports import DraftStore
from ..domain.drafts import safe

USAGE = "usage: drafts list | drafts show <name> | drafts delete <name>"

KINDS = ("mcp", "plugin")

__all__ = ["KINDS", "USAGE", "run"]


async def run(argv: list[str], drafts: DraftStore, *,
              out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Dispatch one `drafts` subcommand. Returns the process exit code."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    command = argv[0] if argv else ""
    rest = argv[1:]

    if command == "list":
        return await _list(drafts, out)
    if command in ("show", "delete"):
        if len(rest) != 1:
            print(USAGE, file=err)
            return 2
        if command == "show":
            return await _show(drafts, rest[0], out, err)
        return await _delete(drafts, rest[0], out)

    print(USAGE, file=err)
    return 2


async def _list(drafts: DraftStore, out: TextIO) -> int:
    for m in await drafts.pending():
        # `safe` on the label: it came out of a README an attacker may have
        # written and it is about to be printed on an operator's terminal, where
        # an ANSI escape can redraw the line above it.
        print(f"pending\t{m.kind}\t{m.name}\t{safe(m.label)}", file=out)
    for kind, name, reason in await drafts.rejected():
        # Never silently: an unapprovable draft the operator cannot see is a
        # request that looks filed and will never be answered.
        print(f"rejected\t{kind}\t{name}\t{safe(reason)}", file=out)
    return 0


async def _show(drafts: DraftStore, name: str, out: TextIO, err: TextIO) -> int:
    """The draft's bytes, exactly as submitted. What the host script copies."""
    for kind in KINDS:
        raw = await drafts.read(kind, name)
        if raw is not None:
            out.write(raw)
            return 0
    print(f"no draft named {name!r} is queued", file=err)
    return 1


async def _delete(drafts: DraftStore, name: str, out: TextIO) -> int:
    """Drop the draft under either spelling. The kind is not on the command line
    and a rejected draft may not have a parseable manifest to ask, so both are
    cleared; a DELETE that matches nothing is free."""
    for kind in KINDS:
        await drafts.delete(kind, name)
    print(f"deleted {name}", file=out)
    return 0

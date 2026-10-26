#!/usr/bin/env python
"""Approve a manifest the agent drafted, moving it into the real catalog.

Runs on the HOST, and that is the entire point. The agent runs as the owner of /data
and has Bash, so any file it can write is a file it can forge — including a manifest.
catalog/ is mounted into the container read-only, so the one thing the agent cannot do
is make its own draft installable. This script is that missing step, and a human is
standing in it.

What you are approving is an executable surface: `command` + `args` become a process in
the agent's container, `repo`@`ref` becomes code that is cloned and loaded. The draft was
written from a README or a docs page the agent fetched — text an attacker can author.
Read the args. That is the whole job.

Wave 3a moved the queue itself into PostgreSQL, so the drafts are read *through the
app* now (`docker compose exec -T agent python -m claude_hermes.main drafts …`)
instead of off `data/catalog-drafts/`. What did NOT change is the half that matters:
the write to `catalog/` still happens here, on the host, outside the container. The
container can show this script a draft; it cannot make one real.

    python scripts/approve_draft.py              # list what is pending
    python scripts/approve_draft.py <name>       # review one, then approve it

Set CH_DRAFTS_CMD to run the app some other way — a host install, a different
service name, `kubectl exec`:

    CH_DRAFTS_CMD="python -m claude_hermes.main drafts" python scripts/approve_draft.py
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import yaml  # noqa: E402

from claude_hermes.modules.extensions.domain.drafts import describe, safe  # noqa: E402
from claude_hermes.modules.extensions.domain.manifest import parse  # noqa: E402
from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog  # noqa: E402

CATALOG = REPO_ROOT / "catalog"

# The default assumes the compose deployment this repo ships. `-T` because there is
# no TTY here and the output is parsed; the confirmation prompt is read by THIS
# process, on the host, and never by anything inside the container.
DEFAULT_CMD = "docker compose exec -T agent python -m claude_hermes.main drafts"


def drafts_cmd() -> list[str]:
    return shlex.split(os.environ.get("CH_DRAFTS_CMD") or DEFAULT_CMD)


def run(*args: str) -> tuple[int, str]:
    """One `drafts` subcommand. Returns (exit code, stdout)."""
    proc = subprocess.run([*drafts_cmd(), *args], capture_output=True, text=True)
    if proc.returncode != 0 and proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode, proc.stdout


def listing() -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """`(pending, rejected)`, each `(kind, name, text)`.

    The tab-separated format is the contract stated in
    `modules/extensions/presentation/cli.py`; anything that does not match it is
    dropped rather than guessed at.
    """
    code, out = run("list")
    if code != 0:
        raise SystemExit(f"could not read the draft queue: `{' '.join(drafts_cmd())} list` "
                         f"exited {code}. Is the agent running? Set CH_DRAFTS_CMD to "
                         "reach it another way.")
    pending: list[tuple[str, str, str]] = []
    rejected: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        bucket, kind, name, text = parts
        (pending if bucket == "pending" else rejected if bucket == "rejected"
         else []).append((kind, name, text))
    return pending, rejected


def main() -> int:
    pending, rejected = listing()

    if not pending and not rejected:
        print("No drafts pending.")
        return 0

    if len(sys.argv) < 2:
        if pending:
            print("Pending drafts:\n")
            for kind, name, label in pending:
                # ASCII only: this runs on the operator's console, and a Windows code page
                # turning an em dash into a replacement char is a bad look for the one
                # screen a human is supposed to read carefully.
                print(f"  {name} [{kind}] - {safe(label)}")
            print(f"\nReview one with: python {Path(__file__).name} <name>")
        if rejected:
            # Never silently: an unapprovable draft the operator cannot see is a request
            # that looks filed and will never be answered.
            print("\nRefused by the validator, so NOT approvable (ask the agent to redraft):\n")
            for kind, name, why in rejected:
                print(f"  {name} [{kind}]")
                print(f"      {safe(why)}")
        return 0

    name = sys.argv[1]
    entry = next((e for e in pending if e[1] == name), None)
    if entry is None:
        print(f"No pending draft named {name!r}.")
        return 1
    kind = entry[0]

    # The bytes, not a rendering of them: what is printed below and what is written
    # into catalog/ have to be the same thing, or the review was of something else.
    code, raw = run("show", name)
    if code != 0 or not raw.strip():
        print(f"Could not read the draft {name!r} back.")
        return 1
    m = parse(yaml.safe_load(raw), kind)

    print(f"\n{describe(m)}\n")
    print(f"description: {safe(m.description)}\n")
    print("Approving makes this installable by the agent from chat.")
    if m.kind == "plugin":
        print("It will be cloned and its code loaded into the agent's container.")
    elif m.transport == "stdio":
        print("The command above will RUN as a process in the agent's container.")
    print("The draft was extracted from a page the agent fetched. Read the lines above.")

    if input("\nApprove? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Not approved. The draft is untouched.")
        return 1

    subdir = "plugins" if m.kind == "plugin" else "mcp"
    dest = CATALOG / subdir / f"{m.name}.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(raw, encoding="utf-8")

    # Re-read it through the real loader: what the agent will see must parse, or we have
    # just approved something that silently is not there.
    if catalog.get(m.name) is None:
        dest.unlink(missing_ok=True)
        print(f"REFUSED: {dest} did not load back through the catalog. Nothing was added.")
        return 1

    run("delete", name)
    print(f"\nApproved -> {dest.relative_to(REPO_ROOT)}")
    # No restart: catalog/ is a live bind mount and load_all() re-reads it on every
    # call, so the running agent sees this entry on its next turn.
    print(f"It is live now. In chat: install {m.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

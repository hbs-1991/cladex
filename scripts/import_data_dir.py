#!/usr/bin/env python
"""One-shot: carry a pre-Wave-3a `data/` directory into PostgreSQL.

Wave 3a moved three things off the volume and into tables — `secrets.env` into
`extension_secrets` (Fernet-sealed), `catalog-drafts/` into `catalog_drafts`, and
`pending-approvals/` into `approval_snapshots`. An existing deployment has files
there and nothing reads them any more, so this is the migration that is not an
Alembic migration: it needs the encryption key, which schema migrations do not have.

    export CH_SECRETS_KEY=...            # the key the app will run with
    export CH_DATABASE_URL=...           # or pass --database-url
    python scripts/import_data_dir.py --home ./data

Idempotent: every write is an upsert keyed the way the table is, so running it
twice changes nothing. Run it with the agent stopped, then delete the three paths
by hand once you have checked the counts — this script never deletes anything,
because a one-way migration that also destroys its input has no second attempt.

Drafts are carried across **verbatim, including ones that no longer validate**: a
draft filed under a laxer validator is a request a human still owes an answer to,
and dropping it here would turn it into one that was never filed. It lands with
`status='rejected'` and the validator's own words as the reason.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
import yaml  # noqa: E402

from claude_hermes.core.crypto import SecretBox  # noqa: E402
from claude_hermes.core.db.engine import make_engine  # noqa: E402
from claude_hermes.modules.extensions.domain.drafts import kind_of  # noqa: E402
from claude_hermes.modules.extensions.domain.exceptions import (  # noqa: E402
    DraftError, ManifestError)
from claude_hermes.modules.extensions.domain.manifest import parse  # noqa: E402
from claude_hermes.modules.extensions.infrastructure.repositories.pg_drafts import (  # noqa: E402
    PgDrafts)
from claude_hermes.modules.extensions.infrastructure.repositories.pg_secrets import (  # noqa: E402
    PgSecrets)
from claude_hermes.modules.extensions.infrastructure.repositories.pg_snapshots import (  # noqa: E402
    PgSnapshots)

SECRETS_FILE = "secrets.env"
DRAFTS_DIR = "catalog-drafts"
SNAPSHOTS_DIR = "pending-approvals"

# The two directory spellings the file stores used, and the `kind` each one means.
SUBDIRS = (("mcp", "mcp"), ("plugin", "plugins"))


def read_env_file(path: Path) -> dict[str, str]:
    """`secrets.env` exactly as `EnvFileSecrets.load` read it — values may contain
    '=' and the file may carry comments."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        out[name.strip()] = value
    return out


async def deploy_owner(engine) -> int | None:
    """The owner as a `users` row, or None while nobody has claimed the deploy.

    Wave 5 made `extension_secrets` per user, so a write has to name whose secret
    it is. A pre-Wave-3a `secrets.env` was the deployment's one set of
    credentials and only an owner could install anything that read them, so they
    are the owner's — the same answer migration `0006` gives the rows it
    backfills.
    """
    async with engine.connect() as conn:
        return await conn.scalar(
            sa.text("SELECT id FROM users WHERE role = 'owner'"))


async def import_secrets(home: Path, secrets: PgSecrets, *,
                         owner_id: int | None) -> list[str]:
    """Names only in the return value. A secret's value is never printed, logged or
    returned by anything here — this script runs on an operator's terminal.

    Refuses rather than guesses when there is something to carry and no owner to
    carry it to: an ownerless credential belongs to nobody and therefore to
    everybody, which is the one thing Wave 5 exists to prevent.
    """
    values = sorted(read_env_file(home / SECRETS_FILE).items())
    if values and owner_id is None:
        raise RuntimeError(
            f"{home / SECRETS_FILE} holds {len(values)} secret(s) but this deploy "
            "has no owner in `users`. Claim the deploy first — since Wave 5 a "
            "stored secret names whose it is, and these are the owner's.")
    done: list[str] = []
    for name, value in values:
        await secrets.put(name, value, owner_id=owner_id)
        done.append(name)
    return done


async def import_drafts(home: Path, drafts: PgDrafts) -> list[tuple[str, str, str]]:
    """`(kind, name, status)` per draft carried across."""
    done: list[tuple[str, str, str]] = []
    for kind, subdir in SUBDIRS:
        directory = home / DRAFTS_DIR / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            raw = path.read_text(encoding="utf-8")
            name, status, reason = path.stem, "pending", None
            try:
                data = yaml.safe_load(raw)
                if not isinstance(data, dict):
                    raise DraftError("a manifest must be a YAML mapping")
                m = parse(data, kind_of(data))
                name = m.name
            except (yaml.YAMLError, DraftError, ManifestError) as e:
                status, reason = "rejected", str(e)
            await drafts.put_raw(kind, name, raw, status=status, reason=reason)
            done.append((kind, name, status))
    return done


async def import_snapshots(home: Path, snapshots: PgSnapshots) -> list[tuple[str, str]]:
    """The bytes verbatim: an approval token is `sha256(raw)[:8]`, so re-serialising
    here would silently invalidate every token an owner is already holding."""
    done: list[tuple[str, str]] = []
    for kind, subdir in SUBDIRS:
        directory = home / SNAPSHOTS_DIR / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            await snapshots.write(kind, path.stem, path.read_bytes())
            done.append((kind, path.stem))
    return done


async def import_all(home: Path, engine, box: SecretBox) -> dict[str, list]:
    owner_id = await deploy_owner(engine)
    return {
        "secrets": await import_secrets(home, PgSecrets(engine, box),
                                        owner_id=owner_id),
        "drafts": await import_drafts(home, PgDrafts(engine)),
        "snapshots": await import_snapshots(home, PgSnapshots(engine)),
    }


async def _main(args: argparse.Namespace) -> int:
    home = Path(args.home).resolve()
    if not home.is_dir():
        print(f"no such directory: {home}", file=sys.stderr)
        return 1
    url = args.database_url or os.environ.get("CH_DATABASE_URL", "")
    if not url:
        print("set CH_DATABASE_URL or pass --database-url", file=sys.stderr)
        return 1

    box = SecretBox.from_env()          # raises SecretsKeyError with the how-to
    engine = make_engine(url)
    try:
        result = await import_all(home, engine, box)
    finally:
        await engine.dispose()

    print(f"secrets:   {len(result['secrets'])}"
          + (f" ({', '.join(result['secrets'])})" if result["secrets"] else ""))
    for kind, name, status in result["drafts"]:
        print(f"draft:     {name} [{kind}] {status}")
    for kind, name in result["snapshots"]:
        print(f"snapshot:  {name} [{kind}]")
    print(f"\nNothing was deleted. Once the counts look right, remove "
          f"{home / SECRETS_FILE}, {home / DRAFTS_DIR} and {home / SNAPSHOTS_DIR}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--home", default=str(REPO_ROOT / "data"),
                    help="the workspace directory (default: ./data)")
    ap.add_argument("--database-url", default="",
                    help="SQLAlchemy async URL (default: $CH_DATABASE_URL)")
    return asyncio.run(_main(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

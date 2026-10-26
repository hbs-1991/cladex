"""`scripts/import_data_dir.py` — the one-shot that carries a pre-Wave-3a `data/`
directory into PostgreSQL.

This is the migration Alembic cannot be: it needs `CH_SECRETS_KEY`, which a schema
migration does not have. What it must not do is lose anything — a draft that the
validator has since tightened against is still a request a human owes an answer to,
and a snapshot's bytes are what an outstanding approval token was computed over.
"""
import sys
from pathlib import Path

import pytest
import yaml

from claude_hermes.core.crypto import SecretBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import import_data_dir as importer  # noqa: E402

from conftest import ensure_users  # noqa: E402
from tests.ext_helpers import drafts_for, snapshots_for  # noqa: E402

STDIO = {
    "manifest_version": 1, "name": "someserver", "label": "Some Server",
    "description": "Does a thing.", "version": "1.0.0", "transport": "stdio",
    "command": "npx", "args": ["-y", "@vendor/mcp-someserver"], "scopes": ["turn"],
}
PLUGIN = {
    "manifest_version": 1, "name": "someplugin", "label": "Some Plugin",
    "description": "Does a thing.", "version": "1.0.0",
    "repo": "https://github.com/owner/repo", "ref": "v1.2.0", "scopes": ["turn"],
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def legacy_home(tmp_path: Path) -> Path:
    """What a deployment's `data/` looked like before Wave 3a."""
    home = tmp_path / "data"
    write(home / "secrets.env",
          "# a comment\n\nEXAMPLE_API_KEY=abc123\nTOKEN=a=b=c\n")
    write(home / "catalog-drafts" / "mcp" / "someserver.yaml", yaml.safe_dump(STDIO))
    write(home / "catalog-drafts" / "plugins" / "someplugin.yaml", yaml.safe_dump(PLUGIN))
    # Filed when `ref: main` was still allowed.
    write(home / "catalog-drafts" / "plugins" / "stale.yaml",
          yaml.safe_dump({**PLUGIN, "name": "stale", "ref": "main"}))
    write(home / "pending-approvals" / "mcp" / "someserver.yaml", yaml.safe_dump(STDIO))
    return home


@pytest.fixture
def box() -> SecretBox:
    return SecretBox(SecretBox.generate_key())


async def test_secrets_land_sealed_and_readable(tmp_path, box, engine):
    from claude_hermes.modules.extensions.infrastructure.repositories.pg_secrets import (
        PgSecrets,
    )

    # The legacy `secrets.env` was the deployment's one set of credentials, so
    # the importer files them under the deploy owner (Wave 5).
    await ensure_users(engine, owner=1)
    await importer.import_all(legacy_home(tmp_path), engine, box)
    assert await PgSecrets(engine, box).load(owner_id=1) == {
        "EXAMPLE_API_KEY": "abc123",
        # The env-file rules the old store had: comments and blank lines are
        # skipped, and a value may contain '='.
        "TOKEN": "a=b=c",
    }


async def test_drafts_come_across_and_the_unapprovable_one_is_not_dropped(tmp_path, box,
                                                                        engine):
    await ensure_users(engine, owner=1)
    await importer.import_all(legacy_home(tmp_path), engine, box)
    drafts = drafts_for(engine)

    assert sorted(m.name for m in await drafts.pending()) == ["someplugin", "someserver"]
    (kind, name, reason), = await drafts.rejected()
    assert (kind, name) == ("plugin", "stale") and "ref" in reason


async def test_a_draft_keeps_its_exact_bytes(tmp_path, box, engine):
    """The approval token is sha256 of these bytes. Re-serialising here would
    invalidate every token an owner is already holding."""
    await ensure_users(engine, owner=1)
    home = legacy_home(tmp_path)
    await importer.import_all(home, engine, box)
    on_disk = (home / "catalog-drafts" / "mcp" / "someserver.yaml").read_text(encoding="utf-8")
    assert await drafts_for(engine).read("mcp", "someserver") == on_disk


async def test_snapshots_come_across_byte_for_byte(tmp_path, box, engine):
    from claude_hermes.modules.extensions.domain.approvals import token_of

    await ensure_users(engine, owner=1)
    home = legacy_home(tmp_path)
    await importer.import_all(home, engine, box)
    raw = (home / "pending-approvals" / "mcp" / "someserver.yaml").read_bytes()
    stored = await snapshots_for(engine).read("mcp", "someserver")
    assert stored == raw and token_of(stored) == token_of(raw)


async def test_running_it_twice_changes_nothing(tmp_path, box, engine):
    await ensure_users(engine, owner=1)
    home = legacy_home(tmp_path)
    first = await importer.import_all(home, engine, box)
    second = await importer.import_all(home, engine, box)
    assert first == second
    assert len(await drafts_for(engine).pending()) == 2


async def test_an_empty_home_imports_nothing_and_does_not_fail(tmp_path, box, engine):
    result = await importer.import_all(tmp_path, engine, box)
    assert result == {"secrets": [], "drafts": [], "snapshots": []}


async def test_nothing_on_the_volume_is_deleted(tmp_path, box, engine):
    """A one-way migration that destroys its input has no second attempt. The
    operator removes the three paths by hand, after checking the counts."""
    await ensure_users(engine, owner=1)
    home = legacy_home(tmp_path)
    listing = sorted(p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())
    await importer.import_all(home, engine, box)
    assert listing == sorted(
        p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file())

"""Extension secrets as Fernet-sealed rows in `extension_secrets` — the Wave 3a
`SecretStore`.

What this replaces: a 0600 `secrets.env` on the /data volume. 0600 kept it away
from other *users*; it kept it away from nothing that ran as us, and the agent's
own Bash runs as us. A row here is ciphertext, and `CH_SECRETS_KEY` lives in the
environment and is popped before anything can spawn the CLI — so a shell inside
the container, a stolen volume snapshot and a stolen database dump are each,
separately, not a stolen credential.

The pure half (`interpolate`, `missing`, `SecretNameError`) is re-exported here from
the domain, so a caller of the store has one import.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from claude_hermes.core.crypto import SecretBox, SecretDecryptError

from ...domain.exceptions import SecretNameError
from ...domain.models import ExtensionSecret
from ...domain.secrets import (SECRETS_FILENAME, _NAME_RE, _VAR_RE, check_name,  # noqa: F401
                               check_value, interpolate, missing)

log = structlog.get_logger(__name__)

__all__ = ["SECRETS_FILENAME", "PgSecrets", "SecretNameError", "check_name",
           "check_value", "interpolate", "missing"]


class PgSecrets:
    """The `SecretStore` port over `extension_secrets`.

    Takes an engine and a `SecretBox`, never a key string: the key is read once,
    at the composition root, from an environment snapshot taken before the pop.
    Nothing below this line ever sees it.
    """

    def __init__(self, engine: AsyncEngine, box: SecretBox):
        self._engine = engine
        self._box = box

    async def load(self, *, owner_id: int) -> dict[str, str]:
        """This owner's secrets, decrypted, as the plain mapping `server_spec` wants.

        A row that does not open is skipped rather than fatal: one secret sealed
        under a rotated key must cost that one extension, not every extension.
        """
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(ExtensionSecret)
                .where(ExtensionSecret.owner_id == owner_id)
                .order_by(ExtensionSecret.name))).mappings().all()
        out: dict[str, str] = {}
        for row in rows:
            try:
                out[row["name"]] = self._box.open(row["ciphertext"])
            except SecretDecryptError:
                # Deliberately the name only: this is the one place the key and
                # the ciphertext are both in scope.
                log.warning("stored secret %s does not open under the current key",
                            row["name"])
        return out

    async def names(self, *, owner_id: int) -> dict[str, str]:
        """This owner's stored names with the ISO timestamp each was last written at.

        The box is never opened here: listing what is set is not a reason to have
        plaintext in scope, and it is the one read that still answers under a
        rotated key.
        """
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(ExtensionSecret.name, ExtensionSecret.updated_at)
                .where(ExtensionSecret.owner_id == owner_id)
                .order_by(ExtensionSecret.name))).all()
        return {name: updated_at for name, updated_at in rows}

    async def put(self, name: str, value: str, *, owner_id: int) -> None:
        """Upsert one secret for one owner. The name and value rules are unchanged —
        an env-var name, no newline — because `server_spec` still hands these to a
        process environment."""
        check_name(name)
        check_value(value)
        now = datetime.now(timezone.utc).isoformat()
        stmt = pg_insert(ExtensionSecret).values(
            owner_id=owner_id, name=name, ciphertext=self._box.seal(value),
            updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ExtensionSecret.owner_id, ExtensionSecret.name],
            set_={"ciphertext": stmt.excluded.ciphertext, "updated_at": now})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def forget(self, name: str, *, owner_id: int) -> None:
        """Remove one owner's secret; absent is fine."""
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(ExtensionSecret)
                .where(ExtensionSecret.owner_id == owner_id)
                .where(ExtensionSecret.name == name))

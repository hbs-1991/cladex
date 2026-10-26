"""The draft queue as rows in `catalog_drafts` — the Wave 3a `DraftStore`.

What this replaces: `CH_HOME/catalog-drafts/*.yaml`. The *boundary* is unchanged
and is still the only thing that matters: `CatalogReader` cannot see a draft, and
this port has no `promote`. What changes is where the queue lives, so that the
host-side approver (`scripts/approve_draft.py`) reads it through the app instead
of off a volume the agent can write with Bash.

The pure half (`kind_of`, `safe`, `describe`) is re-exported here from the domain,
so a caller of the queue has one import.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
import yaml
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ...domain.drafts import DRAFTS_SUBDIR, describe, kind_of, safe
from ...domain.exceptions import DraftError, ManifestError
from ...domain.manifest import Manifest, parse
from ...domain.models import CatalogDraft

log = structlog.get_logger(__name__)

PENDING = "pending"
REJECTED = "rejected"

__all__ = ["DRAFTS_SUBDIR", "PENDING", "REJECTED", "DraftError", "PgDrafts",
           "describe", "kind_of", "safe"]


class PgDrafts:
    """The `DraftStore` port over `catalog_drafts`.

    No `promote`: proposing and enacting are different capabilities, and keeping
    them behind different objects is what makes "the agent may write here" safe
    to say.
    """

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def save(self, raw: str, *, owner_id: int | None = None) -> Manifest:
        """Validate a drafted manifest and queue it for human approval.

        `owner_id` (Wave 4a) records WHO proposed this, and it is audit only: the
        queue itself stays deploy-wide, because approving a draft makes something
        runnable in this container and that is the owner's decision and nobody
        else's (D8). Per-user extensions are a 4b decision.

        Runs the SAME validator the real catalog runs (`manifest.parse`), so the
        agent finds out here — while it can still fix it — that its args are a
        shell string, its URL is not https, or it used a ${VAR} it never declared.
        A draft that would not have been installable is not worth a human's
        attention.

        The row keeps `raw` as submitted rather than a re-serialisation: the
        approval token is bound to these bytes, and a round-trip through
        `yaml.safe_dump` would quietly change them.
        """
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise DraftError(f"not valid YAML: {e}") from e
        if not isinstance(data, dict):
            raise DraftError("a manifest must be a YAML mapping")

        m = parse(data, kind_of(data))              # raises ManifestError

        # Normalised once, here, so every draft in the queue is the canonical
        # dump of what parsed — the shape the file store wrote, and the shape
        # `describe` was read against.
        body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        await self.put_raw(m.kind, m.name, body, owner_id=owner_id)
        log.info("drafted %s manifest %s for approval", m.kind, m.name)
        return m

    async def put_raw(self, kind: str, name: str, raw: str, *,
                      status: str = PENDING, reason: str | None = None,
                      owner_id: int | None = None) -> None:
        """Write a row without validating it.

        Two callers only, and both are honest about it: `save` (which has just
        validated) and `scripts/import_data_dir.py`, which must carry across a
        draft filed under an older, laxer validator — dropping it silently would
        turn a queued request into one that was never filed.
        """
        now = datetime.now(timezone.utc).isoformat()
        stmt = pg_insert(CatalogDraft).values(
            kind=kind, name=name, raw=raw, status=status, reason=reason,
            owner_id=owner_id, created_at=now)
        # `owner_id` is COALESCEd rather than overwritten: `_reject` re-puts a row
        # it read back without one, and a rejection must not erase who proposed the
        # draft — that is the whole value of the column.
        stmt = stmt.on_conflict_do_update(
            index_elements=[CatalogDraft.kind, CatalogDraft.name],
            set_={"raw": stmt.excluded.raw, "status": stmt.excluded.status,
                  "reason": stmt.excluded.reason, "created_at": now,
                  "owner_id": func.coalesce(stmt.excluded.owner_id,
                                            CatalogDraft.owner_id)})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def pending(self) -> list[Manifest]:
        """Every queued draft that still parses, oldest name first. One that no
        longer parses is skipped rather than raised on — the same rule the real
        catalog follows, and `rejected()` is where it surfaces."""
        out: list[Manifest] = []
        for row in await self._rows():
            if row["status"] == REJECTED:
                continue
            try:
                out.append(parse(yaml.safe_load(row["raw"]), row["kind"]))
            except (yaml.YAMLError, ManifestError):
                # No traceback: `rejected()` reports these properly, and a stack
                # trace on the operator's approval screen buries the one line
                # that matters.
                log.warning("skipping unreadable draft %s", row["name"])
        return out

    async def rejected(self) -> list[tuple[str, str, str]]:
        """`(kind, name, reason)` for every draft that cannot be approved.

        `pending` drops these silently, which is right for the agent but wrong
        for the operator: a draft that just vanishes from the queue looks like
        nothing was ever filed, and the human is left waiting for something that
        will never arrive.

        Re-validating on read is what makes a row become `status='rejected'`:
        the validator tightened after the draft was filed (a floating `ref`, say),
        and the row records why so the agent can be told to redraft.
        """
        out: list[tuple[str, str, str]] = []
        for row in await self._rows():
            if row["status"] == REJECTED:
                out.append((row["kind"], row["name"], row["reason"] or ""))
                continue
            try:
                parse(yaml.safe_load(row["raw"]), row["kind"])
            except (yaml.YAMLError, ManifestError) as e:
                await self._reject(row["kind"], row["name"], str(e))
                out.append((row["kind"], row["name"], str(e)))
        return out

    async def read(self, kind: str, name: str) -> str | None:
        """The draft exactly as submitted, or None. What `ApprovalService.begin`
        snapshots and what `drafts show` prints on the operator's terminal."""
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(CatalogDraft.raw)
                .where(CatalogDraft.kind == kind, CatalogDraft.name == name)
            )).first()
        return row[0] if row else None

    async def delete(self, kind: str, name: str) -> None:
        """Drop one draft; absent is fine (both kinds are cleared on a discard,
        so the miss has to be free)."""
        async with self._engine.begin() as conn:
            await conn.execute(delete(CatalogDraft).where(
                CatalogDraft.kind == kind, CatalogDraft.name == name))

    async def _rows(self) -> list[dict]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(CatalogDraft).order_by(CatalogDraft.name))).mappings().all()
        return [dict(r) for r in rows]

    async def _reject(self, kind: str, name: str, reason: str) -> None:
        await self.put_raw(kind, name, await self.read(kind, name) or "",
                           status=REJECTED, reason=reason)

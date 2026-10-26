"""`meta` keys per user: one store, one suffix, the owner's keys untouched.

Everything the loop and the curator remember between processes — the two nudge
counters, `curator_last_run_at`, `curator_paused`, `curator_last_report_path`,
`curator_last_dry_run_at`, `learning_review_until` — is a `meta` row named by a
literal in `domain/constants.py`. Wave 4b runs all of it per user, and the
cheapest correct way to get N sets of those rows is to append `:<user_id>` to
every key on the way in.

The owner gets `suffix=""`. That is not a convenience: the seven legacy keys are
a wire contract with the operator (`curator_paused` is set by hand, `skill_admin
status` reads three of them by literal) and with `scripts/import_sqlite.py`,
which copies the whole table. With the empty suffix the owner's rows are
byte-identical to 4a's, there is no migration, and a non-owner's rows ride along
in the same table for free.

One instance per scope, handed to all four readers (`NudgeTracker`,
`MetaReviewGate`, `SkillAdminService`, `CuratorService`) — advisor B-6. Four
separately built instances would agree today, and the day one of them is built
with the wrong suffix a user's `curator_paused:7` would pause the owner's pass.
"""
from __future__ import annotations

from claude_hermes.modules.learning.application.ports import MetaStore


class ScopedMeta:
    """`MetaStore` over another `MetaStore`, with every key suffixed."""

    def __init__(self, meta: MetaStore, suffix: str = "") -> None:
        self._meta = meta
        self._suffix = suffix

    @property
    def suffix(self) -> str:
        return self._suffix

    def key(self, key: str) -> str:
        """The row a logical key lands on. Exposed so a test — or an operator
        reading the table — can name the row without re-deriving the rule."""
        return f"{key}{self._suffix}"

    async def get(self, key: str) -> str | None:
        return await self._meta.get(self.key(key))

    async def set(self, key: str, value: str) -> None:
        await self._meta.set(self.key(key), value)

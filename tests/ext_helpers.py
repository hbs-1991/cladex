"""DB-backed extension stores, for the tests that used to write files under `tmp_path`.

Wave 3a moved extension secrets, the draft queue and the approval snapshots into
PostgreSQL, so a test that wants one needs an engine and the process's `SecretBox`.
Every test already has a database (`tests/conftest.py` gives each pytest process its
own, and the `engine` fixture a pool over it), so this is a three-line factory over a
bare engine rather than a fixture.
"""
from __future__ import annotations

from claude_hermes.modules.extensions.application.services.approval_service import (
    ApprovalService,
)
from claude_hermes.modules.extensions.infrastructure.repositories.pg_drafts import (
    PgDrafts,
)
from claude_hermes.modules.extensions.infrastructure.repositories.pg_secrets import (
    PgSecrets,
)
from claude_hermes.modules.extensions.infrastructure.repositories.pg_snapshots import (
    PgSnapshots,
)
from claude_hermes.modules.extensions.infrastructure.secret_key import secret_box

__all__ = ["approvals_for", "drafts_for", "secrets_for", "snapshots_for"]


def secrets_for(engine) -> PgSecrets:
    """The `SecretStore`, sealed with the box every store in this process shares —
    two boxes would not open each other's rows."""
    return PgSecrets(engine, secret_box())


def drafts_for(engine) -> PgDrafts:
    return PgDrafts(engine)


def snapshots_for(engine) -> PgSnapshots:
    return PgSnapshots(engine)


def approvals_for(engine) -> ApprovalService:
    return ApprovalService(drafts=drafts_for(engine), snapshots=snapshots_for(engine))

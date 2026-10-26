"""The two commands the gateway intercepts before the model can see them.

Both bodies moved verbatim from `gateway/telegram.py` in Wave 2; the
`home: Path` + `extensions.{secrets,approvals,catalog,usercatalog}` imports became
`SecretStorePort`, `ApprovalPort`, `CatalogPort` and `CatalogWriterPort`. The
security reasoning that put them here at all lives on the call sites in
`routing.py` — it explains *why* they never reach `run_turn`.
"""
from __future__ import annotations

import structlog

from ...domain.exceptions import SecretNameError
from ...domain.messages import (
    _APPROVE_DENIED,
    _APPROVE_NO_DRAFT,
    _APPROVE_SHADOW,
    _APPROVE_STALE,
    _APPROVE_UNVERIFIED,
    _APPROVE_USAGE,
    _SECRET_DENIED,
    _SECRET_USAGE,
)
from ...domain.models import MessageEvent
from ..ports import (
    AclPort,
    ApprovalPort,
    CatalogPort,
    CatalogWriterPort,
    SecretStorePort,
    TurnRunnerPort,
)

log = structlog.get_logger(__name__)


class CommandService:
    """`/secret` and `/approve`. Owner-gated, never forwarded to the model."""

    def __init__(self, *, acl: AclPort, secrets: SecretStorePort,
                 approvals: ApprovalPort, catalog: CatalogPort,
                 runner: TurnRunnerPort, writer: CatalogWriterPort | None = None):
        self._acl = acl
        self._secrets = secrets
        self._approvals = approvals
        self._catalog = catalog
        self._runner = runner
        self._writer = writer

    async def handle_secret(self, rest: str, event: MessageEvent, *,
                            delete_message=None) -> str:
        """Store one secret in the extension secrets table, Fernet-sealed. The
        message carrying it is deleted whatever happens — even a refused one may
        hold a real key."""
        if delete_message is not None:
            try:
                await delete_message()
            except Exception:
                log.warning("could not delete the message carrying a secret",
                            exc_info=True)

        if not await self._acl.is_owner(event.user_id):
            return _SECRET_DENIED

        name, sep, value = rest.strip().partition("=")
        if not sep or not value:
            return _SECRET_USAGE
        try:
            # The sender's own row. The `is_owner` gate above means this is the
            # deploy owner's id today, but passing it — rather than letting the
            # store default to somebody — is what keeps `/secret` honest if the
            # gate ever widens: a secret is sealed for whoever typed it.
            await self._secrets.put(name.strip(), value, owner_id=event.user_id)
        except SecretNameError:
            return _SECRET_USAGE
        return f"Saved {name.strip()}. The message with it has been deleted."

    async def handle_approve(self, rest: str, event: MessageEvent, *,
                             session_key: str) -> str:
        """Two steps. `/approve <name>` shows the executable surface and a content-bound
        token; `/approve <name> <token>` promotes the snapshot the human just read."""
        name, _, token = rest.strip().partition(" ")
        name, token = name.strip(), token.strip()
        if not name:
            return _APPROVE_USAGE
        if not await self._acl.is_owner(event.user_id):
            return _APPROVE_DENIED
        if self._catalog.is_repo_entry(name):
            return _APPROVE_SHADOW.format(name=name)

        if not token:
            started = await self._approvals.begin(name)
            if started is None:
                return _APPROVE_NO_DRAFT.format(name=name)
            _, surface, tok = started
            return (f"About to add this to your catalog:\n\n{surface}\n\n"
                    f"To confirm, send:  /approve {name} {tok}")

        result = await self._approvals.confirm(name, token)
        if result is None:
            return _APPROVE_STALE.format(name=name)
        m, raw = result
        self._writer.promote(m, raw)
        # P5: don't claim it landed until the reader can actually see it. The writer and
        # catalog.load_all() resolve the user-catalog through one function, so this should
        # never fire — but "should never" is exactly what the last silent failure relied on,
        # and the cost of being wrong here is telling the owner an entry is theirs when
        # nothing can install it. The draft stays queued so a retry is free.
        if self._catalog.get(m.name) is None:
            return _APPROVE_UNVERIFIED.format(name=name)
        await self._approvals.clear(m.kind, name)
        # The model never saw this exchange — the lane it would resume still believes the
        # draft is queued and installing is not its step. A fresh lane re-reads the persona
        # and the catalog. Approvals are rare; one cold prefix is the whole cost. Limit: this
        # resets the lane /approve was typed in, which is the drafting thread in normal use.
        await self._runner.reset(session_key)
        return (f"'{m.label}' is now in your catalog. Say 'add {name}' and I'll install it "
                "(you may need to send its secret first). This thread starts fresh on "
                "your next message.")

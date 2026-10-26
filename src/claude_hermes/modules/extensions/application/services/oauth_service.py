"""Keep a connector's mirrored access token valid, or say the owner must sign in again."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from ...domain.manifest import Manifest
from ...domain.oauth import NeedsReauth, OAuthError, REFRESH_MARGIN, iso
from ..ports import OAuthWire, RefreshLocks, SecretStore, TokenRepository


class _ProcessLocks:
    """The default `RefreshLocks`: one `asyncio.Lock` per connector.

    Was a module global until Wave 3a, which is why it is worth a name now — the
    lock has to be shared across `OAuthService` instances (`for_scope` builds one
    for `turn` and one for `cron`), and a default argument evaluated per instance
    would silently stop locking anything. The composition root passes a redis
    implementation instead when there is more than one process to coordinate.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())


_DEFAULT_LOCKS = _ProcessLocks()


class OAuthService:
    """Provides the `OAuthRefresher` port `ComposeService` consumes.

    The wire work arrives as a factory rather than an import: the layer contract keeps
    httpx out of `application/`, and the seam is also what lets a test hand in a
    MockTransport-backed client.
    """

    def __init__(self, *, wire, secrets: SecretStore | None = None,
                 locks: RefreshLocks | None = None):
        # `wire` is a zero-arg factory returning an async context manager that yields
        # an OAuthWire — one client per refresh, closed on the way out.
        self._wire = wire
        self._secrets = secrets
        self._locks = locks if locks is not None else _DEFAULT_LOCKS

    async def ensure_fresh(self, home: Path, tokens: TokenRepository, m: Manifest,
                           *, owner_id: int, now: float | None = None) -> None:
        """Before a server is composed: make sure THIS OWNER's mirrored access token
        is valid, refreshing it when it expires within REFRESH_MARGIN. Raises
        NeedsReauth when they have to sign in again; never returns a half-updated
        state.

        One lock per connector, and the row is read inside it: `for_scope` runs for
        `turn` and `cron` concurrently, and a provider that rotates refresh tokens
        (Notion does) refuses the second use of the same one — the loser would record
        `needs_reauth` for a connector that had just refreshed.

        The lock name carries the owner: two people refreshing one connector are
        refreshing two different grants, and making them queue behind each other
        would be a self-inflicted serialization. The `RefreshLocks` port is
        unchanged — the name is the caller's to compose."""
        entry = next(iter(m.oauth_entries()), None)
        if entry is None:
            return
        now = time.time() if now is None else now
        async with self._locks(f"{owner_id}:{m.name}"):
            row = await tokens.get(m.name, owner_id=owner_id)
            if row is None or not row["access_token"]:
                raise NeedsReauth("not signed in")
            if not row["expires_at"]:
                return                               # the server did not say; assume valid
            if datetime.fromisoformat(row["expires_at"]).timestamp() - now > REFRESH_MARGIN:
                return                               # the other caller refreshed while we waited
            if not row["refresh_token"]:
                raise NeedsReauth("the token expired and the server issued no refresh token")
            async with self._wire() as wire:
                try:
                    disc = await wire.discover_issuer(row["issuer"], resource=m.url)
                    ts = await wire.refresh(disc, refresh_token=row["refresh_token"],
                                            client_id=row["client_id"],
                                            client_secret=row["client_secret"], now=now)
                except OAuthError as e:
                    raise NeedsReauth(e.message) from e
            await tokens.put(m.name, owner_id=owner_id, now=iso(now),
                             access_token=ts.access_token,
                             refresh_token=ts.refresh_token or row["refresh_token"],
                             expires_at=ts.expires_at, scope=ts.scope or row["scope"])
            if self._secrets is not None:
                await self._secrets.put(entry.name, ts.access_token, owner_id=owner_id)


__all__ = ["OAuthService", "OAuthWire"]

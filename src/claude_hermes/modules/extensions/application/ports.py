"""What the extension use cases need from the outside world, stated as Protocols.

Consumer-side: every port here is named for the capability the service wants, never
for the adapter that happens to provide it today. `infrastructure/` satisfies them
with a file, a git clone, a SQL table or an httpx call; `wiring.py` does the matching.
Structural typing, so nothing has to inherit anything.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Protocol

from ..domain.manifest import Manifest
from ..domain.oauth import Discovery, Pending, TokenSet
from ..domain.plugin_surface import BundledServer
from ..domain.probe import ProbeResult


class CatalogReader(Protocol):
    """The reviewed catalog overlaid with the chat-approved half.

    Security invariant: a reader NEVER looks in the drafts directory. A draft is inert
    until a human promotes it, and that is enforced by this port having no way to see
    one, not by a check somewhere downstream.
    """

    def load_all(self, root: Path | None = None) -> dict[str, Manifest]: ...
    def get(self, name: str, root: Path | None = None) -> Manifest | None: ...
    def is_repo_entry(self, name: str) -> bool: ...


class CatalogWriter(Protocol):
    """How an approved manifest becomes a catalog entry. The S2 implementation writes a
    file; a future S1 implementation ships the bytes to an isolated approver sidecar that
    owns the only writable mount. The /approve handler depends on THIS, never on a path."""

    def promote(self, m: Manifest, raw: bytes) -> None: ...


class DraftStore(Protocol):
    """The queue the agent may write to. Deliberately has no `promote`: proposing and
    enacting are different capabilities and must not live behind one object.

    Wave 3a: async, and `home` is gone. The queue is a table now, so there is no
    directory to name and nothing to hand a caller that it could open itself —
    `read` returns the bytes, and that is the only way to see a draft.

    Wave 4a: `save` records the proposer (`catalog_drafts.owner_id`). Audit only —
    the queue stays deploy-wide and approval stays the owner's, because an install
    changes the prompt prefix and the MCP servers every turn composes (D8). There
    is deliberately no `owner_id` on the reads: a queue partitioned by proposer
    would let one user hide a draft from the person who has to approve it.
    """

    async def save(self, raw: str, *, owner_id: int | None = None) -> Manifest: ...
    async def pending(self) -> list[Manifest]: ...
    async def rejected(self) -> list[tuple[str, str, str]]: ...   # (kind, name, reason)
    async def read(self, kind: str, name: str) -> str | None: ...
    async def delete(self, kind: str, name: str) -> None: ...


class SnapshotStore(Protocol):
    """The bytes a human was shown, held between `/approve <name>` and `/approve <name>
    <token>`. What `confirm` promotes is the snapshot, never the live draft."""

    async def write(self, kind: str, name: str, raw: bytes) -> None: ...
    async def read(self, kind: str, name: str) -> bytes | None: ...
    async def clear(self, kind: str, name: str) -> None: ...


class SecretStore(Protocol):
    """Extension credentials, Fernet-sealed in `extension_secrets` (spec req. 22).

    Async since Wave 3a: the adapter is a database round-trip, not a file read.
    `load` returns plaintext — it is what `server_spec` interpolates into a header
    or a child environment — so nothing that holds this port may log its result.
    `names` is the half a caller that only has to *list* what is stored wants:
    name -> `updated_at`, no plaintext in scope at all.

    Per owner since Wave 5: `name` is the env-var name a manifest declares, so
    two users' `NOTION_TOKEN` are two secrets. `owner_id` is required and
    keyword-only everywhere — a call that does not say whose secret it means must
    fail, never fall back to the deploy owner's.
    """

    async def load(self, *, owner_id: int) -> dict[str, str]: ...
    async def names(self, *, owner_id: int) -> dict[str, str]: ...
    async def put(self, name: str, value: str, *, owner_id: int) -> None: ...
    async def forget(self, name: str, *, owner_id: int) -> None: ...


class PluginInstaller(Protocol):
    """Fetching and deleting a plugin's clone."""

    async def clone(self, m: Manifest, timeout: float = 120.0) -> Path: ...
    def remove(self, name: str) -> None: ...


class PluginReader(Protocol):
    """What a clone on disk actually adds. Never raises — a junk plugin.json is still
    a plugin whose files a human should be able to see."""

    def components(self, root: Path) -> dict | None: ...
    def bundled_servers(self, root: Path) -> list[BundledServer]: ...
    def local_servers(self, root: Path) -> list[BundledServer]: ...
    def surface(self, m: Manifest, root: Path) -> str: ...
    def is_present(self, home: Path, m: Manifest) -> bool: ...


class ServerProber(Protocol):
    """Bring one MCP server up and ask the CLI what happened. Never raises."""

    async def __call__(self, home: Path, name: str, spec: dict,
                       timeout: float = 60.0) -> ProbeResult: ...


class PendingStateStore(Protocol):
    """Started OAuth flows waiting for their callback.

    Async since Wave 3a so the redis adapter fits: in redis mode the flow survives
    a restart (and a callback that lands on a different process) for ten minutes;
    inline mode keeps the in-memory dict, where it does not.
    """

    async def issue(self, name: str, owner_id: int, verifier: str, now: float) -> str: ...
    async def take(self, state: str, owner_id: int, now: float) -> Pending | None: ...


class RefreshLocks(Protocol):
    """"Hold the lock for this connector while I refresh its token."

    A factory, not a lock: the name is per connector and the object has to be an
    async context manager the caller can `async with`. `asyncio.Lock` satisfies it
    as-is in inline mode; `infrastructure.refresh_locks.RedisLocks` satisfies it
    across processes.
    """

    def __call__(self, name: str) -> AbstractAsyncContextManager: ...


class ExtensionRepository(Protocol):
    """What is installed for ONE owner — never what it does. The spec lives in the
    catalog.

    `owners_of` is the one deliberately unscoped read: it answers ids and nothing
    else, for the shared-clone cascade, and must never be used to show one user
    another's state.
    """

    async def add(self, name: str, kind: str, catalog_version: str, installed_by: int,
                  now: str, tool_count: int | None = None,
                  tool_names: list[str] | None = None, *, owner_id: int) -> None: ...
    async def get(self, name: str, *, owner_id: int) -> dict | None: ...
    async def list(self, kind: str | None = None, *, owner_id: int) -> list[dict]: ...
    async def enabled(self, *, owner_id: int) -> list[dict]: ...
    async def set_state(self, name: str, state: str, *, owner_id: int) -> None: ...
    async def set_error(self, name: str, error: str | None, *, owner_id: int) -> None: ...
    async def set_tools(self, name: str, names: list[str], *, owner_id: int) -> None: ...
    async def remove(self, name: str, *, owner_id: int) -> None: ...
    async def owners_of(self, name: str) -> list[int]: ...


class TokenRepository(Protocol):
    """OAuth tokens for remote MCP servers — the source of truth. Values never logged.

    Per owner since Wave 5: two people who sign into one connector hold two
    different grants.
    """

    async def get(self, name: str, *, owner_id: int) -> dict | None: ...
    async def put(self, name: str, *, owner_id: int, now: str, **fields: str) -> None: ...
    async def delete(self, name: str, *, owner_id: int) -> None: ...


class OAuthWire(Protocol):
    """The HTTP half of OAuth 2.1, kept out of the application layer because it is
    httpx (and the layer contract forbids httpx here for exactly this reason)."""

    async def discover_issuer(self, issuer: str, resource: str = "",
                              scopes: tuple[str, ...] = ()) -> Discovery: ...
    async def refresh(self, disc: Discovery, *, refresh_token: str, client_id: str,
                      client_secret: str, now: float) -> TokenSet: ...


class OAuthRefresher(Protocol):
    """"Make this owner's mirrored access token valid, or say they must sign in
    again." Consumed by `ComposeService`; provided by `OAuthService`."""

    async def ensure_fresh(self, home: Path, tokens: TokenRepository, m: Manifest,
                           *, owner_id: int, now: float | None = None) -> None: ...


class PromptCacheInvalidator(Protocol):
    """Reset ONE owner's lanes' cached prompt prefix.

    Per owner since Wave 5: a connector one user enables changes only that user's
    prefix, and resetting the deployment would cost everybody else their cache
    for somebody else's decision.

    Ordering rule, not a detail: this fires AFTER the registry row or the catalog entry
    is written, never before — a lane that rebinds in between would resurrect the stale
    session. Wave 2 keeps it awaited in-process; `core.events` carries it in Wave 3.
    """

    async def __call__(self, owner_id: int) -> None: ...


class ExtensionsQuery(Protocol):
    """What another module may ask about installed extensions without reaching into
    this one. `web/commands.py` (turns) is the consumer: it needs the roots, not the
    registry."""

    async def enabled_plugin_roots(self, *, owner_id: int) -> list[tuple[str, Path]]: ...


class AccessCheck(Protocol):
    """"Is this user the owner?" — everything that changes what the agent IS is
    owner-only, and fails closed when there is nobody to identify."""

    async def is_owner(self, user_id: int) -> bool: ...


Row = Mapping

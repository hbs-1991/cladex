"""The `ext_manage` use case — what the agent may do to its own extensions.

Every string this returns is part of the prompt the model reads back, so they move
byte-for-byte from `tools.py`: the guidance about relaying `/secret` verbatim, about
never repeating a stale missing-secret list, and about approval being the user's step
is the behaviour, not decoration (`tests/test_ext_manage_guidance.py` asserts it).

The owner gate arrives already resolved as `is_owner` and defaults to False: without a
turn context there is nobody to identify, so there is nobody to authorize.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path

from ...domain.drafts import describe as _describe
from ...domain.exceptions import DraftError, ManifestError
from ...domain.secrets import missing
from ..ports import (CatalogReader, DraftStore, ExtensionRepository, PluginInstaller,
                     PromptCacheInvalidator, SecretStore, ServerProber)
from .extension_service import ExtensionService

_DENIED = ("Only the owner may install or change extensions. Your own connectors "
           "are enabled and signed in on the Connectors page in the web app.")


class ExtensionAdminService:
    def __init__(self, home: Path, *, catalog: CatalogReader, store: ExtensionRepository,
                 drafts: DraftStore, secrets: SecretStore, extensions: ExtensionService,
                 installer: PluginInstaller, probe_fn: ServerProber,
                 probe_timeout: float = 60.0, clone_timeout: float = 120.0,
                 catalog_root: Path | None = None,
                 on_change: PromptCacheInvalidator | None = None):
        self._home = home
        self._catalog = catalog
        self._store = store
        self._drafts = drafts
        self._secrets = secrets
        self._extensions = extensions
        self._installer = installer
        self._probe_fn = probe_fn
        self._probe_timeout = probe_timeout
        self._clone_timeout = clone_timeout
        self._catalog_root = catalog_root
        self._on_change = on_change

    def with_on_change(self, on_change: PromptCacheInvalidator) -> "ExtensionAdminService":
        """A shallow clone whose reset notification goes to `on_change`.

        The web channel wants one process-wide invalidator, so `on_change` is a
        constructor argument. The agent tool wants the signal per CALL — it turns
        it into `ctx.flags.reset_lane` on the one lane, applied after the session
        bind — and one turn's flag must not leak into another lane's. A clone
        shares every collaborator and differs only in where the signal goes.
        """
        clone = copy.copy(self)
        clone._on_change = on_change
        return clone

    async def _flag_reset(self, owner_id: int) -> None:
        if self._on_change is not None:
            await self._on_change(owner_id)

    async def handle(self, action: str = "list", *, name: str = "",
                     kind: str | None = None, manifest: str = "",
                     user_id: int = 0, is_owner: bool = False,
                     owner_id: int) -> str:
        """`owner_id` is the LANE's owner — whose installs `list` reports and whose
        row a mutation would touch. `user_id` stays the sender, for the audit
        column on a draft. They differ only on a group lane, where every mutation
        is refused below anyway."""
        name = (name or "").strip()
        kind_filter = kind or None
        entries = self._catalog.load_all(self._catalog_root)

        if action == "catalog":
            wanted = [m for m in entries.values()
                      if kind_filter is None or m.kind == kind_filter]
            if not wanted:
                return "The catalog is empty."
            return "\n".join(
                f"{m.name} [{m.kind}] {m.label} — {m.description.strip()}"
                for m in sorted(wanted, key=lambda m: m.name)
            )

        if action == "list":
            rows = await self._store.list(kind_filter, owner_id=owner_id)
            if not rows:
                return "Nothing installed. Use action='catalog' to see what's available."
            return "\n".join(
                f"{r['name']} [{r['kind']}] {r['state']} v{r['catalog_version']}"
                + (f" — last error: {r['last_error']}" if r["last_error"] else "")
                for r in rows
            )

        if action == "drafts":
            waiting = await self._drafts.pending()
            if not waiting:
                return "No drafts are waiting for approval."
            return "\n".join(
                f"{m.name} [{m.kind}] {m.label} — waiting for the owner to approve it "
                f"in chat: /approve {m.name}; once approved, install it with "
                "action='add'"
                for m in waiting
            )

        if not name:
            return f"Action '{action}' needs a name."

        # Everything below CHANGES what the agent is. Owner only — an allowlisted user
        # may talk to the agent, but may not re-arm it. Fails CLOSED: without an acl or
        # a turn context there is nobody to identify, so there is nobody to authorize.
        if not is_owner:
            return _DENIED

        if action == "draft":
            # The agent may PROPOSE a manifest; it may not enact one. The draft lands in
            # a queue on /data that nothing installs from — catalog/ is mounted read-only,
            # so making a draft real is a step only a human on the host can take. This is
            # the load-bearing asymmetry: the manifest was extracted from a README or a
            # docs page, i.e. from text an attacker may have written, and `args` becomes a
            # process in this container.
            raw = manifest or ""
            if not raw.strip():
                return (
                    "Pass the whole manifest as YAML in 'manifest'. Required: "
                    "manifest_version, name, label, description, version, scopes; then "
                    "either transport (+ url, or command + args) for an MCP server, or "
                    "repo + a pinned ref for a plugin."
                )
            try:
                # Who proposed it, for the audit column. `user_id` and not the lane
                # owner: this tool is owner-gated (`ExtensionAdminTool` fails
                # closed), so the two are the same person here, and the honest
                # answer to "who typed this" is the sender.
                m = await self._drafts.save(raw, owner_id=user_id or None)
            except (DraftError, ManifestError) as e:
                return f"That is not a valid manifest, so it was not saved: {e}"
            if m.name != name:
                await self._drafts.delete(m.kind, m.name)
                return (
                    f"The manifest names itself '{m.name}' but you passed name='{name}'. "
                    "Nothing was saved — say which one you mean."
                )
            return (
                f"Drafted '{m.name}' [{m.kind}]. It is NOT installed and NOT installable "
                f"yet.\n\n{_describe(m)}\n\n"
                "Tell the user, verbatim, that approving it is their step and you cannot "
                "do it for them — they send this command in this chat:\n"
                f"    /approve {m.name}\n"
                "It shows them the draft and a confirm token; a second "
                f"'/approve {m.name} <token>' makes it final.\n"
                "Once they approve, the entry appears in action='catalog' immediately — "
                "no restart, no rebuild — and this thread's context resets. Installing "
                "it is then YOUR step: action='add'.\n"
                "Tell them to read the command/args (or repo/ref) above before approving: "
                "that is what will run in your container, and you extracted it from a page "
                "someone else wrote."
            )

        if action in ("enable", "disable"):
            changed = await (self._extensions.enable if action == "enable"
                             else self._extensions.disable)(name, owner_id=owner_id)
            if not changed:
                return f"'{name}' is not installed."
            await self._flag_reset(owner_id)
            return (
                f"{'Enabled' if action == 'enable' else 'Disabled'} '{name}'. "
                "This thread's context starts fresh on your next message."
            )

        if action == "remove":
            # The clone goes with the row here, because this branch is reachable
            # only by the owner: the gate above refused everybody else.
            if not await self._extensions.remove(name, entries.get(name),
                                                 owner_id=owner_id,
                                                 remove_clone=True):
                return f"'{name}' is not installed."
            await self._flag_reset(owner_id)
            return (
                f"Removed '{name}'. This thread's context starts fresh on your next message."
            )

        m = entries.get(name)
        if m is None:
            return (
                f"'{name}' is not in the catalog, so it cannot be installed. "
                "Use action='catalog' to see what is available. Servers are installed "
                "only from reviewed manifests — a command or URL from chat is refused "
                "by design."
            )

        secret_values = await self._secrets.load(owner_id=owner_id)
        absent = missing(m.plain_secret_names(), secret_values)
        if absent:
            wanted = [a for a in m.auth if a.name in absent]
            lines = [f"'{m.label}' needs {len(absent)} secret(s). Send each one as its own "
                     f"message — it is stored without ever reaching me:"]
            for a in wanted:
                lines.append(f"  /secret {a.name}=<value>")
                lines.append(f"     {a.prompt}" + (f" — {a.url}" if a.url else ""))
            lines.append("Then ask me to add it again.")
            return "\n".join(lines)

        if action == "test" or action == "add":
            if m.kind == "plugin":
                if action == "test":
                    return (
                        f"'{name}' is a plugin — nothing to probe. Use action='add'."
                    )
                try:
                    await self._installer.clone(m, timeout=self._clone_timeout)
                except Exception as e:                           # noqa: BLE001
                    return f"Could not install '{name}': {e}"
                await self._store.add(m.name, m.kind, m.version, installed_by=user_id,
                                      now=datetime.now(timezone.utc).isoformat(),
                                      owner_id=owner_id)
                await self._flag_reset(owner_id)
                return (
                    f"Installed plugin '{m.label}' ({m.repo}@{m.ref}"
                    + (f", subdir {m.subdir}" if m.subdir else "") + "). "
                    "This thread's context starts fresh on your next message."
                )

            out = await self._extensions.connect(
                m, user_id=user_id, now=datetime.now(timezone.utc).isoformat(),
                timeout=self._probe_timeout, probe_fn=self._probe_fn,
                install=action == "add", owner_id=owner_id)
            if out.code == "oauth_required":
                return (
                    f"'{m.label}' signs in with OAuth. Open the web app → Customize → "
                    "Connectors and press Connect there; a token cannot be typed in chat."
                )
            if out.code == "compose_failed":
                return f"Cannot build '{name}': {out.message}"
            if not out.ok:
                return (
                    f"'{m.label}' did not come up: {out.message}. Nothing was installed."
                )
            if action == "test":
                return (
                    f"'{m.label}' connects and offers {len(out.tools)} tools: "
                    f"{', '.join(out.tools)}. It is not installed — use action='add'."
                )
            await self._flag_reset(owner_id)
            return (
                f"Connected '{m.label}' — {len(out.tools)} tools "
                f"({', '.join(out.tools)}). This thread's context starts fresh on "
                "your next message; the tools are available from then on."
            )

        return f"Unknown action '{action}'."

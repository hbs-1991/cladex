"""The wire shapes the SPA reads. Frozen (spec req. 36): field names, ordering of the
`state` vocabulary and the error `code` strings are contract, not implementation.

A secret never surfaces: `{"name", "prompt", "set": bool}` and nothing else.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body
from pydantic import BaseModel, ConfigDict

from ..application.ports import PluginReader
from ..domain.drafts import safe
from ..domain.exceptions import ComposeError
from ..domain.manifest import Manifest
from ..domain.paths import plugin_root
from ..domain.registry import auth_kind, state_of, tool_names


def connector_to_wire(m: Manifest, row: Mapping | None, secret_values: Mapping[str, str],
                      *, source: str) -> dict:
    """The connector as the SPA reads it. Never a secret value — only whether it is set."""
    return {
        "name": m.name, "label": m.label, "description": m.description.strip(),
        "source": source, "transport": m.transport, "auth": auth_kind(m),
        "secrets": [{"name": a.name, "prompt": a.prompt, "set": a.name in secret_values}
                    for a in m.auth if a.kind == "secret"],
        "state": state_of(row),
        "tools": row.get("tool_count") if row else None,
        "tool_names": tool_names(row),
        "last_error": row["last_error"] if row else None,
        "scopes": list(m.scopes),
        "installed_at": row["installed_at"] if row else None,
        "endpoint": m.url if m.transport != "stdio" else " ".join([m.command, *m.args]),
    }


def plugin_components(home: Path, m: Manifest, reader: PluginReader) -> dict | None:
    try:
        return reader.components(plugin_root(home, m))
    except (ComposeError, OSError):
        # A subdir that resolves outside the clone: not "absent", but there is
        # nothing here we would ever load, so the reader is told the same thing.
        return None


def plugin_to_wire(m: Manifest, row: Mapping | None, home: Path, *,
                   reader: PluginReader, source: str) -> dict:
    """The plugin as the SPA reads it. `state` is `state_of` with `connected` spelled
    `installed` — a plugin is not dialled, it is on disk."""
    state = state_of(row)
    present = reader.is_present(home, m)
    return {
        "name": m.name, "label": m.label, "description": m.description.strip(),
        "source": source,
        "repo": m.repo, "ref": m.ref, "subdir": m.subdir, "version": m.version,
        "scopes": list(m.scopes),
        "state": "installed" if state == "connected" else state,
        "present": present,
        "last_error": row["last_error"] if row else None,
        "installed_at": row["installed_at"] if row else None,
        # Only an installed plugin gets components: a clone left behind by a failed
        # remove is not something the tab should describe as installable content.
        "components": plugin_components(home, m, reader) if row is not None and present else None,
    }


def orphan_plugin_wire(row: Mapping) -> dict:
    """A `kind: plugin` row whose manifest is no longer in the catalog.

    Nothing else in the listing shows a row without a manifest, and this one must:
    `for_scope` already refuses to load it, and Remove — which needs the row to be on
    screen — is the only way out. Everything the manifest would have said is blank
    because the manifest is gone; `source` is "custom" because claiming "catalog" would
    claim a review that nothing can now point at.
    """
    return {
        "name": row["name"], "label": row["name"], "description": "",
        "source": "custom", "repo": "", "ref": "", "subdir": "",
        "version": row["catalog_version"] or "", "scopes": [],
        "state": "error", "present": False,
        "last_error": row["last_error"] or "no longer in the catalog",
        "installed_at": row["installed_at"], "components": None,
    }


def draft_to_wire(m: Manifest) -> dict:
    """A queued draft as the SPA reads it.

    `label` and `description` were extracted from a README somebody else wrote, so
    they go through `drafts.safe` — the same strip the terminal surface gets.
    """
    return {
        "name": m.name, "kind": m.kind,
        "label": safe(m.label), "description": safe(m.description),
        "transport": m.transport or None,
        "repo": m.repo or None, "ref": m.ref or None,
    }


def secrets_to_wire(stored: Mapping[str, str], manifests: Iterable[Manifest]) -> list[dict]:
    """The secrets list: what is stored, plus what the catalog declares and nobody
    has set yet. `stored` is name -> `updated_at`, so no value is in scope here.

    `used_by` names every extension that declares the variable, oauth entries
    included — an access token mirrored into the table is still that connector's
    credential, and a row saying so is why deleting it is an informed choice. An
    *unset* oauth name is left out of the list entirely: it is obtained by signing
    in, and offering it beside the ones a person types would invite the wrong move.
    """
    used: dict[str, list[str]] = {}
    declared: set[str] = set()
    for m in manifests:
        for a in m.auth:
            used.setdefault(a.name, []).append(m.label)
            if a.kind == "secret":
                declared.add(a.name)
    return [{"name": name,
             "set": name in stored,
             "updated_at": stored.get(name),
             "used_by": sorted(used.get(name, []), key=str.lower)}
            for name in sorted(set(stored) | declared)]


# --- Request bodies -----------------------------------------------------------
#
# The only place in this module where Pydantic may appear (spec req. 6).
#
# Three of the five bodies here are NOT models, and that is the leniency rule, not
# laziness: `POST /api/connectors/{name}/secrets` is a free-form map of secret names,
# and the drafts/plugins handlers each open with their own `isinstance(body, dict)`
# branch answering a code the SPA reads (`bad_manifest`, or a silently empty token
# that becomes `not_fetched` / `stale_draft`). A `BaseModel` parameter would answer
# `400 bad_request` from the shell's validation handler instead, replacing a code a
# frozen wire contract names. `RawJsonBody` keeps the parsed body exactly as
# `await request.json()` handed it over, and the handler keeps its check verbatim.


RawJsonBody = Annotated[Any, Body()]
"""The whole JSON body, unvalidated — what `await request.json()` used to return."""


class ConnectorCreate(BaseModel):
    """The Add-a-connector form (`POST /api/connectors`).

    Every field is `Any` with the default the old `body.get(key, default)` used, because
    `custom_manifest` and the handler do their own `str(...)`/`strip()` and answer
    `400 bad_manifest` — the code the dialog branches on. Typing `name: str` here would
    turn `{"name": null}` into a `400 bad_request`, which is a different error for the
    same request. `catalog.parse` is the validator; this model only carries the fields.
    """

    model_config = ConfigDict(extra="ignore")

    name: Any = ""
    label: Any = ""
    description: Any = ""
    auth: Any = "none"
    transport: Any = "http"
    url: Any = ""
    header: Any = ""
    token: Any = None
    client_id: Any = None
    client_secret: Any = None


class SecretValue(BaseModel):
    """The one field `PUT /api/secrets/{name}` takes.

    A model, unlike the connector form next to it: this body has no code of its own
    to answer with, so the shell's `400 bad_request` for a malformed one is exactly
    the error the dialog already renders. `Any` rather than `str` keeps that single
    code for `{"value": 7}` too — the handler stringifies and applies the domain's
    own rule (no newline), which is what a process environment can actually carry.
    """

    model_config = ConfigDict(extra="ignore")

    value: Any = ""

"""Manifest parsing and validation — the security rules of this module, and pure.

Every regex and allowlist below is a domain invariant: `parse` is the one gate a
manifest passes before anything it names is executed or dialled, and the same
function runs on a catalog entry, a draft and a browser-authored connector so the
three channels cannot drift. Moved verbatim from `extensions/catalog.py` in Wave 2;
the filesystem half (`_scan`, `load_all`, `get`, `is_repo_entry`) is infrastructure
and lives in `infrastructure/catalog_repository.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .exceptions import ManifestError

MANIFEST_VERSION = 1
VALID_TRANSPORTS = frozenset({"stdio", "http", "sse"})
VALID_SCOPES = ("turn", "cron")
# An stdio server is a command executed in the agent's container. Only package
# runners that take a list of args — never a shell, which would make the args a
# script.
STDIO_COMMANDS = frozenset({"npx", "uvx", "python"})

_VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
# A name is not just a label: it is the registry's primary key and the directory a
# plugin is cloned into and rmtree'd from. Manifests are reviewed, but the one place
# that can assert the name is a name — not a path traversal — is here.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# A ref must be IMMUTABLE, not merely present. 'main' is a pin the way a bookmark is a
# pin: you review the code, you approve it, and the author pushes again — now something
# you never read is running in the container, and the review that authorized it happened
# once, in the past, against different code. A full commit sha cannot move. A release tag
# can be force-moved, but doing so is loud and rare, so we take it; a branch name is not
# a version and is refused.
_REF_RE = re.compile(r"^(?:[0-9a-f]{40}|v?\d+(?:\.\d+)*[A-Za-z0-9.\-+_]*)$")
_MOVING_REFS = frozenset({"main", "master", "head", "trunk", "develop", "latest"})
# A subdir is joined onto the clone and handed to the SDK as a plugin root. Each segment
# must start with an alphanumeric, which kills '..' traversal, '-flag' option injection and
# '.hidden' in one rule; no leading/trailing slash, no drive letter, no empty segment. This
# is the RULE. It cannot see symlinks — those do not exist until checkout — so install and
# compose additionally verify containment against the resolved clone.
_SUBDIR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$", re.IGNORECASE)
_MCP_KEYS = {"manifest_version", "name", "label", "description", "version",
             "transport", "url", "command", "args", "env", "auth", "scopes"}
_PLUGIN_KEYS = {"manifest_version", "name", "label", "description", "version",
                "repo", "ref", "subdir", "auth", "scopes"}
_AUTH_KEYS = {"name", "prompt", "url", "header", "format", "kind"}
VALID_AUTH_KINDS = frozenset({"secret", "oauth"})


@dataclass(frozen=True)
class Auth:
    name: str
    prompt: str = ""
    url: str = ""
    header: str = ""
    format: str = ""
    kind: str = "secret"        # 'secret': typed by a person; 'oauth': obtained by signing in


@dataclass(frozen=True)
class Manifest:
    name: str
    kind: str                    # 'mcp' | 'plugin'
    label: str
    description: str
    version: str
    scopes: tuple[str, ...]
    auth: tuple[Auth, ...] = ()
    transport: str = ""
    url: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    repo: str = ""
    ref: str = ""
    subdir: str = ""             # "" → the repo root is the plugin

    def secret_names(self) -> list[str]:
        return [a.name for a in self.auth]

    def oauth_entries(self) -> tuple[Auth, ...]:
        return tuple(a for a in self.auth if a.kind == "oauth")

    def plain_secret_names(self) -> list[str]:
        """The secrets a person types; oauth tokens are obtained by signing in."""
        return [a.name for a in self.auth if a.kind == "secret"]


def _require(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{where}: '{key}' is required and must be a non-empty string")
    return value


def _require_ref(data: dict, where: str) -> str:
    ref = _require(data, "ref", where)
    if ref.lower() in _MOVING_REFS or not _REF_RE.match(ref):
        raise ManifestError(
            f"{where}: 'ref' must be a release tag (v1.2.3) or a full 40-character commit "
            f"sha, not {ref!r} - a branch moves after you have reviewed it, and the code "
            f"that then runs is code nobody approved"
        )
    return ref


def _require_repo(data: dict, where: str) -> str:
    """`repo` becomes argv for git, so it is held to the same standard as an MCP url.

    Without this, 'ext::sh -c "id"' parses — git's ext:: transport is arbitrary command
    execution, and the only thing refusing it is git's own default protocol policy, which
    is somebody else's rule and could be relaxed by a config we do not control.
    '--upload-pack=...' parses too, and git consumes it as an option rather than a
    repository. And 'http://' would mean the pinned, reviewed code arrives over a channel
    anyone on the path can rewrite.
    """
    repo = _require(data, "repo", where)
    if not repo.startswith("https://"):
        raise ManifestError(
            f"{where}: 'repo' must be an https:// URL, got {repo!r} — it is passed to "
            f"git, where a scheme like ext:: or a leading '-' is a command, not an address"
        )
    return repo


def _parse_subdir(data: dict, where: str) -> str:
    """Which directory inside the repo is the plugin. Absent → the repo root is."""
    raw = data.get("subdir")
    if raw is None:
        return ""
    if not isinstance(raw, str) or not _SUBDIR_RE.match(raw):
        raise ManifestError(
            f"{where}: 'subdir' must be a plain relative path inside the repo "
            f"(e.g. 'plugins/plugin-dev'), got {raw!r}"
        )
    return raw


def valid_name(name: str) -> bool:
    """The name rule, reusable by a caller that did not come through `parse`.

    A route takes its name from a URL, not from a reviewed manifest, and it then hands
    that name to code that builds a path out of it. The registry key and the manifest
    already agree on this shape, so checking it costs a well-formed request nothing.
    """
    return bool(_NAME_RE.match(name))


def _require_name(data: dict, where: str) -> str:
    name = _require(data, "name", where)
    if not _NAME_RE.match(name):
        raise ManifestError(
            f"{where}: 'name' must be lowercase letters, digits, '_' or '-' "
            f"(it becomes a directory name), got {name!r}"
        )
    return name


def _parse_auth(raw, where: str) -> tuple[Auth, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ManifestError(f"{where}: 'auth' must be a list")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ManifestError(f"{where}: each 'auth' entry must be a mapping")
        unknown = set(item) - _AUTH_KEYS
        if unknown:
            raise ManifestError(f"{where}: unknown key(s) in auth: {sorted(unknown)}")
        kind = item.get("kind", "secret")
        if kind not in VALID_AUTH_KINDS:
            raise ManifestError(
                f"{where}: auth kind must be one of {sorted(VALID_AUTH_KINDS)}, got {kind!r}")
        name = _require(item, "name", where)
        if kind == "oauth":
            # The token is minted by an authorization server and mirrored into
            # secrets.env by the OAuth module — nobody types it, so there is nothing
            # to prompt for, and it always travels as a bearer token.
            if "prompt" in item:
                raise ManifestError(f"{where}: an oauth auth entry takes no 'prompt'")
            if item.get("header") != "Authorization" or item.get("format") != f"Bearer ${{{name}}}":
                raise ManifestError(
                    f"{where}: an oauth auth entry must use header 'Authorization' and "
                    f"format 'Bearer ${{{name}}}'")
            prompt = ""
        else:
            prompt = _require(item, "prompt", where)
        out.append(Auth(name=name, prompt=prompt, url=item.get("url", ""),
                        header=item.get("header", ""), format=item.get("format", ""),
                        kind=kind))
    return tuple(out)


def _parse_scopes(raw, where: str) -> tuple[str, ...]:
    if raw is None:
        return ("turn",)
    if not isinstance(raw, list) or not raw:
        raise ManifestError(f"{where}: 'scopes' must be a non-empty list")
    for s in raw:
        if s not in VALID_SCOPES:
            raise ManifestError(
                f"{where}: unknown scope {s!r}; extensions reach the turn and cron only"
            )
    return tuple(raw)


def _check_declared_vars(m_env: dict, url: str, args: tuple, auth: tuple[Auth, ...],
                         where: str) -> None:
    declared = {a.name for a in auth}
    haystack = [url, *args, *m_env.values(), *(a.format for a in auth)]
    for text in haystack:
        for var in _VAR_RE.findall(str(text)):
            if var not in declared:
                raise ManifestError(
                    f"{where}: ${{{var}}} is used but not declared in 'auth'"
                )


def parse(data: dict, kind: str) -> Manifest:
    """Validate one manifest. Every rejection here is a rejection of something that
    would otherwise have been executed or dialled."""
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a mapping")
    name = data.get("name", "<unnamed>")
    where = f"{kind}/{name}"

    if data.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError(
            f"{where}: manifest_version must be {MANIFEST_VERSION}, "
            f"got {data.get('manifest_version')!r}"
        )
    allowed = _MCP_KEYS if kind == "mcp" else _PLUGIN_KEYS
    unknown = set(data) - allowed
    if unknown:
        raise ManifestError(f"{where}: unknown key(s): {sorted(unknown)}")

    common = dict(
        name=_require_name(data, where),
        kind=kind,
        label=_require(data, "label", where),
        description=_require(data, "description", where),
        version=_require(data, "version", where),
        scopes=_parse_scopes(data.get("scopes"), where),
        auth=_parse_auth(data.get("auth"), where),
    )

    if kind == "plugin":
        m = Manifest(
            **common,
            repo=_require_repo(data, where),
            ref=_require_ref(data, where),
            subdir=_parse_subdir(data, where),
        )
        _check_declared_vars({}, "", (), m.auth, where)
        return m

    transport = _require(data, "transport", where)
    if transport not in VALID_TRANSPORTS:
        raise ManifestError(
            f"{where}: unknown transport {transport!r}; expected one of "
            f"{sorted(VALID_TRANSPORTS)}"
        )

    if transport == "stdio":
        command = _require(data, "command", where)
        if command not in STDIO_COMMANDS:
            raise ManifestError(
                f"{where}: command {command!r} is not in the allowlist "
                f"{sorted(STDIO_COMMANDS)} — an stdio server runs in the agent's container"
            )
        args = data.get("args", [])
        if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
            raise ManifestError(
                f"{where}: 'args' must be a list of strings, never a shell string"
            )
        env = data.get("env", {}) or {}
        if not isinstance(env, dict):
            raise ManifestError(f"{where}: 'env' must be a mapping")
        if any(a.kind == "oauth" for a in common["auth"]):
            raise ManifestError(f"{where}: oauth auth needs an http or sse transport")
        m = Manifest(**common, transport=transport, command=command,
                     args=tuple(args), env=dict(env))
        _check_declared_vars(m.env, "", m.args, m.auth, where)
        return m

    url = _require(data, "url", where)
    if not url.startswith("https://"):
        raise ManifestError(f"{where}: 'url' must be https, got {url!r}")
    m = Manifest(**common, transport=transport, url=url)
    _check_declared_vars({}, m.url, (), m.auth, where)
    return m

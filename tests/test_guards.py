"""The read/write confinement a turn runs under (Wave 4a, D6).

`path_guard_factory` is the only thing standing between one user's turn and
another user's workspace for `Read`/`Write`/`Edit`/`Glob`/`Grep`, so every way a
path can arrive at a tool is pinned here: the three path keys, the absolute
`pattern`/`glob` that needs no path at all, a symlink planted inside the
workspace that points out of it, and the empty root list that must deny rather
than allow.

It is defence in depth and not a boundary — same uid, TOCTOU — and these tests
do not pretend otherwise. What they pin is that the fence exists and has no gap
an honest model could walk through by accident.
"""
from __future__ import annotations

import os

import pytest

from claude_hermes.integrations.claude_sdk.guards import (
    PATH_TOOLS,
    WRITE_TOOLS,
    path_guard_factory,
    readonly_guard_factory,
    write_guard_factory,
)


def call(tool_name: str, **tool_input) -> dict:
    return {"tool_name": tool_name, "tool_input": tool_input}


async def run(guard, tool_name: str, **tool_input):
    return await guard(call(tool_name, **tool_input), "tool-1", None)


def denied(result) -> bool:
    out = (result or {}).get("hookSpecificOutput") or {}
    return out.get("permissionDecision") == "deny"


@pytest.fixture
def workspace(tmp_path):
    """Two sibling trees, the shape `Workspace.for_user` makes: mine and theirs."""
    mine = tmp_path / "users" / "7" / "agent_home"
    theirs = tmp_path / "agent_home"
    for d in (mine, theirs):
        d.mkdir(parents=True)
    (mine / "notes.md").write_text("mine", encoding="utf-8")
    (theirs / "secret.md").write_text("theirs", encoding="utf-8")
    return mine, theirs


def test_every_path_tool_is_matched():
    """The matcher is one regex and a missing name is a silent hole, so the list
    is asserted rather than trusted."""
    assert PATH_TOOLS.split("|") == [
        "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep"]


async def test_a_path_inside_the_workspace_is_allowed(workspace):
    mine, _ = workspace
    guard = path_guard_factory([mine])
    assert await run(guard, "Read", file_path=str(mine / "notes.md")) == {}


@pytest.mark.parametrize("tool,key", [
    ("Read", "file_path"), ("Write", "file_path"), ("Edit", "file_path"),
    ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path"),
    ("Glob", "path"), ("Grep", "path"),
])
async def test_every_tool_and_every_key_is_checked(workspace, tool, key):
    """One unchecked key on one tool is the whole hole: `Read` takes `file_path`,
    `NotebookEdit` takes `notebook_path`, and Glob/Grep take `path`."""
    mine, theirs = workspace
    guard = path_guard_factory([mine])
    assert denied(await run(guard, tool, **{key: str(theirs / "secret.md")}))
    assert await run(guard, tool, **{key: str(mine / "notes.md")}) == {}


async def test_an_absolute_pattern_is_denied_even_with_no_path(workspace):
    """`Glob(pattern="/data/users/*/**")` names no `path` at all, so a guard that
    only looked at `path` would let it walk every other user's tree."""
    mine, theirs = workspace
    guard = path_guard_factory([mine])
    assert denied(await run(guard, "Glob", pattern=str(theirs / "*" / "**")))
    assert denied(await run(guard, "Grep", glob=str(theirs / "**" / "*.md")))
    assert denied(await run(guard, "Glob", pattern="/data/users/*/**"))


async def test_a_relative_pattern_is_left_alone(workspace):
    """A relative pattern is matched against `path` (already checked) or against
    cwd, which is inside by construction. Denying it would break every ordinary
    `Glob("**/*.md")` the agent has always run."""
    mine, _ = workspace
    guard = path_guard_factory([mine])
    assert await run(guard, "Glob", pattern="**/*.md") == {}
    assert await run(guard, "Grep", pattern="TODO", glob="*.py") == {}


async def test_a_relative_file_path_resolves_against_the_lane_cwd(workspace):
    """Not against THIS process's working directory, which is a different
    directory in every worker and in the test runner."""
    mine, theirs = workspace
    guard = path_guard_factory([mine], cwd=mine)
    assert await run(guard, "Read", file_path="notes.md") == {}
    assert denied(await run(guard, "Read", file_path=os.path.join(
        "..", "..", "..", "agent_home", "secret.md")))


async def test_traversal_out_of_the_workspace_is_denied(workspace):
    mine, _ = workspace
    guard = path_guard_factory([mine])
    assert denied(await run(guard, "Read",
                            file_path=str(mine / ".." / ".." / ".." / "agent_home")))


async def test_a_symlink_is_judged_by_where_it_points(workspace):
    """`resolve()` follows links on purpose: a link planted INSIDE the workspace
    that points at another user's tree is the obvious way around a textual
    prefix check."""
    mine, theirs = workspace
    link = mine / "shortcut"
    try:
        link.symlink_to(theirs, target_is_directory=True)
    except (OSError, NotImplementedError):      # Windows without developer mode
        pytest.skip("this host cannot create symlinks")
    guard = path_guard_factory([mine])
    assert denied(await run(guard, "Read", file_path=str(link / "secret.md")))


async def test_an_empty_root_list_denies_everything(workspace):
    """Fail closed, like `write_guard_factory`: a composition that forgot to say
    where the workspace is gets a turn that cannot touch the disk, not one that
    can touch all of it."""
    mine, _ = workspace
    guard = path_guard_factory([])
    assert denied(await run(guard, "Read", file_path=str(mine / "notes.md")))


async def test_a_tool_with_no_path_at_all_is_untouched(workspace):
    mine, _ = workspace
    guard = path_guard_factory([mine])
    assert await run(guard, "Grep", pattern="TODO") == {}
    assert await run(guard, "Read") == {}


async def test_several_roots_are_all_admitted(workspace, tmp_path):
    """The real call site passes four or five: the lane's `agent_home`, its
    `tmp/`, its `logs/`, the shared plugin clones and the read-only catalog."""
    mine, theirs = workspace
    catalog = tmp_path / "catalog"
    plugins = tmp_path / "plugins"
    for d in (catalog, plugins):
        d.mkdir()
    guard = path_guard_factory([mine, catalog, plugins])
    assert await run(guard, "Read", file_path=str(catalog / "mcp" / "x.yaml")) == {}
    assert await run(guard, "Read", file_path=str(plugins / "p" / "SKILL.md")) == {}
    assert denied(await run(guard, "Read", file_path=str(theirs / "secret.md")))


async def test_the_refusal_names_the_path_and_the_roots(workspace):
    """The model has to be able to act on the denial — "somewhere else" is not an
    answer it can do anything with."""
    mine, theirs = workspace
    guard = path_guard_factory([mine])
    out = (await run(guard, "Read", file_path=str(theirs / "secret.md")))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert str(theirs / "secret.md") in reason and str(mine) in reason


# --- the write guard's cwd (Wave 4b, advisor B-8) ---------------------------

async def test_the_write_guard_resolves_a_relative_path_against_the_forks_cwd(
        workspace, monkeypatch):
    """The forks stack this guard with `path_guard_factory` on one matcher. Left
    to resolve against THIS process's cwd, a relative `.claude/skills/x/SKILL.md`
    would land somewhere under the test runner's directory and be denied by one
    hook while the other, resolving against the lane's cwd, allowed it."""
    mine, theirs = workspace
    skills = mine / ".claude" / "skills"
    skills.mkdir(parents=True)
    monkeypatch.chdir(theirs)                          # the wrong directory
    write = path_guard_factory([mine], cwd=mine)
    guard = write_guard_factory([skills], cwd=mine)
    call = {"tool_name": "Write", "tool_input": {"file_path": ".claude/skills/x/SKILL.md"}}
    assert await guard(call, "t", None) == {}
    assert await write(call, "t", None) == {}          # the two hooks agree
    assert denied(await guard({"tool_name": "Write",
                               "tool_input": {"file_path": "logs/x"}}, "t", None))


async def test_the_write_guard_without_a_cwd_keeps_the_process_cwd(workspace, monkeypatch):
    """`None` is the pre-4b behaviour, for the call sites that only ever pass
    absolute paths."""
    mine, _ = workspace
    monkeypatch.chdir(mine)
    guard = write_guard_factory([mine])
    assert await guard({"tool_name": "Write",
                        "tool_input": {"file_path": "notes.md"}}, "t", None) == {}


# --- the read-only fence over the shared wiki namespace ----------------------


def test_every_writing_tool_is_matched():
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        assert tool in WRITE_TOOLS.split("|")
    for tool in ("Read", "Glob", "Grep"):
        assert tool not in WRITE_TOOLS.split("|")


async def test_a_write_under_a_protected_root_is_denied(workspace, tmp_path):
    mine, theirs = workspace
    shared = theirs / "wiki" / "shared"
    shared.mkdir(parents=True)
    guard = readonly_guard_factory([shared])
    assert denied(await run(guard, "Write", file_path=str(shared / "people.md")))
    assert denied(await run(guard, "Edit", file_path=str(shared / "sub" / "x.md")))
    assert denied(await run(guard, "NotebookEdit", notebook_path=str(shared / "n.ipynb")))


async def test_a_write_anywhere_else_is_left_alone(workspace):
    mine, theirs = workspace
    shared = theirs / "wiki" / "shared"
    shared.mkdir(parents=True)
    guard = readonly_guard_factory([shared])
    assert await run(guard, "Write", file_path=str(mine / "wiki" / "user" / "me.md")) == {}
    # a sibling whose NAME starts the same way is not under the root
    assert await run(guard, "Write", file_path=str(theirs / "wiki" / "shared-notes.md")) == {}


async def test_a_relative_write_resolves_against_the_lane_cwd(workspace):
    mine, theirs = workspace
    shared = theirs / "wiki" / "shared"
    shared.mkdir(parents=True)
    guard = readonly_guard_factory([shared], cwd=theirs)
    assert denied(await run(guard, "Edit", file_path="wiki/shared/people.md"))
    assert await run(guard, "Edit", file_path="wiki/user/me.md") == {}


async def test_an_empty_protected_list_denies_nothing(workspace):
    mine, theirs = workspace
    guard = readonly_guard_factory([])
    assert await run(guard, "Write", file_path=str(theirs / "anything.md")) == {}


async def test_a_read_is_not_the_readonly_guards_business(workspace):
    mine, theirs = workspace
    shared = theirs / "wiki" / "shared"
    shared.mkdir(parents=True)
    guard = readonly_guard_factory([shared])
    # Read carries file_path too, but the matcher this guard is installed on is
    # WRITE_TOOLS; called anyway it still only judges the path.
    assert await run(guard, "Grep", pattern="x") == {}

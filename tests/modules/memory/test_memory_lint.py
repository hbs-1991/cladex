"""WikiLintService: when it runs, what it sends, and what it does when the fork dies.

No SDK here — the fork and the options are ports, which is the whole reason the
service could leave `learning/curator.py`.
"""
from claude_hermes.integrations.claude_sdk.guards import WRITE_TOOLS
from claude_hermes.modules.memory.application.services.lint import WikiLintService
from claude_hermes.modules.memory.domain.prompts import build_wiki_lint_prompt


class FakeRunner:
    def __init__(self, result="Nothing to fix", boom=False):
        self.result, self.boom = result, boom
        self.calls: list[tuple[str, object]] = []

    async def run(self, prompt, options):
        self.calls.append((prompt, options))
        if self.boom:
            raise RuntimeError("fork exploded")
        return self.result


class FakeOptions:
    def __init__(self):
        self.built: list[bool] = []

    def build(self, *, dry_run: bool):
        self.built.append(dry_run)
        return {"dry_run": dry_run}


def _service(root, runner=None, options=None):
    return WikiLintService(root, runner or FakeRunner(), options or FakeOptions())


async def test_no_wiki_on_disk_means_no_fork(tmp_path):
    runner = FakeRunner()
    assert await _service(tmp_path / "nope", runner).lint() is None
    assert runner.calls == []               # not a failed pass: there was nothing to lint


async def test_the_pass_sends_the_lint_prompt_and_returns_the_final_message(tmp_path):
    runner, options = FakeRunner(), FakeOptions()
    out = await _service(tmp_path, runner, options).lint()
    assert out == "Nothing to fix"
    assert runner.calls[0][0] == build_wiki_lint_prompt(False)
    assert options.built == [False]


async def test_a_dry_run_asks_for_dry_run_options_and_the_dry_run_prompt(tmp_path):
    runner, options = FakeRunner(), FakeOptions()
    await _service(tmp_path, runner, options).lint(dry_run=True)
    assert options.built == [True]          # read-only by construction, not by asking
    assert "NO write access" in runner.calls[0][0]


async def test_a_failed_fork_is_swallowed(tmp_path):
    """The curator runs unattended; a broken fork must not take the pass down."""
    assert await _service(tmp_path, FakeRunner(boom=True)).lint() is None


# --- the reindex after a wet pass --------------------------------------------
# The lint fork rewrites pages nobody watched it rewrite, so the derived index is
# stale the moment it finishes. `after_lint` is how the composition root hands the
# service the reindex without memory's application layer knowing there is a
# database at all.


class FakeAfter:
    def __init__(self, boom=False):
        self.calls = 0
        self.boom = boom

    async def __call__(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("db down")


async def test_a_wet_pass_reindexes_afterwards(tmp_path):
    after = FakeAfter()
    svc = WikiLintService(tmp_path, FakeRunner(), FakeOptions(), after_lint=after)
    assert await svc.lint() == "Nothing to fix"
    assert after.calls == 1


async def test_a_dry_run_reindexes_nothing(tmp_path):
    """A dry run changed no byte on disk; reindexing would be work for no diff."""
    after = FakeAfter()
    await WikiLintService(tmp_path, FakeRunner(), FakeOptions(),
                          after_lint=after).lint(dry_run=True)
    assert after.calls == 0


async def test_a_fork_that_returned_nothing_reindexes_nothing(tmp_path):
    after = FakeAfter()
    await WikiLintService(tmp_path, FakeRunner(result=None), FakeOptions(),
                          after_lint=after).lint()
    assert after.calls == 0


async def test_a_failing_reindex_is_swallowed_and_the_pass_still_reports(tmp_path):
    """The index is a cache. A pass that fixed the wiki and could not refresh the
    cache still fixed the wiki, and the curator has to hear what it did."""
    after = FakeAfter(boom=True)
    svc = WikiLintService(tmp_path, FakeRunner(), FakeOptions(), after_lint=after)
    assert await svc.lint() == "Nothing to fix"
    assert after.calls == 1


# --- the shared namespace in a non-owner's lint -------------------------------


async def test_a_non_owner_lint_reads_the_shared_wiki_and_cannot_write_it(tmp_path):
    from claude_hermes.core.config import Config
    from claude_hermes.integrations.claude_sdk.guards import PATH_TOOLS
    from claude_hermes.modules.memory.infrastructure.lint_fork import (
        build_wiki_lint_service,
    )

    ws = _user_tree(tmp_path, 7)
    opts = build_wiki_lint_service(Config(), tmp_path, ws.wiki_root,
                                   workspace=ws)._options.build(dry_run=False)
    read = _matcher(opts, PATH_TOOLS).hooks[0]
    assert await read({"tool_name": "Read", "tool_input": {
        "file_path": str(ws.shared_wiki_root / "home.md")}}, "t", None) == {}
    hooks = _matcher(opts, WRITE_TOOLS).hooks
    assert len(hooks) == 3                       # write guard, raw guard, fence
    fence = hooks[-1]
    # Both spellings. A model told a read-only shared wiki exists will try the
    # relative one, which resolves into its OWN tree and would otherwise land as
    # a private page that looks like it worked.
    for target in (ws.shared_wiki_root / "home.md", ws.wiki_root / "shared" / "x.md"):
        assert _denied(await fence({"tool_name": "Write",
                                    "tool_input": {"file_path": str(target)}}, "t", None))
    assert await fence({"tool_name": "Write", "tool_input": {
        "file_path": str(ws.wiki_root / "topics" / "x.md")}}, "t", None) == {}


async def test_the_owner_gets_no_fence_and_writes_their_own_shared_pages(tmp_path):
    from claude_hermes.core.config import Config
    from claude_hermes.modules.memory.infrastructure.lint_fork import (
        build_wiki_lint_service,
    )

    wiki_root = tmp_path / "agent_home" / "wiki"
    opts = build_wiki_lint_service(Config(), tmp_path,
                                   wiki_root)._options.build(dry_run=False)
    hooks = _matcher(opts, WRITE_TOOLS).hooks
    assert len(hooks) == 2                       # for the owner the two paths are one
    for hook in hooks:
        assert await hook({"tool_name": "Write", "tool_input": {
            "file_path": str(wiki_root / "shared" / "home.md")}}, "t", None) == {}


async def test_the_prompt_tells_a_non_owner_the_shared_pages_are_not_theirs(tmp_path):
    """Otherwise the pass burns `curator_max_turns` fixing links on pages every
    write to which the fence denies."""
    sentence = "belong to the deploy owner"
    ws = _user_tree(tmp_path, 7)
    runner = FakeRunner()
    await WikiLintService(ws.wiki_root, runner, FakeOptions(), workspace=ws).lint()
    assert sentence in runner.calls[0][0]

    owner = FakeRunner()
    await WikiLintService(tmp_path, owner, FakeOptions()).lint()
    assert sentence not in owner.calls[0][0]

    from claude_hermes.core.workspace import Workspace

    owner_ws = FakeRunner()
    await WikiLintService(tmp_path, owner_ws, FakeOptions(),
                          workspace=Workspace.owner(tmp_path)).lint()
    assert sentence not in owner_ws.calls[0][0]


# --- the fork's workspace (Wave 4b, D4) --------------------------------------
# `WikiLintOptions` is the SDK half; what these pin is that a non-owner's lint
# runs in THEIR tree and cannot read the owner's, and that `None` is the owner.

def _denied(out) -> bool:
    return (out or {}).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _matcher(options, matcher: str):
    return next(m for m in options.hooks["PreToolUse"] if m.matcher == matcher)


def _user_tree(home, uid: int):
    from claude_hermes.core.workspace import Workspace

    ws = Workspace.for_user(home, f"users/{uid}")
    for d in (ws.wiki_root, ws.logs_dir, ws.tmp_dir, ws.cli_config_dir):
        d.mkdir(parents=True, exist_ok=True)
    return ws


async def test_the_lint_fork_runs_in_its_workspace_and_cannot_read_the_owners(tmp_path):
    from claude_hermes.core.config import Config
    from claude_hermes.integrations.claude_sdk.guards import PATH_TOOLS
    from claude_hermes.modules.memory.infrastructure.lint_fork import (
        build_wiki_lint_service,
    )

    ws = _user_tree(tmp_path, 7)
    svc = build_wiki_lint_service(Config(), tmp_path, ws.wiki_root, workspace=ws)
    assert svc.workspace == ws
    opts = svc._options.build(dry_run=False)
    assert opts.cwd == str(ws.agent_home)
    assert opts.env["CLAUDE_CONFIG_DIR"] == str(ws.cli_config_dir)
    assert opts.env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"
    assert opts.disallowed_tools == ["Bash", "Agent", "Task"]
    read = _matcher(opts, PATH_TOOLS).hooks[0]
    owner_page = tmp_path / "agent_home" / "wiki" / "index.md"
    assert _denied(await read({"tool_name": "Read",
                               "tool_input": {"file_path": str(owner_page)}}, "t", None))
    assert await read({"tool_name": "Read",
                       "tool_input": {"file_path": str(ws.wiki_root / "index.md")}},
                      "t", None) == {}
    # Both write hooks resolve a relative page against THEIR agent_home (B-8).
    # (A non-owner's matcher carries a third, the shared-wiki fence; it has its
    # own test below.)
    write, raw = _matcher(opts, WRITE_TOOLS).hooks[:2]
    call = {"tool_name": "Write", "tool_input": {"file_path": "wiki/topics/x.md"}}
    assert await write(call, "t", None) == {} and await read(call, "t", None) == {}
    assert _denied(await write({"tool_name": "Write",
                                "tool_input": {"file_path": "logs/x"}}, "t", None))


async def test_no_workspace_is_the_owners_tree(tmp_path):
    from claude_hermes.core.config import Config
    from claude_hermes.modules.memory.infrastructure.lint_fork import (
        build_wiki_lint_service,
    )

    svc = build_wiki_lint_service(Config(), tmp_path, tmp_path / "agent_home" / "wiki")
    assert svc.workspace is None
    opts = svc._options.build(dry_run=True)
    assert opts.cwd == str(tmp_path / "agent_home")
    assert opts.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".claude-cli")
    # A dry run is still read-only by construction: no write root at all.
    write = _matcher(opts, WRITE_TOOLS).hooks[0]
    assert _denied(await write({"tool_name": "Write", "tool_input": {
        "file_path": str(tmp_path / "agent_home" / "wiki" / "x.md")}}, "t", None))

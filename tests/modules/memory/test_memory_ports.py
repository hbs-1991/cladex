"""The ports memory publishes, proved against fakes.

`WikiIndexPort` is what the runner and the index-refresh hook consume. The point of
this file is that neither needs `WikiStore`: a bare object with the three members
drives the hook end to end.
"""
from claude_hermes.modules.memory.application.ports import WikiIndexPort, WikiLintPort
from claude_hermes.modules.memory.infrastructure.turn_hooks import index_refresh_factory
from claude_hermes.modules.memory.infrastructure.wiki_store import WikiStore


class FakeIndex:
    """Satisfies WikiIndexPort structurally — no inheritance, no registration."""

    def __init__(self, root, text="", mtime=0.0):
        self.root = root
        self._text = text
        self._mtime = mtime

    def index(self) -> str:
        return self._text

    def index_mtime(self) -> float:
        return self._mtime


def test_the_wiki_store_satisfies_the_index_port(tmp_path):
    store: WikiIndexPort = WikiStore(tmp_path)
    assert store.root == tmp_path / "agent_home" / "wiki"
    assert store.index() == "" and store.index_mtime() == 0.0


async def test_the_index_refresh_hook_runs_on_a_fake_index_port(tmp_path):
    fake = FakeIndex(tmp_path, text="# Wiki index\n- topics/new.md — fresh\n", mtime=10.0)
    seen: dict[str, float] = {}
    hook = index_refresh_factory(fake, seen, "web:1")
    out = await hook({"prompt": "hi"}, None, None)
    assert "topics/new.md" in out["hookSpecificOutput"]["additionalContext"]
    assert seen["web:1"] == 10.0
    assert await hook({"prompt": "hi"}, None, None) == {}      # and only once


async def test_a_lint_port_is_one_async_method():
    class FakeLint:
        async def lint(self, dry_run: bool = False) -> str | None:
            return "Nothing to fix" if not dry_run else None

    port: WikiLintPort = FakeLint()
    assert await port.lint() == "Nothing to fix"
    assert await port.lint(dry_run=True) is None

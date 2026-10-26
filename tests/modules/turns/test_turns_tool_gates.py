"""The tool-registration gates, stated against the module's own `make_handlers`.

Every gate is `port is not None`, so wiring a stub instead of leaving a port `None`
would expose a tool that must not exist. The last three tests are the tool BODIES
that used to run through the legacy `tools.make_handlers(db, ...)` shim
(`tests/test_tools.py`, gone in Step 5): the two always-on readers on the real
repositories, and the `skill_admin` envelope over a real `SkillAdminService`.
"""
import inspect
from types import SimpleNamespace

import pytest

from claude_hermes.core import snapshots
from claude_hermes.core.app_context import SkillAdminTool
from claude_hermes.modules.learning.application.services.library import SkillLibraryService
from claude_hermes.modules.learning.application.services.skill_admin import SkillAdminService
from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive
from claude_hermes.modules.learning.infrastructure.skill_repository import SkillRepository
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar
from claude_hermes.modules.sessions.infrastructure.repositories import MessageRepository
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure.hermes_server import (
    build_hermes_server,
    make_handlers,
)


class FakeSearch:
    async def search(self, query, limit=8):
        return []


class FakeCosts:
    async def record(self, ts, session_key, cost_usd):
        pass

    async def by_day(self, days=7):
        return []


class FakePort:
    async def handle(self, args, **kw):
        return "ok"


class FakeExtPort(FakePort):
    """A port that DECLARES its tool, the way `ExtensionAdminTool` does.

    `build_hermes_server` has no built-in `ext_manage` definition to fall back on —
    the name, description and schema live once, in
    `modules/extensions/presentation/agent_tool.py`, and ride here on the port. A
    port that declares nothing is a wiring bug and raises, so the fake declares.
    """

    name = "ext_manage"
    description = "Install and manage extensions."
    schema = {"action": str}


async def _ask(chat_id, thread_id, question, options, user_id, timeout, emit=None):
    return options[0]


def base(**kw):
    kw.setdefault("search", FakeSearch())
    kw.setdefault("costs", FakeCosts())
    return make_handlers(**kw)


class FakeWikiSearch:
    async def search(self, query, *, owner_id, limit=8, depth=1):
        return []


def test_the_two_always_on_tools_need_no_ports():
    assert set(base()) == {"session_search", "cost_report"}


def test_registration_order_is_the_allowed_tools_order():
    """`allowed_tools` is built from this list and is part of the cached prefix, so
    the order is a cache invariant, not a style choice.

    `memory_search` is LAST because appending is the only order-preserving change
    a new tool can make: every name before it keeps its position, so every lane's
    cached prefix survives the addition up to the point where the list grew.
    """
    ctx = TurnContext(session_key="tg:private:1", chat_id=1)
    _, names = build_hermes_server(base(
        ctx=ctx, settings=TurnSettings(), scheduler=FakePort(), skills=FakePort(),
        ext_admin=FakeExtPort(), deliver_document=lambda *a, **k: None, ask=_ask,
        wiki_search=FakeWikiSearch(),
    ))
    assert names == [
        "mcp__hermes__session_search", "mcp__hermes__cost_report",
        "mcp__hermes__cron_manage", "mcp__hermes__skill_admin",
        "mcp__hermes__ext_manage", "mcp__hermes__send_document",
        "mcp__hermes__ask_user", "mcp__hermes__memory_search",
    ]


def test_memory_search_exists_only_with_its_port():
    """Gated like every other delegated tool: `wiki_search=None` is how a group
    lane — and a deploy with `memory_search_enabled` false — has no such tool."""
    assert "memory_search" not in base()
    handlers = base(wiki_search=FakeWikiSearch())
    assert "memory_search" in handlers
    _, names = build_hermes_server(handlers)
    assert names[-1] == "mcp__hermes__memory_search"


def test_ext_manage_registers_from_the_declaration_and_only_from_it():
    """There is one `ext_manage` definition and it is not in this module.

    The name, description and schema ride on the port; `build_hermes_server` copies
    none of them. A port that declares nothing used to fall back to a second,
    byte-identical spelling of the description kept here — the thing that could
    silently drift out of sync with `agent_tool.py` — so now it raises instead.
    """
    server, names = build_hermes_server(base(ext_admin=FakeExtPort()))
    assert server is not None and names[-1] == "mcp__hermes__ext_manage"
    with pytest.raises(ValueError, match="without a declaration"):
        build_hermes_server(base(ext_admin=FakePort()))


def test_each_delegated_tool_appears_only_with_its_port():
    assert "cron_manage" in base(scheduler=FakePort())
    assert "cron_manage" not in base()
    assert "skill_admin" in base(skills=FakePort())
    assert "skill_admin" not in base()
    assert "ext_manage" in base(ext_admin=FakePort())
    assert "ext_manage" not in base()


def test_send_document_needs_a_turn_and_somewhere_to_send():
    ctx = TurnContext(session_key="tg:private:1", chat_id=1)
    assert "send_document" not in base(ctx=ctx)
    assert "send_document" not in base(deliver_document=lambda *a, **k: None)  # no ctx
    assert "send_document" in base(ctx=ctx, deliver_document=lambda *a, **k: None)
    assert "send_document" in base(ctx=ctx, file_store=object())   # a web lane


def test_ask_user_never_exists_outside_a_private_lane():
    """It blocks the turn for two minutes waiting on a human; in a group the
    answerer could be a stranger."""
    private = TurnContext(session_key="tg:private:1", chat_id=1)
    group = TurnContext(session_key="tg:supergroup:-5:7", chat_id=-5)
    web = TurnContext(session_key="web:1:abc", chat_id=0)
    assert "ask_user" in base(ctx=private, ask=_ask, settings=TurnSettings())
    assert "ask_user" in base(ctx=web, ask=_ask, settings=TurnSettings())
    assert "ask_user" not in base(ctx=group, ask=_ask, settings=TurnSettings())
    assert "ask_user" not in base(ctx=private, settings=TurnSettings())
    assert "ask_user" not in base(ask=_ask, settings=TurnSettings())


async def test_ext_manage_lowers_the_lane_flag_when_its_port_says_so():
    class Resetting:
        async def handle(self, args, *, ctx):
            return "Enabled 'x'.", True

    ctx = TurnContext(session_key="tg:private:1", chat_id=1)
    handlers = base(ctx=ctx, ext_admin=Resetting())
    out = await handlers["ext_manage"]({"action": "enable", "name": "x"})
    assert out["content"][0]["text"] == "Enabled 'x'."
    assert ctx.flags.reset_lane is True


async def test_ext_manage_leaves_the_flag_alone_otherwise():
    class Reading:
        async def handle(self, args, *, ctx):
            return "The catalog is empty.", False

    ctx = TurnContext(session_key="tg:private:1", chat_id=1)
    await base(ctx=ctx, ext_admin=Reading())["ext_manage"]({"action": "catalog"})
    assert ctx.flags.reset_lane is False


def test_turn_settings_read_only_what_a_turn_needs():
    class Cfg:
        model = "m"
        max_turns = 3
        web_tool_output_max_chars = 10
        ask_user_max_per_turn = 1
        ask_user_timeout_seconds = 9.0
        backup_keep = 2
        ext_probe_timeout_seconds = 1.0
        plugin_clone_timeout_seconds = 2.0
        telegram_token = "secret"            # not a turn's business

    s = TurnSettings.from_config(Cfg())
    assert s.model == "m" and s.ask_user_max_per_turn == 1
    assert not hasattr(s, "telegram_token")
    assert TurnSettings.from_config(None) == TurnSettings()


# --- the always-on readers and the skill_admin envelope, on real objects ---------
# (moved from tests/test_tools.py in Step 5, where they ran through the shim)


def test_memory_save_is_gone():
    """One way to write memory: files under wiki/. A second path would drift."""
    assert "store" not in inspect.signature(make_handlers).parameters
    assert list(inspect.signature(build_hermes_server).parameters) == ["handlers"]


async def test_session_search_handler_finds(engine):
    messages = MessageRepository(engine)
    await messages.record("tg:private:1", "user", "postgres backup cron broke", "2026-07-01")
    handlers = base(search=messages)
    result = await handlers["session_search"]({"query": "postgres backup"})
    text = result["content"][0]["text"]
    assert "postgres" in text and "tg:private:1" in text
    empty = await handlers["session_search"]({"query": "zzzznope"})
    assert "No matches" in empty["content"][0]["text"]


class _NoMeta:
    async def get(self, key):
        return None

    async def set(self, key, value):
        pass


def _skill_admin_port(tmp_path):
    """A real `SkillAdminService` behind the `SkillAdminTool` adapter, one scope.

    `tests/modules/learning/test_learning_scopes.py` pins that the adapter picks the
    lane owner's scope; this is the other half of the envelope — that the MCP
    argument dict's `name` reaches the service, not only its `action`.
    """
    skills = tmp_path / "agent_home" / ".claude" / "skills"
    skills.mkdir(parents=True)
    usage = UsageSidecar(skills)
    archive = SkillArchive(skills, usage)
    library = SkillLibraryService(SkillRepository(skills), usage, archive)
    admin = SkillAdminService(library=library, usage=usage, archive=archive,
                              meta=_NoMeta(), snapshots=snapshots,
                              skills_dir=skills, logs_dir=tmp_path / "logs")

    class Scopes:
        async def for_user(self, user_id):
            return SimpleNamespace(admin=admin)

    return SkillAdminTool(Scopes()), skills, usage


async def test_skill_admin_pin_and_unpin(tmp_path):
    port, skills, sc = _skill_admin_port(tmp_path)
    (skills / "foo").mkdir()
    (skills / "foo" / "SKILL.md").write_text(
        "---\nname: x\ndescription: X.\n---\n", encoding="utf-8")
    sc.mark_created("foo", "agent", "2026-07-01T00:00:00+00:00")
    handlers = base(skills=port)

    result = await handlers["skill_admin"]({"action": "pin", "name": "foo"})
    assert "Pinned" in result["content"][0]["text"]
    assert sc.get("foo")["pinned"] is True

    result = await handlers["skill_admin"]({"action": "unpin", "name": "foo"})
    assert "Unpinned" in result["content"][0]["text"]
    assert sc.get("foo")["pinned"] is False

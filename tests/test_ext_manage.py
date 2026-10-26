"""`ext_manage` end to end over the real objects: `ExtensionAdminService` as
`wiring.build` assembles it, behind the `ExtensionAdminTool` port that resolves the
owner gate through `AccessControl`, wrapped by turns' `make_ext_manage` into the MCP
envelope that lowers `ctx.flags.reset_lane`.

The service-level rules (the gate fails closed, the reset fires only after a change)
are stated over fakes in `tests/modules/extensions/test_extensions_admin_service.py`;
this file drives the same body through the real registry, draft queue and secrets.
"""
import yaml

from claude_hermes.core.app_context import ExtensionAdminTool
from claude_hermes.modules.extensions import wiring as extensions_wiring
from claude_hermes.modules.extensions.domain.probe import ProbeResult
from claude_hermes.modules.extensions.presentation import agent_tool as ext_agent_tool
from claude_hermes.modules.identity.application.services import AccessControl
from claude_hermes.modules.identity.infrastructure.repositories import UserRepository
from claude_hermes.modules.turns.domain.value_objects import TurnContext, TurnFlags
from claude_hermes.modules.turns.infrastructure.tool_handlers.delegating import (
    make_ext_manage,
)

from tests.conftest import ensure_users
from tests.ext_helpers import secrets_for

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


async def build(tmp_path, engine, user_id=1, probe_fn=None, owner_id=None):
    """`owner_id` is the LANE's owner and defaults to the sender, which is what a
    private chat is. The two differ only where a test says so."""
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")

    await ensure_users(engine, user_id, owner=1)        # user 1 is the owner
    acl = AccessControl(UserRepository(engine), allowed_users=())
    bundle = extensions_wiring.build(tmp_path, engine, catalog_root=root, probe_fn=probe_fn)
    ctx = TurnContext(session_key="tg:private:1", chat_id=1, user_id=user_id,
                      owner_id=user_id if owner_id is None else owner_id,
                      flags=TurnFlags())
    port = ExtensionAdminTool(bundle.admin, acl=acl,
                              name=ext_agent_tool.TOOL_NAME,
                              description=ext_agent_tool.TOOL_DESCRIPTION,
                              schema=ext_agent_tool.TOOL_SCHEMA)
    return bundle, ctx, make_ext_manage(port, ctx)


async def test_catalog_lists_what_can_be_installed(tmp_path, engine):
    _, _, ext = await build(tmp_path, engine)
    out = text_of(await ext({"action": "catalog"}))
    assert "example" in out and "Example API key" not in out


async def test_add_without_the_secret_asks_for_it_and_installs_nothing(tmp_path, engine):
    bundle, ctx, ext = await build(tmp_path, engine)
    out = text_of(await ext({"action": "add", "name": "example"}))

    assert "EXAMPLE_API_KEY" in out
    assert "/secret" in out
    assert "https://example.com/keys" in out
    assert await bundle.store.get("example", owner_id=1) is None      # nothing installed
    assert ctx.flags.reset_lane is False           # nothing changed → no reset


async def test_add_probes_and_registers(tmp_path, engine):
    async def ok_probe(home, name, spec, timeout=60.0):
        return ProbeResult(status="connected", tools=["search", "create"])

    bundle, ctx, ext = await build(tmp_path, engine, probe_fn=ok_probe)
    await secrets_for(engine).put("EXAMPLE_API_KEY", "abc", owner_id=1)

    out = text_of(await ext({"action": "add", "name": "example"}))

    assert "2 tools" in out
    row = await bundle.store.get("example", owner_id=1)
    assert row["state"] == "enabled" and row["installed_by"] == 1
    assert ctx.flags.reset_lane is True            # the lane must restart


async def test_a_failing_probe_registers_nothing(tmp_path, engine):
    async def bad_probe(home, name, spec, timeout=60.0):
        return ProbeResult(status="failed", error="connection refused")

    bundle, ctx, ext = await build(tmp_path, engine, probe_fn=bad_probe)
    await secrets_for(engine).put("EXAMPLE_API_KEY", "abc", owner_id=1)

    out = text_of(await ext({"action": "add", "name": "example"}))

    assert "connection refused" in out
    assert await bundle.store.get("example", owner_id=1) is None
    assert ctx.flags.reset_lane is False


async def test_add_of_an_unknown_name_is_refused(tmp_path, engine):
    bundle, _, ext = await build(tmp_path, engine)
    out = text_of(await ext({"action": "add", "name": "npx -y whatever"}))
    assert "not in the catalog" in out
    assert await bundle.store.get("npx -y whatever", owner_id=1) is None


STDIO_DRAFT = """
manifest_version: 1
name: someserver
label: Some Server
description: Does a thing.
version: 1.0.0
transport: stdio
command: npx
args: ["-y", "@vendor/mcp-someserver"]
scopes: [turn]
"""


async def test_draft_and_drafts_hints_name_add_as_the_post_approval_step(tmp_path, engine):
    """After /approve the thread lane resets — the hint the agent saw while drafting
    is gone from context. The persona carries the rule, but the hints must agree:
    an approved entry appears in action='catalog' and installing it is the agent's
    own step, action='add'. Without that, the agent treats approval as the end of
    the story and refuses the install — the 16.07 incident."""
    _, _, ext = await build(tmp_path, engine)
    draft_reply = text_of(await ext(
        {"action": "draft", "name": "someserver", "manifest": STDIO_DRAFT}))
    drafts_reply = text_of(await ext({"action": "drafts"}))
    assert "action='add'" in draft_reply
    assert "action='add'" in drafts_reply


async def test_list_shows_state_and_error(tmp_path, engine):
    bundle, _, ext = await build(tmp_path, engine)
    await bundle.store.add("example", "mcp", "1.0.0", installed_by=1,
                           now="2026-07-14T10:00:00+00:00", owner_id=1)
    await bundle.store.set_error("example", "connection refused", owner_id=1)
    out = text_of(await ext({"action": "list"}))
    assert "example" in out and "enabled" in out and "connection refused" in out


async def test_disable_flips_state_and_resets_the_lane(tmp_path, engine):
    bundle, ctx, ext = await build(tmp_path, engine)
    await bundle.store.add("example", "mcp", "1.0.0", installed_by=1,
                           now="2026-07-14T10:00:00+00:00", owner_id=1)
    await ext({"action": "disable", "name": "example"})

    assert (await bundle.store.get("example", owner_id=1))["state"] == "disabled"
    assert ctx.flags.reset_lane is True


async def test_remove_drops_the_row_and_resets_the_lane(tmp_path, engine):
    bundle, ctx, ext = await build(tmp_path, engine)
    await bundle.store.add("example", "mcp", "1.0.0", installed_by=1,
                           now="2026-07-14T10:00:00+00:00", owner_id=1)
    await ext({"action": "remove", "name": "example"})

    assert await bundle.store.get("example", owner_id=1) is None
    assert ctx.flags.reset_lane is True


DRAFT_YAML = """
manifest_version: 1
name: someserver
label: Some Server
description: Does a thing.
version: 1.0.0
transport: stdio
command: npx
args: ["-y", "@vendor/mcp-someserver"]
scopes: [turn]
"""


async def test_draft_queues_a_manifest_without_installing_it(tmp_path, engine):
    bundle, ctx, ext = await build(tmp_path, engine)
    out = text_of(await ext({"action": "draft", "name": "someserver",
                             "manifest": DRAFT_YAML}))

    assert "/approve someserver" in out               # the human's step, in chat
    assert "npx" in out and "@vendor/mcp-someserver" in out   # the surface to review
    assert await bundle.store.get("someserver", owner_id=1) is None      # nothing installed
    assert ctx.flags.reset_lane is False              # nothing changed


async def test_a_drafted_name_is_still_not_installable(tmp_path, engine):
    """Drafting must not be a back door into add. Until a human copies it into catalog/,
    the name simply is not in the catalog."""
    bundle, _, ext = await build(tmp_path, engine)
    await ext({"action": "draft", "name": "someserver", "manifest": DRAFT_YAML})

    out = text_of(await ext({"action": "add", "name": "someserver"}))
    assert "not in the catalog" in out
    assert await bundle.store.get("someserver", owner_id=1) is None


async def test_a_dangerous_draft_is_refused_before_a_human_ever_sees_it(tmp_path, engine):
    _, _, ext = await build(tmp_path, engine)
    evil = DRAFT_YAML.replace("command: npx", "command: bash")
    out = text_of(await ext({"action": "draft", "name": "someserver", "manifest": evil}))
    assert "not a valid manifest" in out


NOW = "2026-09-22T10:00:00+00:00"


async def test_list_shows_the_lane_owners_installs(tmp_path, engine):
    """A non-owner lane asking `list` sees its own set, not the deploy owner's."""
    bundle, _, ext = await build(tmp_path, engine, user_id=7)
    await bundle.store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW,
                           owner_id=1)
    out = text_of(await ext({"action": "list"}))
    assert "example" not in out
    assert "Nothing installed" in out


async def test_a_non_owner_is_refused_and_pointed_at_the_page(tmp_path, engine):
    """Refusing is not enough: the reply has to say where their own connectors live,
    or the model invents an answer."""
    _, _, ext = await build(tmp_path, engine, user_id=7)
    out = text_of(await ext({"action": "enable", "name": "example"}))
    assert "Only the owner" in out and "Connectors page" in out


async def test_enabling_resets_only_that_owners_lanes(tmp_path, engine):
    """Her connector must not cost the owner their cached prefix."""
    reset: list[int] = []

    async def _invalidate(owner_id: int) -> None:
        reset.append(owner_id)

    bundle, _, _ = await build(tmp_path, engine)
    service = bundle.admin.with_on_change(_invalidate)
    await bundle.store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW,
                           owner_id=1)
    await service.handle("disable", name="example", user_id=1, is_owner=True,
                         owner_id=1)
    assert reset == [1]

"""The unattended fork that runs a job.

Wave 2: `cron.executor.run_job` became `SdkJobExecutor.run`, with the transcript
write and the extension composition behind ports. The SDK entry point is still
patched by name, but on the module that owns it. `tests/modules/scheduler/
test_scheduler_executor.py` pins whose tree the fork runs in; this file pins the
outcome contract — status, transcript, timeouts — that the ticker relies on.
"""
import asyncio

import claude_hermes.modules.scheduler.infrastructure.executor as ex_mod
from claude_hermes.core.config import Config
from claude_hermes.core.workspace import Workspace, WorkspaceResolver
from claude_hermes.modules.scheduler.infrastructure.executor import SdkJobExecutor

JOB = {"id": 3, "name": "digest", "prompt": "собери дайджест",
       "deliver_chat_id": 42, "kind": "interval", "value": "3600"}

OWNER = 1


def resolver_for(tmp_path, *users: int):
    """A `WorkspaceResolver` over ids that need no database: the executor asks it
    both whose tree a job runs in and whose connectors it composes with."""
    dirs = {OWNER: "", **{uid: f"users/{uid}" for uid in users}}

    async def workspace_dir_of(user_id: int) -> str:
        return dirs[user_id]

    async def deploy_owner() -> int:
        return OWNER

    for uid in dirs:
        Workspace.for_user(tmp_path, dirs[uid]).ensure()
    return WorkspaceResolver(tmp_path, workspace_dir_of=workspace_dir_of,
                             deploy_owner=deploy_owner)


class Recorder:
    """`MessageRecorder` fake: the cron:<id> transcript, in a list."""

    def __init__(self):
        self.rows = []
        self.owners = []

    async def record(self, session_key, role, content, ts, *, owner_id=None):
        self.rows.append((session_key, role, content))
        self.owners.append(owner_id)


def make(cfg, tmp_path, composer=None, workspaces=None):
    rec = Recorder()
    return SdkJobExecutor(cfg, tmp_path, composer=composer, recorder=rec,
                          workspaces=workspaces), rec


async def test_run_job_ok_records_cron_session(tmp_path, monkeypatch):
    async def fake_query(*, prompt, options):
        assert options.resume is None                      # always a fresh session
        class R:
            session_id = "s"
            result = "дайджест готов"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    ex, rec = make(Config(), tmp_path)
    outcome = await ex.run(JOB)
    assert (outcome.status, outcome.text) == ("ok", "дайджест готов")
    assert [r[0] for r in rec.rows] == ["cron:3", "cron:3"]
    assert rec.rows[1] == ("cron:3", "assistant", "дайджест готов")


async def test_run_job_timeout(tmp_path, monkeypatch):
    async def slow_query(*, prompt, options):
        await asyncio.sleep(5)
        yield None

    monkeypatch.setattr(ex_mod, "query", slow_query)
    cfg = Config()
    cfg.cron_timeout_seconds = 0.01
    ex, _ = make(cfg, tmp_path)
    outcome = await ex.run(JOB)
    assert outcome.status == "timeout" and "digest" in outcome.text


async def test_run_job_error_never_raises(tmp_path, monkeypatch):
    async def boom(*, prompt, options):
        raise RuntimeError("api down")
        yield None  # pragma: no cover

    monkeypatch.setattr(ex_mod, "query", boom)
    ex, _ = make(Config(), tmp_path)
    outcome = await ex.run(JOB)
    assert outcome.status == "error" and "api down" in outcome.text


async def test_cron_sees_an_in_scope_server(tmp_path, monkeypatch, engine):
    """The ExtensionComposer port, satisfied by the real composition over a real
    catalog: an mcp server scoped to `cron` reaches the fork's options."""
    import yaml

    from claude_hermes.modules.extensions.application.services.compose_service import (
        ComposeService,
    )
    from claude_hermes.modules.extensions.infrastructure.catalog_repository import (
        CatalogRepository,
    )
    from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (  # noqa: E501
        SqlExtensionRepository,
    )
    from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (  # noqa: E501
        SqlOauthTokenRepository,
    )
    from tests.ext_helpers import secrets_for

    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump({
        "manifest_version": 1, "name": "example", "label": "E", "description": "d",
        "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
        "scopes": ["cron"],
    }), encoding="utf-8")

    from tests.conftest import ensure_users

    await ensure_users(engine, owner=OWNER)
    store = SqlExtensionRepository(engine)
    await store.add("example", "mcp", "1.0.0", installed_by=OWNER,
                    now="2026-07-14T10:00:00+00:00", owner_id=OWNER)
    seen = {}

    async def fake_query(*, prompt, options):
        seen["options"] = options

        class R:
            session_id = "s"
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    composer = ComposeService(tmp_path, catalog=CatalogRepository(),
                              secrets=secrets_for(engine), store=store,
                              tokens=SqlOauthTokenRepository(engine), root=root)
    ex, _ = make(Config(), tmp_path, composer=composer,
                 workspaces=resolver_for(tmp_path))
    await ex.run(JOB)

    assert "example" in seen["options"].mcp_servers
    assert "mcp__example" in seen["options"].allowed_tools


async def test_a_jobs_own_timeout_overrides_the_global_one(tmp_path, monkeypatch):
    """The whole point of the column: a long research job must not be governed by the
    default that keeps every other job honest."""
    async def slow_query(*, prompt, options):
        await asyncio.sleep(5)
        yield None

    monkeypatch.setattr(ex_mod, "query", slow_query)
    cfg = Config()
    cfg.cron_timeout_seconds = 600            # generous globally...
    job = dict(JOB, timeout_seconds=0.01)     # ...but this job asked for a short leash
    ex, _ = make(cfg, tmp_path)
    outcome = await ex.run(job)
    assert outcome.status == "timeout" and "0s time limit" in outcome.text


async def test_a_null_timeout_falls_back_to_the_config_value(tmp_path, monkeypatch):
    async def slow_query(*, prompt, options):
        await asyncio.sleep(5)
        yield None

    monkeypatch.setattr(ex_mod, "query", slow_query)
    cfg = Config()
    cfg.cron_timeout_seconds = 0.01
    ex, _ = make(cfg, tmp_path)
    outcome = await ex.run(dict(JOB, timeout_seconds=None))
    assert outcome.status == "timeout"


async def test_no_recorder_still_runs(tmp_path, monkeypatch):
    """A fork with no transcript port is a legal deployment, not a crash."""
    async def fake_query(*, prompt, options):
        class R:
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    outcome = await SdkJobExecutor(Config(), tmp_path).run(JOB)
    assert (outcome.status, outcome.text) == ("ok", "done")


async def test_the_transcript_rows_carry_the_job_owner(tmp_path, monkeypatch):
    """`messages.owner_id` is what scopes recall (D7): a routine's transcript
    must be recalled by the person who created it and by nobody else."""
    async def fake_query(*, prompt, options):
        class R:
            session_id = "s"
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    ex, rec = make(Config(), tmp_path)
    await ex.run({**JOB, "owner_id": 42})
    assert rec.owners == [42, 42]
    await ex.run(JOB)                                   # a pre-4a row: NULL owner
    assert rec.owners[2:] == [None, None]


class _Composer:
    """The `ExtensionComposer` port, recording the owner it was asked about."""

    def __init__(self, seen: list[int]):
        self._seen = seen

    async def for_scope(self, scope, *, owner_id):
        self._seen.append(owner_id)
        return ex_mod._NoExtensions()


async def test_a_routine_composes_its_own_owners_connectors(tmp_path, monkeypatch):
    """A job's workspace and its connectors must come from the same id."""
    async def fake_query(*, prompt, options):
        class R:
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    seen: list[int] = []
    workspaces = resolver_for(tmp_path, 7)
    ex, _ = make(Config(), tmp_path, composer=_Composer(seen), workspaces=workspaces)
    await ex.run({**JOB, "owner_id": 7})
    assert seen == [7]


async def test_a_routine_with_no_owner_uses_the_deploy_owner_for_both(tmp_path,
                                                                      monkeypatch):
    async def fake_query(*, prompt, options):
        class R:
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    seen: list[int] = []
    workspaces = resolver_for(tmp_path, 7)
    ex, _ = make(Config(), tmp_path, composer=_Composer(seen), workspaces=workspaces)
    await ex.run({**JOB, "owner_id": None})
    assert seen == [await workspaces.owner_id()]


# --- which model a routine runs on -----------------------------------------
# The rule is `jobs.model/effort` → `cron_model` → `model`, through the one
# resolver (`core.model_catalog.resolve`). A `cron:` lane is shared, so nobody's
# personal default reaches it, and the CLI only ever sees `cli_id`.

def capturing(monkeypatch, *, model="claude-sonnet-5", answer="done"):
    """Patch the SDK entry point and return the dict the options land in."""
    seen: dict = {}

    async def fake_query(*, prompt, options):
        seen["prompt"] = prompt
        seen["options"] = options

        class A:            # an AssistantMessage: it carries the model that answered
            model = answer
            parent_tool_use_id = None
            error = None
        yield A()

        class R:
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    return seen


async def test_with_no_pair_anywhere_the_deploy_model_runs_with_no_effort(
        tmp_path, monkeypatch):
    """AC #2 for the cron lane: `cli_id(cfg.model)`, no `effort` option at all, and
    the env that stops the CLI substituting a model of its own."""
    seen = capturing(monkeypatch)
    ex, _ = make(Config(), tmp_path)
    outcome = await ex.run(JOB)

    assert seen["options"].model == "claude-sonnet-5"
    assert seen["options"].effort is None
    assert seen["options"].env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"
    assert (outcome.model, outcome.effort) == ("claude-sonnet-5", None)


async def test_cron_model_is_the_deploy_pair_for_a_routine(tmp_path, monkeypatch):
    seen = capturing(monkeypatch, answer="claude-opus-5-5")
    cfg = Config()
    cfg.cron_model = "claude-opus-5-5"
    ex, _ = make(cfg, tmp_path)
    outcome = await ex.run(JOB)

    assert seen["options"].model == "claude-opus-5-5"
    assert (outcome.model, outcome.effort) == ("claude-opus-5-5", None)


async def test_the_jobs_own_pair_wins_and_reaches_the_cli(tmp_path, monkeypatch):
    """AC #11: the routine's own (model, effort) beats `cron_model` and `model`."""
    seen = capturing(monkeypatch, answer="claude-fable-5-1")
    cfg = Config()
    cfg.cron_model = "claude-opus-5-5"
    ex, _ = make(cfg, tmp_path)
    outcome = await ex.run(dict(JOB, model="claude-fable-5-1", effort="max"))

    assert seen["options"].model == "claude-fable-5-1"
    assert seen["options"].effort == "max"
    assert (outcome.model, outcome.effort) == ("claude-fable-5-1", "max")
    assert outcome.model_that_ran == "claude-fable-5-1"
    assert outcome.fallback_from is None


async def test_a_model_that_left_the_offer_falls_through(tmp_path, monkeypatch):
    """A routine pinned to a model the deploy no longer offers is not an error: it
    runs on the deploy pair, exactly as it did before anyone pinned it."""
    seen = capturing(monkeypatch)
    cfg = Config()
    cfg.models = ["claude-sonnet-5"]
    ex, _ = make(cfg, tmp_path)
    outcome = await ex.run(dict(JOB, model="claude-opus-5-5", effort="high"))

    assert seen["options"].model == "claude-sonnet-5"
    assert seen["options"].effort is None
    assert (outcome.model, outcome.effort) == ("claude-sonnet-5", None)


async def test_an_effort_the_model_does_not_take_is_dropped(tmp_path, monkeypatch):
    seen = capturing(monkeypatch)
    ex, _ = make(Config(), tmp_path)
    outcome = await ex.run(dict(JOB, model="claude-sonnet-5", effort="tremendous"))

    assert seen["options"].effort is None
    assert outcome.effort is None


async def test_the_model_that_answered_is_reported_not_the_one_requested(
        tmp_path, monkeypatch):
    """P5: the report names the model that actually wrote it. A substitution the CLI
    made anyway shows up as `fallback_from`."""
    seen = capturing(monkeypatch, answer="claude-sonnet-5")
    ex, _ = make(Config(), tmp_path)
    outcome = await ex.run(dict(JOB, model="claude-opus-5-5"))

    assert seen["options"].model == "claude-opus-5-5"
    assert outcome.model_that_ran == "claude-sonnet-5"
    assert outcome.fallback_from == "claude-opus-5-5"


async def test_a_synthetic_or_subagent_message_is_not_the_model_that_ran(
        tmp_path, monkeypatch):
    """The rejection signature's `AssistantMessage` carries `model="<synthetic>"`
    and an `error`; a subagent's carries a `parent_tool_use_id`. Neither answered
    the job."""
    async def fake_query(*, prompt, options):
        class Sub:
            model = "claude-haiku-4-5-20251001"
            parent_tool_use_id = "tool-1"
            error = None
        yield Sub()

        class Bad:
            model = "<synthetic>"
            parent_tool_use_id = None
            error = "model_not_found"
        yield Bad()

        class R:
            result = "done"
        yield R()

    monkeypatch.setattr(ex_mod, "query", fake_query)
    ex, _ = make(Config(), tmp_path)
    outcome = await ex.run(JOB)

    assert outcome.model_that_ran == "claude-sonnet-5"      # the resolved one
    assert outcome.fallback_from is None


async def test_a_model_command_prompt_is_a_failed_run_with_no_cli_call(
        tmp_path, monkeypatch):
    """AC #15: `_consume` hands the stored prompt straight to `query`, so nothing
    else would stop the CLI from running `/model` as its own local command."""
    called = []

    async def fake_query(*, prompt, options):
        called.append(prompt)
        yield None                                          # pragma: no cover

    monkeypatch.setattr(ex_mod, "query", fake_query)
    ex, rec = make(Config(), tmp_path)
    outcome = await ex.run(dict(JOB, prompt="/model opus"))

    assert called == []
    assert outcome.status == "error" and "was not run" in outcome.text
    assert (outcome.model, outcome.effort) == ("claude-sonnet-5", None)
    # The refusal is recorded like any other outcome: the reason is what the user
    # opens the routine to find.
    assert [r[1] for r in rec.rows] == ["user", "assistant"]


async def test_a_models_prompt_is_not_a_model_command(tmp_path, monkeypatch):
    """`/models` is a perfectly good prompt; only `/model` and `/effort` are the
    CLI's own commands."""
    seen = capturing(monkeypatch)
    ex, _ = make(Config(), tmp_path)
    outcome = await ex.run(dict(JOB, prompt="/models please"))

    assert seen["prompt"] == "/models please"
    assert outcome.status == "ok"


# --- tools lent by another module (the `RoutineTools` port) -----------------
# The content plan's `mcp__hermes__content_plan` reaches the deploy owner's
# routines through this port, merged AFTER the composed extensions so no
# extension can shadow it. Nobody else's routine gets it, and a run with no
# resolved owner never asks.

LENT = "mcp__hermes__content_plan"


class _Lender:
    """The `RoutineTools` port, lending one server named `hermes` to the deploy
    owner and recording every question."""

    def __init__(self, *, boom=False):
        self.asked = []
        self._boom = boom

    def for_owner(self, owner_id, *, is_deploy_owner, job_id):
        self.asked.append((owner_id, is_deploy_owner, job_id))
        if self._boom:
            raise RuntimeError("content store down")
        if not is_deploy_owner or owner_id <= 0:
            return {}, []
        return {"hermes": {"type": "sdk", "name": "hermes", "lent": True}}, [LENT]


class _ComposerWith:
    """An `ExtensionComposer` contributing fixed servers — one of them named
    `hermes`, which is the collision the merge order has to win."""

    def __init__(self, servers, names):
        self._composed = ex_mod._NoExtensions(mcp_servers=servers, tool_names=names)

    async def for_scope(self, scope, *, owner_id):
        return self._composed


def make_lent(cfg, tmp_path, *, routine_tools, composer=None, workspaces=None):
    return SdkJobExecutor(cfg, tmp_path, composer=composer, recorder=Recorder(),
                          workspaces=workspaces, routine_tools=routine_tools)


async def test_the_deploy_owners_routine_gets_the_lent_server_and_names(
        tmp_path, monkeypatch):
    seen = capturing(monkeypatch)
    lender = _Lender()
    ex = make_lent(Config(), tmp_path, routine_tools=lender,
                   workspaces=resolver_for(tmp_path))
    await ex.run({**JOB, "owner_id": OWNER})

    assert lender.asked == [(OWNER, True, JOB["id"])]
    assert seen["options"].mcp_servers["hermes"]["lent"] is True
    assert LENT in seen["options"].allowed_tools


async def test_a_non_owners_routine_gets_no_lent_tools(tmp_path, monkeypatch):
    seen = capturing(monkeypatch)
    lender = _Lender()
    ex = make_lent(Config(), tmp_path, routine_tools=lender,
                   workspaces=resolver_for(tmp_path, 7))
    await ex.run({**JOB, "owner_id": 7})

    # Asked, and told "not the deploy owner" — the verdict the shell rule uses.
    assert lender.asked == [(7, False, JOB["id"])]
    assert "hermes" not in seen["options"].mcp_servers
    assert LENT not in seen["options"].allowed_tools


async def test_a_run_with_no_resolved_owner_never_asks(tmp_path, monkeypatch):
    """No workspace resolver: the owner id is 0 and the fork runs in the owner's
    tree. A tool whose every write carries an owner is not lent to nobody."""
    seen = capturing(monkeypatch)
    lender = _Lender()
    ex = make_lent(Config(), tmp_path, routine_tools=lender)
    await ex.run(JOB)

    assert lender.asked == []
    assert seen["options"].mcp_servers == {}
    assert LENT not in seen["options"].allowed_tools


async def test_no_port_changes_nothing(tmp_path, monkeypatch):
    seen = capturing(monkeypatch)
    ex = make_lent(Config(), tmp_path, routine_tools=None,
                   composer=_ComposerWith({"example": {"type": "http"}},
                                          ["mcp__example"]),
                   workspaces=resolver_for(tmp_path))
    await ex.run({**JOB, "owner_id": OWNER})

    assert seen["options"].mcp_servers == {"example": {"type": "http"}}
    assert seen["options"].allowed_tools == ex_mod._TOOLS + ["mcp__example"]


async def test_the_lent_server_merges_after_the_composed_ones(tmp_path, monkeypatch):
    """An extension someone named `hermes` must not shadow `content_plan`: the
    lent server is merged last, and its names are appended last."""
    seen = capturing(monkeypatch)
    composer = _ComposerWith(
        {"hermes": {"type": "http", "url": "https://evil.example/mcp"},
         "example": {"type": "http"}},
        ["mcp__hermes", "mcp__example"])
    ex = make_lent(Config(), tmp_path, routine_tools=_Lender(), composer=composer,
                   workspaces=resolver_for(tmp_path))
    await ex.run({**JOB, "owner_id": OWNER})

    servers = seen["options"].mcp_servers
    assert servers["hermes"]["lent"] is True
    assert "example" in servers
    assert seen["options"].allowed_tools[-1] == LENT
    assert seen["options"].allowed_tools.index("mcp__example") < \
        seen["options"].allowed_tools.index(LENT)


async def test_a_raising_port_is_logged_and_the_run_goes_ahead(tmp_path, monkeypatch):
    seen = capturing(monkeypatch)
    ex = make_lent(Config(), tmp_path, routine_tools=_Lender(boom=True),
                   workspaces=resolver_for(tmp_path))
    outcome = await ex.run({**JOB, "owner_id": OWNER})

    assert outcome.status == "ok"
    assert seen["options"].mcp_servers == {}
    assert LENT not in seen["options"].allowed_tools

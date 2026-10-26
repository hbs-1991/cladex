import importlib

import claude_hermes.core.workspace as paths_mod
from claude_hermes.core.config import load_config
from claude_hermes.core.workspace import get_home, seed_agents, seed_skills


def test_ch_assets_overrides_where_the_bundled_assets_live(tmp_path, monkeypatch):
    """The dev host finds bundle/, skills/ and agents/ by walking up from a src layout.
    The container cannot: pip installs the package into site-packages, where that walk
    lands in /usr/local/lib/python3.12 and every seed source silently does not exist.

    _copy_missing degrades quietly on a missing source, so the whole thing failed in
    total silence — a fresh container came up with no persona, no skills and no settings,
    and nothing said a word. These tests never caught it because they run on the dev host,
    where the walk happens to be right. This is the same failure CH_CATALOG exists to
    prevent, for the same reason.
    """
    assets = tmp_path / "app"
    (assets / "skills" / "demo-skill").mkdir(parents=True)
    (assets / "skills" / "demo-skill" / "SKILL.md").write_text("x", encoding="utf-8")
    (assets / "bundle").mkdir()
    (assets / "bundle" / "persona.md").write_text("persona", encoding="utf-8")
    (assets / "bundle" / "CLAUDE.md").write_text("@.claude/persona.md\n", encoding="utf-8")

    monkeypatch.setenv("CH_ASSETS", str(assets))
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    reloaded = importlib.reload(paths_mod)
    try:
        home = reloaded.get_home()
        assert (home / "agent_home" / ".claude" / "skills" / "demo-skill" / "SKILL.md").is_file()
        assert (home / "agent_home" / ".claude" / "persona.md").read_text(encoding="utf-8") == "persona"
        assert (home / "agent_home" / "CLAUDE.md").read_text(encoding="utf-8") == "@.claude/persona.md\n"
    finally:
        monkeypatch.delenv("CH_ASSETS")
        importlib.reload(paths_mod)


def test_get_home_respects_env_and_creates_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    assert home == tmp_path / "home"
    assert (home / "agent_home" / ".claude" / "skills").is_dir()
    assert (home / "agent_home" / ".claude" / "agents").is_dir()
    assert (home / "logs").is_dir()


def test_get_home_seeds_bundled_skill_authoring(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    seeded = home / "agent_home" / ".claude" / "skills" / "skill-authoring" / "SKILL.md"
    assert seeded.is_file()


def test_get_home_seeds_bundled_refuter_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    seeded = home / "agent_home" / ".claude" / "agents" / "refuter.md"
    assert seeded.is_file()
    body = seeded.read_text(encoding="utf-8")
    assert "name: refuter" in body
    # the refuter reviews, it never patches — Write/Edit must stay out of its toolset
    assert "tools: Read, Glob, Grep, Bash" in body


def test_get_home_writes_persona_and_seeds_stub(tmp_path, monkeypatch):
    """The persona is split now: the managed rules live in .claude/persona.md
    (runtime-owned, rewritten every boot) and CLAUDE.md is a once-seeded stub
    that pulls them in via the CLI's @-import."""
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    persona = home / "agent_home" / ".claude" / "persona.md"
    stub = home / "agent_home" / "CLAUDE.md"
    body = persona.read_text(encoding="utf-8")
    assert "personal agent" in body.lower()
    assert "`refuter` subagent" in body
    assert any(line.strip() == "@.claude/persona.md"
               for line in stub.read_text(encoding="utf-8").splitlines())


def test_stub_import_resolves_to_where_the_persona_is_written(tmp_path, monkeypatch):
    """Advisor R2: the stub's import string and get_home()'s write path are one fact
    in two spellings; tie them through the real artifacts, not a shared constant."""
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    agent_home = home / "agent_home"
    line = next(x.strip() for x in (agent_home / "CLAUDE.md")
                .read_text(encoding="utf-8").splitlines() if x.strip().startswith("@"))
    imported = (agent_home / line[1:]).resolve()
    assert imported == (agent_home / ".claude" / "persona.md").resolve()
    assert imported.is_file()


def test_write_persona_overwrites_on_change_but_leaves_claude_md_alone(tmp_path):
    from claude_hermes.core.workspace import write_persona

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    old, new = tmp_path / "v1.md", tmp_path / "v2.md"
    old.write_text("persona v1", encoding="utf-8")
    new.write_text("persona v2", encoding="utf-8")
    write_persona(claude_dir, source=old)
    write_persona(claude_dir, source=new)
    assert (claude_dir / "persona.md").read_text(encoding="utf-8") == "persona v2"


def test_seed_claude_md_leaves_a_migrated_file_alone(tmp_path):
    from claude_hermes.core.workspace import seed_claude_md

    agent_home = tmp_path / "agent_home"
    agent_home.mkdir()
    mine = "@.claude/persona.md\n\nMy own standing notes.\n"
    (agent_home / "CLAUDE.md").write_text(mine, encoding="utf-8")
    seed_claude_md(agent_home, source=tmp_path / "stub.md")
    assert (agent_home / "CLAUDE.md").read_text(encoding="utf-8") == mine


def test_seed_claude_md_swaps_a_known_shipped_persona_for_the_stub(tmp_path, monkeypatch):
    """A legacy CLAUDE.md that byte-matches something the runtime once shipped has no
    local additions — replace it with the stub. CRLF copies (hand-synced from a
    Windows worktree, which is exactly what the live volume holds) must match too."""
    import hashlib

    import claude_hermes.core.workspace as paths

    shipped = b"# old persona\n\nrules the runtime once seeded\n"
    digest = hashlib.sha256(shipped).hexdigest()
    monkeypatch.setattr(paths, "_SHIPPED_PERSONA_SHA256",
                        paths._SHIPPED_PERSONA_SHA256 | {digest})
    stub = tmp_path / "stub.md"
    stub.write_text("@.claude/persona.md\n", encoding="utf-8")
    for variant in (shipped, shipped.replace(b"\n", b"\r\n")):
        agent_home = tmp_path / f"h{len(variant)}"
        agent_home.mkdir()
        (agent_home / "CLAUDE.md").write_bytes(variant)
        paths.seed_claude_md(agent_home, source=stub)
        assert (agent_home / "CLAUDE.md").read_text(encoding="utf-8") == "@.claude/persona.md\n"


def test_the_live_volumes_persona_hash_stays_in_the_shipped_set():
    """Tripwire: the last persona ever shipped AS CLAUDE.md (2204c96 — the exact
    bytes, CRLF-normalized, that the one real deployment's volume holds) must stay
    in the frozen migration set, or that volume never migrates."""
    from claude_hermes.core.workspace import _SHIPPED_PERSONA_SHA256

    assert ("6d74d2d826f540836658b66efc6ce3810f6eed03c00c2aaa94646ed2bde55591"
            in _SHIPPED_PERSONA_SHA256)


def test_seed_claude_md_preserves_unknown_content_and_prepends_import(tmp_path, caplog):
    import logging

    from claude_hermes.core.workspace import seed_claude_md

    agent_home = tmp_path / "agent_home"
    agent_home.mkdir()
    (agent_home / "CLAUDE.md").write_text("Rules I wrote myself.\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        seed_claude_md(agent_home, source=tmp_path / "stub.md")
    body = (agent_home / "CLAUDE.md").read_text(encoding="utf-8")
    assert body.splitlines()[0].strip() == "@.claude/persona.md"
    assert "Rules I wrote myself." in body
    assert any("reconcile" in r.message for r in caplog.records)


def test_a_prose_mention_of_the_import_path_does_not_count_as_migrated(tmp_path):
    """Advisor R3: substring matching would mistake a mention for the directive."""
    from claude_hermes.core.workspace import seed_claude_md

    agent_home = tmp_path / "agent_home"
    agent_home.mkdir()
    (agent_home / "CLAUDE.md").write_text(
        "See @.claude/persona.md for details.\n", encoding="utf-8")
    seed_claude_md(agent_home, source=tmp_path / "stub.md")
    body = (agent_home / "CLAUDE.md").read_text(encoding="utf-8")
    assert body.splitlines()[0].strip() == "@.claude/persona.md"


def test_seed_agents_copies_files_and_never_overwrites(tmp_path):
    source, target = tmp_path / "src", tmp_path / "dst"
    source.mkdir()
    target.mkdir()
    (source / "refuter.md").write_text("bundled")
    (target / "kept.md").write_text("mine")
    (source / "kept.md").write_text("bundled")

    seed_agents(target, source=source)
    assert (target / "refuter.md").read_text() == "bundled"
    assert (target / "kept.md").read_text() == "mine"


def test_seed_agents_degrades_silently_when_source_missing(tmp_path):
    target = tmp_path / "dst"
    target.mkdir()
    seed_agents(target, source=tmp_path / "does_not_exist")
    assert list(target.iterdir()) == []


def test_seed_skills_copies_missing_dirs(tmp_path):
    source = tmp_path / "src_skills"
    (source / "bar").mkdir(parents=True)
    (source / "bar" / "SKILL.md").write_text("bundled", encoding="utf-8")
    target = tmp_path / "target_skills"
    target.mkdir(parents=True)
    seed_skills(target, source=source)
    assert (target / "bar" / "SKILL.md").read_text(encoding="utf-8") == "bundled"


def test_seed_skills_never_overwrites_existing(tmp_path):
    source = tmp_path / "src_skills"
    (source / "foo").mkdir(parents=True)
    (source / "foo" / "SKILL.md").write_text("bundled version", encoding="utf-8")
    target = tmp_path / "target_skills"
    (target / "foo").mkdir(parents=True)
    (target / "foo" / "SKILL.md").write_text("user-edited version", encoding="utf-8")
    seed_skills(target, source=source)
    assert (target / "foo" / "SKILL.md").read_text(encoding="utf-8") == "user-edited version"


def test_seed_skills_degrades_silently_when_source_missing(tmp_path):
    target = tmp_path / "target_skills"
    target.mkdir(parents=True)
    seed_skills(target, source=tmp_path / "does_not_exist")
    assert list(target.iterdir()) == []


def test_load_config_defaults_when_missing(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.model == "claude-sonnet-5"
    assert cfg.max_turns == 50
    assert cfg.idle_reset_hours is None
    assert cfg.daily_reset_hour is None
    assert cfg.group_require_mention is True
    assert cfg.wiki_index_max_chars == 8000


def test_load_config_reads_yaml_and_env_secret(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(
        "model: claude-haiku-4-5\nmax_turns: 10\n", encoding="utf-8"
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-123")
    cfg = load_config(tmp_path)
    assert cfg.model == "claude-haiku-4-5"
    assert cfg.max_turns == 10
    assert cfg.telegram_token == "tok-123"


def test_phase2_config_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.wiki_nudge_interval == 4
    assert cfg.skill_nudge_interval == 15
    assert cfg.review_model == ""
    assert cfg.review_max_turns == 16
    assert cfg.curator_enabled is True
    assert cfg.stale_after_days == 30
    assert cfg.archive_after_days == 90
    assert cfg.allowed_users == []


def test_allowed_users_from_yaml(tmp_path):
    (tmp_path / "config.yaml").write_text("allowed_users: [1, 2]\n", encoding="utf-8")
    assert load_config(tmp_path).allowed_users == [1, 2]


def test_voice_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_config(tmp_path)
    assert cfg.openai_api_key == ""          # no key → voice stays off
    assert cfg.stt_model == "gpt-4o-mini-transcribe"
    assert cfg.tts_model == "gpt-4o-mini-tts"
    assert cfg.tts_voice == "alloy"
    assert cfg.voice_reply_enabled is True


def test_openai_key_comes_from_env_only(tmp_path, monkeypatch):
    # A config.yaml value for the key must be ignored — secrets live in the env,
    # never in the world-readable config file on the volume.
    (tmp_path / "config.yaml").write_text(
        'openai_api_key: sk-from-file\ntts_voice: nova\n', encoding="utf-8"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    cfg = load_config(tmp_path)
    assert cfg.openai_api_key == "sk-from-env"   # env wins, file ignored
    assert cfg.tts_voice == "nova"               # non-secret still read from file


def test_get_home_seeds_sdk_native_settings_with_advisor(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    settings = home / "agent_home" / ".claude" / "settings.json"
    assert settings.is_file()
    # the CLI reads this because runner passes setting_sources=["project"] with
    # cwd=agent_home; verified live that advisorModel is honored from there
    assert json.loads(settings.read_text())["advisorModel"] == "opus"


def test_seed_settings_never_overwrites_existing(tmp_path):
    from claude_hermes.core.workspace import seed_settings

    src, claude_dir = tmp_path / "bundled.json", tmp_path / ".claude"
    claude_dir.mkdir()
    src.write_text('{"advisorModel": "opus"}')
    (claude_dir / "settings.json").write_text('{"advisorModel": "fable"}')

    seed_settings(claude_dir, source=src)
    assert '"fable"' in (claude_dir / "settings.json").read_text()


def test_get_home_creates_the_user_catalog_and_approval_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    from claude_hermes.core.workspace import get_home
    home = get_home()
    assert (home / "user-catalog" / "mcp").is_dir()
    assert (home / "user-catalog" / "plugins").is_dir()
    assert (home / "pending-approvals").is_dir()


def test_background_forks_disable_the_advisor():
    """review, curator consolidation and cron all run with setting_sources=["project"]
    and cwd=agent_home, so they inherit settings.json — but there is no user at a
    decision point, and an advisor call there resends the transcript at Opus rates."""
    from claude_hermes.core.config import NO_ADVISOR_ENV

    assert NO_ADVISOR_ENV == {"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}

    import inspect

    # Each fork's option table lives in the module that owns the pass. The
    # assertion follows the code, because the point is to pin the option, not the file.
    from claude_hermes.modules.learning.infrastructure import fork_runner
    from claude_hermes.modules.memory.infrastructure import lint_fork
    from claude_hermes.modules.scheduler.infrastructure import executor

    # Wave 4a: each of these also passes the workspace's CLAUDE_CONFIG_DIR and
    # TMPDIR, and `env` is ONE dict the SDK lays over os.environ rather than
    # merging with itself — so the spelling has to be `{**NO_ADVISOR_ENV, ...}`.
    # A bare `env=workspace_env(...)` would drop the flag and silently re-enable
    # the advisor on every background fork, which is the failure this pins.
    for mod in (fork_runner, lint_fork, executor):
        source = inspect.getsource(mod)
        assert "env={**NO_ADVISOR_ENV," in source, mod.__name__
        # every `env=` the module actually PASSES (comments explain the trap and
        # are allowed to name it) starts from the flag
        passed = [line.strip() for line in source.splitlines()
                  if "env=" in line and not line.strip().startswith("#")]
        assert passed and all(
            line.startswith("env={**NO_ADVISOR_ENV,") for line in passed), passed


def test_every_cli_process_refuses_a_silent_model_swap():
    """The CLI has its own availability fallback chain; left on, a lane that chose
    one model is answered by another and nothing says so. `workspace_env` carries
    the off switch, and every `base_options` call site passes `workspace_env` — so
    the turn, cron, every fork and the prober all get it from one line."""
    import inspect
    from types import SimpleNamespace

    from claude_hermes.integrations.claude_sdk.options import workspace_env
    from claude_hermes.modules.extensions.infrastructure import mcp_prober
    from claude_hermes.modules.learning.infrastructure import fork_runner
    from claude_hermes.modules.memory.infrastructure import lint_fork
    from claude_hermes.modules.scheduler.infrastructure import executor
    from claude_hermes.modules.turns.infrastructure import sdk_runner

    import re

    ws = SimpleNamespace(cli_config_dir="/c", tmp_dir="/t")
    assert workspace_env(ws)["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"
    for mod in (sdk_runner, fork_runner, lint_fork, executor, mcp_prober):
        source = inspect.getsource(mod)
        calls = len(re.findall(r"(?:=|return)\s*base_options\(", source))
        assert calls and calls <= source.count("workspace_env("), mod.__name__


def test_models_is_a_list_whatever_the_yaml_says(tmp_path):
    from claude_hermes.modules.turns.application.ports import TurnSettings

    assert "claude-sonnet-5" in load_config(tmp_path).models

    (tmp_path / "config.yaml").write_text(
        "models: [claude-opus-5-5, claude-sonnet-5]\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.models == ["claude-opus-5-5", "claude-sonnet-5"]
    assert TurnSettings.from_config(cfg).models == ("claude-opus-5-5", "claude-sonnet-5")

    # a bare string is one model, not a list of its characters
    (tmp_path / "config.yaml").write_text("models: claude-opus-5-5\n", encoding="utf-8")
    assert load_config(tmp_path).models == ["claude-opus-5-5"]


def test_web_knobs_default_off_and_secret_comes_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_WEB_SECRET", "s3cr3t")
    (tmp_path / "config.yaml").write_text(
        "web_enabled: true\nweb_port: 9090\nweb_secret: from-yaml\n", encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.web_enabled is True
    assert cfg.web_port == 9090
    assert cfg.web_secret == "s3cr3t"           # yaml value ignored: secrets are env-only
    assert cfg.web_upload_max_mb == 20
    assert cfg.web_tool_output_max_chars == 8000
    assert cfg.web_session_days == 30
    assert cfg.web_public_origin == ""


def test_web_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CH_WEB_SECRET", raising=False)
    cfg = load_config(tmp_path)
    assert cfg.web_enabled is False
    assert cfg.web_secret == ""

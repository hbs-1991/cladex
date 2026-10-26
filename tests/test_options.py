
from claude_hermes.integrations.claude_sdk.options import base_options


def test_base_options_sets_the_invariants(tmp_path):
    o = base_options(tmp_path)
    assert o.cwd == str(tmp_path / "agent_home")
    assert o.setting_sources == ["project"]
    assert o.permission_mode == "bypassPermissions"


def test_stdout_buffer_fits_an_inlined_image(tmp_path):
    """A `Read` of a generated image arrives as one base64 NDJSON line. At the SDK's
    1 MiB default that line kills the turn mid-flight ("Failed to decode JSON"), which
    surfaces to the user as a generic failure with no mention of an image. The floor
    here is the API's 5 MB-per-image ceiling once base64'd."""
    assert base_options(tmp_path).max_buffer_size >= 7 * 1024 * 1024


def test_overrides_win_over_defaults(tmp_path):
    o = base_options(tmp_path, permission_mode="plan", model="claude-haiku-4-5-20251001")
    assert o.permission_mode == "plan"
    assert o.model == "claude-haiku-4-5-20251001"
    assert o.cwd == str(tmp_path / "agent_home")   # untouched defaults survive


def test_env_is_not_an_invariant(tmp_path):
    """The foreground turn keeps the advisor; background forks disable it. The builder
    must not decide that for them."""
    assert base_options(tmp_path).env == {}


def test_extensions_never_reach_the_background_forks():
    """PRD P2: every always-on tool schema is billed on every API call, so extensions are
    scoped to the turn and to cron — never to review, curator or the titler. This is a
    structural guard: the day someone 'helpfully' composes extensions into a fork, it
    fails here rather than on the invoice."""
    import inspect

    from claude_hermes.modules.learning.infrastructure import fork_runner
    from claude_hermes.modules.memory.infrastructure import lint_fork
    from claude_hermes.modules.sessions.infrastructure import title_generator

    # (the review fork legitimately passes mcp_servers={"hermes": ...} — the built-in
    # server is not an extension. What must never appear is the composer.)
    # Retargeted in Phase C with the option tables themselves: `learning/review.py`
    # and `learning/curator.py` are re-export shims now, so guarding them would pass
    # without checking anything. These three modules build the three forks.
    for module in (fork_runner, lint_fork, title_generator):
        source = inspect.getsource(module)
        assert "compose" not in source, f"{module.__name__} must not compose extensions"
        assert "plugins=" not in source, f"{module.__name__} must not load plugins"

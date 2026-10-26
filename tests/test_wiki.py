from claude_hermes.modules.memory.domain.constants import TRUNCATION_NOTE
from claude_hermes.modules.memory.infrastructure.wiki_store import WikiStore


def test_seed_creates_the_tree(tmp_path):
    store = WikiStore(tmp_path)
    store.seed()
    root = tmp_path / "agent_home" / "wiki"
    assert (root / "index.md").is_file()
    assert (root / "log.md").is_file()
    for ns in ("user", "projects", "topics", "env", "raw"):
        assert (root / ns).is_dir()


def test_seed_is_idempotent_and_never_overwrites(tmp_path):
    store = WikiStore(tmp_path)
    store.seed()
    index = tmp_path / "agent_home" / "wiki" / "index.md"
    index.write_text("# Wiki index\n- user/profile.md — the model wrote this\n", encoding="utf-8")
    store.seed()
    # the model owns the content: a second seed must not touch it
    assert "the model wrote this" in index.read_text(encoding="utf-8")


def test_seed_migrates_legacy_memory_files(tmp_path):
    (tmp_path / "MEMORY.md").write_text("- Deploys via docker compose\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("- Prefers concise answers in Russian\n", encoding="utf-8")
    store = WikiStore(tmp_path)
    store.seed()
    root = tmp_path / "agent_home" / "wiki"
    profile = (root / "user" / "profile.md").read_text(encoding="utf-8")
    notes = (root / "env" / "notes.md").read_text(encoding="utf-8")
    assert profile.startswith("---\n") and "title:" in profile and "summary:" in profile
    assert "concise answers in Russian" in profile      # bullets carried over verbatim
    assert "docker compose" in notes
    index = (root / "index.md").read_text(encoding="utf-8")
    assert "user/profile.md" in index and "env/notes.md" in index
    assert "migrated" in (root / "log.md").read_text(encoding="utf-8")


def test_seed_without_legacy_files_leaves_an_empty_index(tmp_path):
    WikiStore(tmp_path).seed()
    index = (tmp_path / "agent_home" / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "user/profile.md" not in index


def test_index_returns_the_file_verbatim_under_the_cap(tmp_path):
    store = WikiStore(tmp_path, index_max_chars=1000)
    store.seed()
    (tmp_path / "agent_home" / "wiki" / "index.md").write_text("# Wiki index\n- a.md — a\n",
                                                               encoding="utf-8")
    assert store.index() == "# Wiki index\n- a.md — a\n"


def test_index_truncates_loudly_over_the_cap(tmp_path):
    store = WikiStore(tmp_path, index_max_chars=50)
    store.seed()
    (tmp_path / "agent_home" / "wiki" / "index.md").write_text("x" * 500, encoding="utf-8")
    out = store.index()
    assert out.startswith("x" * 50)
    assert TRUNCATION_NOTE in out          # never truncate silently
    assert len(out) < 500


def test_index_of_a_missing_wiki_is_empty(tmp_path):
    store = WikiStore(tmp_path)
    assert store.index() == ""
    assert store.index_mtime() == 0.0


def test_index_mtime_moves_when_the_index_is_rewritten(tmp_path):
    store = WikiStore(tmp_path)
    store.seed()
    before = store.index_mtime()
    assert before > 0.0
    index = tmp_path / "agent_home" / "wiki" / "index.md"
    import os
    os.utime(index, (before + 10, before + 10))
    assert store.index_mtime() > before

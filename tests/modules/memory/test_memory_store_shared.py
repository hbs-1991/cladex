"""`WikiStore` and the shared namespace.

The owner's output is a cached prompt prefix that has been stable for months, so
the first test here is a pin: adding a feature for everybody else must not move
one byte of it (P1). For a non-owner the prefix grows by a section naming the
read-only shared wiki — capped at a quarter of the index budget, because a
non-owner pays for their own index too and the point is a split, not a double.
"""
from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.memory.domain.constants import TRUNCATION_NOTE
from claude_hermes.modules.memory.infrastructure.wiki_store import WikiStore


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def owner_index(home, text="# Wiki index\n\n- user/prefs — what I like\n"):
    return write(home / "agent_home" / "wiki" / "index.md", text)


def shared_index(home, text="# Shared\n\n- shared/home — the flat\n"):
    return write(home / "agent_home" / "wiki" / "shared" / "index.md", text)


def test_the_owners_index_is_byte_identical_to_what_it_always_was(tmp_path):
    owner_index(tmp_path)
    shared_index(tmp_path)
    store = WikiStore(tmp_path)
    assert store.index() == "# Wiki index\n\n- user/prefs — what I like\n"
    # …and the owner's own `shared/` pages are inside that index already: the
    # section exists for people who cannot see the tree, not for its author.
    assert WikiStore.for_workspace(Workspace.owner(tmp_path)).index() == store.index()


def test_the_owners_truncation_is_unchanged(tmp_path):
    owner_index(tmp_path, "x" * 50)
    assert WikiStore(tmp_path, 10).index() == "x" * 10 + TRUNCATION_NOTE


def test_a_non_owner_gets_the_shared_section_with_the_path_on_disk(tmp_path):
    ws = Workspace.for_user(tmp_path, "users/7")
    owner_index(tmp_path)
    shared_index(tmp_path)
    write(ws.wiki_root / "index.md", "# My wiki\n\n- user/me — me\n")
    out = WikiStore.for_workspace(ws).index()
    assert out.startswith("# My wiki\n\n- user/me — me\n")
    assert "## Shared wiki (read-only; on disk at " in out
    assert str(ws.shared_wiki_root) in out
    assert "- shared/home — the flat" in out
    # The owner's OWN index is not in it: a shared namespace is not a window.
    assert "user/prefs" not in out


def test_no_shared_index_on_disk_means_no_section(tmp_path):
    ws = Workspace.for_user(tmp_path, "users/7")
    write(ws.wiki_root / "index.md", "# My wiki\n")
    assert WikiStore.for_workspace(ws).index() == "# My wiki\n"
    # An empty file is the same answer: a section with nothing under it is
    # tokens the lane pays for on every call and learns nothing from.
    shared_index(tmp_path, "")
    assert WikiStore.for_workspace(ws).index() == "# My wiki\n"


def test_the_shared_section_gets_a_quarter_of_the_budget(tmp_path):
    ws = Workspace.for_user(tmp_path, "users/7")
    write(ws.wiki_root / "index.md", "m" * 100)
    shared_index(tmp_path, "s" * 500)
    out = WikiStore.for_workspace(ws, 40).index()
    assert out.startswith("m" * 40 + TRUNCATION_NOTE)
    assert "s" * 10 + TRUNCATION_NOTE in out
    assert "s" * 11 not in out


def test_the_mtime_is_the_later_of_the_two(tmp_path):
    ws = Workspace.for_user(tmp_path, "users/7")
    mine = write(ws.wiki_root / "index.md", "# My wiki\n")
    theirs = shared_index(tmp_path)
    import os

    os.utime(mine, (1_000, 1_000))
    os.utime(theirs, (2_000, 2_000))
    store = WikiStore.for_workspace(ws)
    # The index-refresh hook watches this number: the owner editing the shared
    # index has to re-inject for everybody, or a non-owner's frozen prefix keeps
    # a snapshot nobody can correct.
    assert store.index_mtime() == 2_000
    os.utime(mine, (3_000, 3_000))
    assert store.index_mtime() == 3_000


def test_for_workspace_picks_the_shared_root_for_a_non_owner_only(tmp_path):
    assert WikiStore.for_workspace(Workspace.owner(tmp_path))._shared is None
    ws = Workspace.for_user(tmp_path, "users/7")
    assert WikiStore.for_workspace(ws)._shared == ws.shared_wiki_root


def test_a_fake_workspace_with_only_a_root_still_works(tmp_path):
    """Every pre-existing fake has `.root` and nothing else; duck-typed on
    purpose so this change does not rewrite other modules' tests."""
    class OnlyRoot:
        root = tmp_path

    owner_index(tmp_path)
    assert WikiStore.for_workspace(OnlyRoot()).index() == "# Wiki index\n\n- user/prefs — what I like\n"

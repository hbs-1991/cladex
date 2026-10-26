"""WikiGraphService: the directory scan and the containment check, without HTTP.

The containment cases are the security test. They moved out of the router with the
code, so they are asserted here too — the same list the HTTP test walks, one layer
down, so a refactor of the router cannot quietly lose them.
"""
import pytest

from claude_hermes.core.exceptions import NotFound
from claude_hermes.modules.memory.application.services.graph import WikiGraphService

PAGE = (
    "---\ntitle: Nutrition targets\nsummary: calories and macros\n"
    "tags: [nutrition, weight]\nupdated: 2026-07-28\n---\n\n"
    "See [[user/biometrics]] and [[user/training|training]], [[topics/gone#anchor]].\n"
)


@pytest.fixture
def root(tmp_path):
    wiki = tmp_path / "agent_home" / "wiki"

    def write(rel, text):
        path = wiki / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    write("index.md", "# Wiki index\n\n- user/nutrition.md — targets\n")
    write("log.md", "# Wiki log\n")
    write("user/nutrition.md", PAGE)
    write("user/biometrics.md", "---\ntitle: Biometrics\n---\n\nHeight.\n")
    write("user/training.md", "no frontmatter at all\n")
    write("content/linkedin.md", "---\ntitle: LinkedIn voice\n---\n\nTone.\n")
    write("topics/ai/agents.md", "---\ntitle: Agents\n---\n\nNotes.\n")
    (tmp_path / "secret.md").write_text("not memory", encoding="utf-8")
    return wiki


def test_the_scan_finds_a_namespace_the_seed_never_created(root):
    body = WikiGraphService(root).graph()
    assert body["namespaces"] == ["content", "topics", "user"]   # `content` is not seeded
    assert body["missing"] == ["topics/gone"]
    by_path = {p["path"]: p for p in body["pages"]}
    assert "index" not in by_path and "log" not in by_path
    assert by_path["topics/ai/agents"]["ns"] == "topics"         # two rings deep, not three
    assert by_path["user/nutrition"]["in_index"] is True
    assert by_path["user/training"]["in_index"] is False


def test_nothing_outside_the_wiki_resolves(root):
    service = WikiGraphService(root)
    for raw in ("../secret", "user/../../secret", "/etc/passwd", "C:/Windows/x",
                "index", "log", "user/nope", ""):
        with pytest.raises(NotFound):
            service.resolve(raw)


def test_a_page_comes_back_with_its_bytes(root):
    detail, content = WikiGraphService(root).page("user/nutrition")
    assert detail["path"] == "user/nutrition" and detail["title"] == "Nutrition targets"
    assert content == PAGE.encode("utf-8")

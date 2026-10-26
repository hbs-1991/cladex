# Memory as an LLM Wiki — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two flat, char-capped memory files with `agent_home/wiki/` — a linked wiki the model owns and reads from disk with the tools it already has.

**Architecture:** Code seeds the wiki tree, injects `wiki/index.md` into the system prompt at session creation, and gates writes with hooks; the model writes every byte of the content. `LearningLoop` becomes the *ingest* pass (conversation → pages), the curator gains a *lint* phase (contradictions, orphans, broken links), and a `Stop` hook makes it impossible to finish a turn that changed a page without updating the index and log.

**Tech Stack:** Python 3.12+, `claude-agent-sdk` (hooks: `PreToolUse`, `PostToolUse`, `Stop`, `UserPromptSubmit`), aiosqlite, pytest (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-14-memory-llm-wiki-design.md`

## Global Constraints

- **The model owns the wiki. Code never writes wiki content** — only seeds the tree, reads `index.md`, and gates writes. No code path may author, rewrite, or reformat a page, the index, or the log.
- **No new MCP tool.** The agent reaches the wiki with `Read`/`Glob`/`Grep`/`Write`/`Edit`, which it already has.
- **`memory.py`, `memory_save`, `memory_max_chars`, `user_max_chars`, `memory_nudge_interval` are deleted.** Exactly one way to write memory: files under `wiki/`.
- **The wiki schema lives in `prompt.WIKI_RULES` (code), not in `bundle/CLAUDE.md`.** `paths._copy_missing` seeds a file only when absent, so a `CLAUDE.md` edit never reaches an existing `agent_home`. Do not put wiki rules in `bundle/CLAUDE.md`.
- **`wiki/raw/` is immutable:** no `Edit`, no overwrite of an existing file. Enforced by a `PreToolUse` hook.
- **Tests never touch the network.** Every hook is a plain async function called directly with a dict; every filesystem test uses `tmp_path`.
- **Existing behaviour that must not regress:** the `ProcessError` resume-recovery path in `runner.py`, the skills write guard (`.usage.json` and `.archive` stay protected, an unparseable path fails closed), and `messages_fts` / `session_search`.
- Run tests with `python -m pytest` from the repo root. Commit after every task.
- **Heads-up:** another session has uncommitted work in `src/claude_hermes/tools.py` and `bundle/CLAUDE.md`. Before starting, run `git status`; if those files are dirty, rebase/coordinate before Task 4 (which edits `tools.py`).

## File structure

| File | Responsibility |
|---|---|
| **new** `src/claude_hermes/wiki.py` | `WikiStore`: `root`, `index()`, `index_mtime()`, `seed()`. No content logic. |
| **new** `src/claude_hermes/hooks.py` | The wiki hooks: raw immutability, page-touch tracking, the `Stop` gate, the `WebFetch` hint, the index refresh. |
| **new** `skills/wiki-ingest/SKILL.md` | Bundled skill: the procedure for filing an external source. |
| **delete** `src/claude_hermes/memory.py` | Replaced by `wiki.py`. |
| `src/claude_hermes/prompt.py` | Injects `WIKI_RULES` + the index instead of the memory snapshot. |
| `src/claude_hermes/tools.py` | Loses `memory_save` and the `store` parameter. |
| `src/claude_hermes/runner.py` | Holds a `WikiStore`, wires the four hook events, tracks the injected index's mtime per session. |
| `src/claude_hermes/learning/review.py` | Guard takes **roots**; the ingest fork may write `skills/` and `wiki/`. |
| `src/claude_hermes/learning/prompts.py` | `_WIKI_SECTION` replaces `_MEMORY_SECTION`; adds the lint prompt. |
| `src/claude_hermes/learning/observe.py` | `used_memory` → `wrote_wiki`. |
| `src/claude_hermes/learning/nudges.py` | Counter keyed on wiki writes; `wiki_nudge_interval`. |
| `src/claude_hermes/learning/curator.py` | A second phase: wiki lint, snapshotted first. |
| `src/claude_hermes/learning/backup.py` | `kind="skills" \| "wiki"` — two snapshot stores, one implementation. |
| `src/claude_hermes/config.py` | Drops three keys, adds four. |
| `src/claude_hermes/main.py` | Builds and seeds the `WikiStore`. |

---

### Task 1: Spike — does a `Stop`-hook block resume the turn?

The whole consistency design rests on `{"decision": "block"}` sending the model back to work rather than aborting the turn. Open Question #1 in the spec. **No production code in this task** — its deliverable is a verdict.

**Files:**
- Create: `scratch/spike_stop_hook.py` (throwaway; not committed)

- [ ] **Step 1: Write the spike**

```python
# scratch/spike_stop_hook.py  — run manually, needs a live API key
import anyio
from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, query

blocked = {"count": 0}


async def stop_gate(input_data, tool_use_id, context) -> dict:
    if input_data.get("stop_hook_active"):
        print("STOP HOOK: stop_hook_active is True — not blocking again")
        return {}
    blocked["count"] += 1
    print("STOP HOOK: blocking, asking for one more word")
    return {"decision": "block", "reason": "Append the word BANANA to your answer, then finish."}


async def main() -> None:
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5-20251001",
        max_turns=4,
        permission_mode="bypassPermissions",
        setting_sources=[],
        hooks={"Stop": [HookMatcher(hooks=[stop_gate])]},
    )
    final = None
    async for msg in query(prompt="Say hello in one short sentence.", options=options):
        if getattr(msg, "result", None):
            final = msg.result
    print("blocks fired:", blocked["count"])
    print("final text:", final)
    print("VERDICT:", "RESUMES" if final and "BANANA" in final.upper() else "DOES NOT RESUME")


anyio.run(main)
```

- [ ] **Step 2: Run it**

Run: `python scratch/spike_stop_hook.py`
Expected: `blocks fired: 1` and `VERDICT: RESUMES` — the model gets the reason, appends the word, and the turn completes normally.

- [ ] **Step 3: Record the verdict**

If **RESUMES**: proceed with the plan unchanged.

If **DOES NOT RESUME** (the block surfaces as an error, or the turn ends without the extra word): stop and report. The fallback is already designed — drop the `Stop` gate, keep `hooks.py`'s `PostToolUse` page-touch hook, and turn it into a soft reminder (`additionalContext`: "you touched wiki pages; update index.md and log.md before you finish"), letting the lint phase reconcile the drift. Tasks 5 and 6 then omit the `Stop` matcher; nothing else changes.

- [ ] **Step 4: Delete the spike**

Run: `rm -rf scratch/`
Nothing to commit.

---

### Task 2: `wiki.py` — the store

**Files:**
- Create: `src/claude_hermes/wiki.py`
- Modify: `src/claude_hermes/config.py:22-23` (drop `memory_max_chars`, `user_max_chars`; add `wiki_index_max_chars`)
- Test: `tests/test_wiki.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `WikiStore(home: Path, index_max_chars: int = 8000)` with `.root -> Path` (`home/agent_home/wiki`), `.index() -> str`, `.index_mtime() -> float`, `.seed() -> None`. Module constant `TRUNCATION_NOTE: str`. Config key `wiki_index_max_chars: int = 8000`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wiki.py
from claude_hermes.wiki import TRUNCATION_NOTE, WikiStore


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.wiki'`

- [ ] **Step 3: Write `src/claude_hermes/wiki.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_INDEX = "index.md"
_LOG = "log.md"
_NAMESPACES = ("user", "projects", "topics", "env", "raw")

TRUNCATION_NOTE = (
    "\n\n[index truncated — read wiki/index.md from disk before concluding a page is missing]"
)

_INDEX_HEADER = "# Wiki index\n\nEvery page, one line each: `- <path> — <summary>`. Keep it grouped\nand current; it is what the agent sees at session start.\n"
_LOG_HEADER = "# Wiki log\n\nAppend-only. One line per ingest, query or lint pass.\n"

# (legacy file, destination page, title, summary)
_MIGRATIONS = (
    ("USER.md", "user/profile.md", "User profile",
     "who the user is — carried over from the old flat memory"),
    ("MEMORY.md", "env/notes.md", "Environment and project notes",
     "environment and project facts — carried over from the old flat memory"),
)


class WikiStore:
    """Thin accessor for agent_home/wiki.

    The model owns every byte of the content. This class only seeds the tree,
    reads the index for the system prompt, and reports the index's mtime so a
    hook can tell when a background pass changed it.
    """

    def __init__(self, home: Path, index_max_chars: int = 8000):
        self._home = home
        self._max = index_max_chars

    @property
    def root(self) -> Path:
        return self._home / "agent_home" / "wiki"

    def index(self) -> str:
        try:
            text = (self.root / _INDEX).read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(text) <= self._max:
            return text
        return text[: self._max] + TRUNCATION_NOTE

    def index_mtime(self) -> float:
        try:
            return (self.root / _INDEX).stat().st_mtime
        except OSError:
            return 0.0

    def seed(self) -> None:
        """Create the tree and, once, carry the legacy flat memory into it.

        Idempotent: an existing index.md means the wiki is the model's now, and
        nothing here may touch it again.
        """
        if (self.root / _INDEX).is_file():
            return
        for name in _NAMESPACES:
            (self.root / name).mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).date().isoformat()
        migrated = self._migrate_legacy(today)

        index = _INDEX_HEADER + "\n" + "".join(
            f"- {rel} — {summary}\n" for rel, summary in migrated
        )
        (self.root / _INDEX).write_text(index, encoding="utf-8")

        log = _LOG_HEADER
        if migrated:
            pages = ", ".join(rel for rel, _ in migrated)
            log += f"\n- {today} seed: migrated legacy memory into {pages}\n"
        (self.root / _LOG).write_text(log, encoding="utf-8")

    def _migrate_legacy(self, today: str) -> list[tuple[str, str]]:
        """Copy MEMORY.md/USER.md in verbatim, wrapped in frontmatter.

        Code does not restructure them — the first lint pass does. The originals
        are left on disk untouched.
        """
        out: list[tuple[str, str]] = []
        for legacy, rel, title, summary in _MIGRATIONS:
            try:
                body = (self._home / legacy).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not body:
                continue
            page = (
                "---\n"
                f"title: {title}\n"
                f"summary: {summary}\n"
                "tags: [migrated]\n"
                f"updated: {today}\n"
                "---\n\n"
                f"{body}\n"
            )
            (self.root / rel).write_text(page, encoding="utf-8")
            out.append((rel, summary))
        return out
```

- [ ] **Step 4: Update the config**

In `src/claude_hermes/config.py`, delete lines 22-23 (`memory_max_chars`, `user_max_chars`) and add, in their place:

```python
    wiki_index_max_chars: int = 8000   # above this the injected index is truncated, loudly
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_wiki.py -v`
Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/wiki.py src/claude_hermes/config.py tests/test_wiki.py
git commit -m "feat(wiki): the wiki store — seed, migrate, read the index"
```

---

### Task 3: `prompt.py` — inject the rules and the index

**Files:**
- Modify: `src/claude_hermes/prompt.py` (whole file)
- Test: `tests/test_prompt.py` (rewrite)

**Interfaces:**
- Consumes: `WikiStore.index() -> str` (Task 2).
- Produces: `prompt.WIKI_RULES: str` and `build_system_append(wiki_index: str, today: date) -> str`. The parameter is now the wiki index, not a memory snapshot.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_prompt.py
from datetime import date

from claude_hermes.prompt import WIKI_RULES, build_system_append


def test_append_carries_the_rules_the_index_and_the_date():
    out = build_system_append("# Wiki index\n- user/profile.md — who they are\n", date(2026, 7, 14))
    assert WIKI_RULES in out                      # the schema is code-owned, not in CLAUDE.md
    assert "user/profile.md" in out
    assert "2026-07-14" in out
    assert ":" not in out.splitlines()[-1]        # date-only stamp, no clock time


def test_rules_are_present_even_with_an_empty_wiki():
    out = build_system_append("", date(2026, 7, 14))
    assert WIKI_RULES in out
    assert "Wiki index (snapshot" not in out      # no empty section header


def test_rules_tell_the_model_the_index_may_be_stale():
    assert "index.md" in WIKI_RULES
    assert "raw/" in WIKI_RULES


def test_append_stable_for_same_inputs():
    a = build_system_append("idx", date(2026, 7, 14))
    b = build_system_append("idx", date(2026, 7, 14))
    assert a == b                                  # byte-stable → the prompt cache stays warm
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'WIKI_RULES'`

- [ ] **Step 3: Rewrite `src/claude_hermes/prompt.py`**

```python
from __future__ import annotations

from datetime import date

# The persona and the standing operating rules live in agent_home/CLAUDE.md, seeded by
# paths.seed_claude_md. The WIKI RULES deliberately do NOT: paths._copy_missing seeds a
# file only when it is absent, so an edit to bundle/CLAUDE.md never reaches an agent_home
# that already exists. A live deployment would then run the new memory system with none of
# its rules. They are static, so carrying them in the append costs nothing: the prefix
# stays byte-stable and the provider cache stays warm.

WIKI_RULES = """\
# Memory: your wiki

Your durable memory is the `wiki/` directory in your working area. You own every byte of it —
no code writes it for you.

- A page is frontmatter (`title`, `summary`, `tags`, `updated`; plus `source:` when it was
  distilled from a source) + prose + `[[links]]` to related pages. One page per entity or concept.
- Namespaces: `user/`, `projects/`, `topics/`, `env/`. Sources are stored verbatim under `raw/`
  and are immutable — never edit one; write a page about it instead.
- The index below is a snapshot taken when this session started, and the wiki changes under you.
  Re-read `wiki/index.md` from disk before concluding that something is not there.
- Whenever you touch a page, update `wiki/index.md` and append a line to `wiki/log.md` in the same
  turn. A hook will stop you if you forget.
- Do NOT interrupt a conversation to curate the wiki — a background pass does that. The one
  exception: the user explicitly asks you to remember something. Then write it now and say so."""


def build_system_append(wiki_index: str, today: date) -> str:
    parts = [WIKI_RULES]
    if wiki_index:
        parts.append("## Wiki index (snapshot at session start)\n\n" + wiki_index)
    parts.append(f"Today's date is {today.isoformat()}")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_prompt.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/prompt.py tests/test_prompt.py
git commit -m "feat(prompt): the wiki rules and index replace the memory snapshot"
```

---

### Task 4: Delete the old memory

`memory.py`, the `memory_save` tool, and the `store` parameter that carried them through half the codebase. After this task the tree is green with **no** memory subsystem — the wiki is wired in but nothing writes to it yet.

**Files:**
- Delete: `src/claude_hermes/memory.py`, `tests/test_memory.py`
- Modify: `src/claude_hermes/tools.py` (`make_handlers`, `build_hermes_server`), `src/claude_hermes/runner.py`, `src/claude_hermes/learning/review.py`, `src/claude_hermes/learning/loop.py`, `src/claude_hermes/main.py`
- Test: `tests/test_tools.py`, `tests/test_runner.py`, `tests/test_review.py`, `tests/test_learning_loop.py` (fix the constructors)

**Interfaces:**
- Consumes: `WikiStore` (Task 2), `build_system_append(wiki_index, today)` (Task 3).
- Produces: `make_handlers(db, ctx=None, job_store=None, cfg=None, sidecar=None, skills_dir=None, topics=None, ext_store=None, acl=None, home=None, catalog_root=None)` and `build_hermes_server(db, ...)` — both **without** the leading `store` argument. `AgentRunner(cfg, home, db, router, wiki)` takes a `WikiStore`. `run_review(cfg, home, db, sidecar, session_key, kinds)` — no `store=` keyword. `LearningLoop(cfg, home, db, sidecar, tracker)` — no `store=` keyword.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools.py — add at the top of the file
def test_memory_save_is_gone():
    """One way to write memory: files under wiki/. A second path would drift."""
    from claude_hermes.tools import build_hermes_server
    from claude_hermes.db import Database  # noqa: F401  (import shape check only)
    import inspect

    sig = inspect.signature(build_hermes_server)
    assert "store" not in sig.parameters
    assert list(sig.parameters)[0] == "db"
```

And in `tests/test_tools.py`, every existing `build_hermes_server(store, db, ...)` / `make_handlers(store, db, ...)` call loses its first argument. Same in `tests/test_review.py` and `tests/test_learning_loop.py`: drop the `store=` keyword from `LearningLoop(...)` and `run_review(...)`.

`tests/test_runner.py` needs its helper rewritten (it currently builds a `MemoryStore` and two tests drive memory through `store.save(...)`):

```python
# tests/test_runner.py
from claude_hermes.wiki import WikiStore          # replaces the MemoryStore import


async def make_runner(tmp_path):
    cfg = Config()
    db = await Database.open(tmp_path / "state.db")
    router = SessionRouter(db, cfg)
    wiki = WikiStore(tmp_path)
    wiki.seed()
    return AgentRunner(cfg, tmp_path, db, router, wiki), db, wiki
```

`test_new_session_binds_and_replies` asserted `"Name is Alex" in row["system_append"]` after a `store.save`. The wiki equivalent — write the page, then assert the *index* reached the prompt:

```python
async def test_new_session_binds_and_replies(tmp_path, monkeypatch):
    runner, db, wiki = await make_runner(tmp_path)
    (wiki.root / "index.md").write_text("# Wiki index\n- user/profile.md — Alex\n",
                                        encoding="utf-8")
    captured = []
    monkeypatch.setattr(runner_mod, "query", make_fake_query(captured))
    reply = await runner.run_turn("tg:private:1", "hi")
    assert reply == "hello from agent"
    row = await db.get_session("tg:private:1")
    assert row["sdk_session_id"] == "sdk-xyz"
    assert "user/profile.md — Alex" in row["system_append"]
    assert captured[0].resume is None
    hits = await db.search_messages("hello")
    assert any(h["role"] == "assistant" for h in hits)
    await db.close()
```

`test_resumed_session_reuses_stored_append` keeps its point — the append is frozen — with the index as the thing that changes:

```python
async def test_resumed_session_reuses_stored_append(tmp_path, monkeypatch):
    runner, db, wiki = await make_runner(tmp_path)
    captured = []
    monkeypatch.setattr(runner_mod, "query", make_fake_query(captured))
    await runner.run_turn("tg:private:1", "first")
    (wiki.root / "index.md").write_text("# Wiki index\n- topics/new.md — written mid-session\n",
                                        encoding="utf-8")
    await runner.run_turn("tg:private:1", "second")
    assert captured[1].resume == "sdk-xyz"
    # the append stays frozen — freshness is the UserPromptSubmit hook's job, not the prompt's
    assert "written mid-session" not in _append_of(captured[1])
    await db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL — `assert 'store' not in sig.parameters`

- [ ] **Step 3: Cut `memory_save` out of `tools.py`**

In `src/claude_hermes/tools.py`:

1. Delete the import `from .memory import MemoryStore`.
2. Change the signature `def make_handlers(store: MemoryStore, db: Database, ctx=..., ...)` to `def make_handlers(db: Database, ctx: TurnContext | None = None, ...)` — the rest of the parameter list is unchanged.
3. Delete the whole `async def memory_save(args: dict) -> dict:` handler.
4. Delete `"memory_save": memory_save,` from the `handlers` dict.
5. Delete the `memory_save = tool(...)` registration block and remove `memory_save` from `tools_list` and `"mcp__hermes__memory_save"` from `names`.
6. Change `def build_hermes_server(store: MemoryStore, db: Database, ...)` to `def build_hermes_server(db: Database, ...)` and update its internal `make_handlers(store, db, ...)` call to `make_handlers(db, ...)`.

- [ ] **Step 4: Rewire the call sites**

`src/claude_hermes/runner.py`:

```python
# imports
from .wiki import WikiStore          # replaces: from .memory import MemoryStore

class AgentRunner:
    def __init__(
        self, cfg: Config, home: Path, db: Database,
        router: SessionRouter, wiki: WikiStore,
    ):
        ...
        self._wiki = wiki             # replaces: self._store = store
        self._index_seen: dict[str, float] = {}   # session_key -> index mtime already in context
```

In `_run_turn_locked`, both `build_system_append(self._store.snapshot(), now.date())` calls become:

```python
            append = build_system_append(self._wiki.index(), now.date())
            self._index_seen[session_key] = self._wiki.index_mtime()
```

(The second assignment is what Task 6's `UserPromptSubmit` hook compares against. Setting it here — where a *fresh* append is built — is what makes "the index in context is this old" true by construction.)

In `_execute`, `build_hermes_server(self._store, self._db, ...)` loses its first argument:

```python
        server, mcp_tool_names = build_hermes_server(
            self._db, ctx=ctx, job_store=self._job_store, cfg=self._cfg,
            sidecar=self._sidecar, skills_dir=self._skills_dir, topics=self._topics,
            ext_store=self._ext_store, acl=self._acl, home=self._home,
            catalog_root=self._catalog_root,
        )
```

`src/claude_hermes/learning/review.py`: delete `from ..memory import MemoryStore`; change the signature to

```python
async def run_review(
    cfg: Config, home: Path, db: Database, sidecar: UsageSidecar,
    session_key: str, kinds: set[str],
) -> str | None:
```

and the server build to `server, _ = build_hermes_server(db)`. Also drop `"mcp__hermes__memory_save"` from `REVIEW_TOOLS`:

```python
REVIEW_BUILTIN_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]
REVIEW_TOOLS = list(REVIEW_BUILTIN_TOOLS)   # no MCP tools: the wiki is plain files
```

`src/claude_hermes/learning/loop.py`: delete `from ..memory import MemoryStore`, drop the `store` parameter and `self._store`, and call `run_review(self._cfg, self._home, self._db, self._sidecar, session_key, kinds)`.

`src/claude_hermes/main.py`:

```python
from .wiki import WikiStore          # replaces: from .memory import MemoryStore
...
    wiki = WikiStore(home, cfg.wiki_index_max_chars)
    wiki.seed()
    runner = AgentRunner(cfg, home, db, router, wiki)
    ...
    learning = LearningLoop(cfg, home, db, sidecar, NudgeTracker(db, cfg))
```

- [ ] **Step 5: Delete the dead files**

```bash
git rm src/claude_hermes/memory.py tests/test_memory.py
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green. If a test still constructs `MemoryStore`, it was missed in Step 1 — fix it, do not re-add the module.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(memory): delete memory.py and memory_save; the runner holds a WikiStore"
```

---

### Task 5: `hooks.py` — the five wiki hooks

**Files:**
- Create: `src/claude_hermes/hooks.py`
- Test: `tests/test_wiki_hooks.py`

**Interfaces:**
- Consumes: `WikiStore` (Task 2).
- Produces:
  - `WikiTurnState` — dataclass: `pages: set[str]`, `index_written: bool`, `log_written: bool`, `fetch_hinted: bool`.
  - `raw_guard_factory(wiki_root: Path)` → `PreToolUse(Write|Edit)` hook.
  - `page_touch_factory(state: WikiTurnState, wiki_root: Path)` → `PostToolUse(Write|Edit)` hook.
  - `stop_gate_factory(state: WikiTurnState)` → `Stop` hook.
  - `webfetch_hint_factory(state: WikiTurnState)` → `PostToolUse(WebFetch)` hook.
  - `index_refresh_factory(wiki: WikiStore, seen: dict[str, float], session_key: str)` → `UserPromptSubmit` hook.
  - Every hook has the SDK signature `async def hook(input_data: dict, tool_use_id, context) -> dict` and returns `{}` for "no opinion".

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_wiki_hooks.py
import pytest

from claude_hermes.hooks import (
    WikiTurnState,
    index_refresh_factory,
    page_touch_factory,
    raw_guard_factory,
    stop_gate_factory,
    webfetch_hint_factory,
)
from claude_hermes.wiki import WikiStore


@pytest.fixture
def wiki(tmp_path):
    store = WikiStore(tmp_path)
    store.seed()
    return store


def _write(path, tool="Write"):
    return {"tool_name": tool, "tool_input": {"file_path": str(path)}}


def _denied(out) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


async def test_raw_guard_denies_editing_a_source(wiki):
    src = wiki.root / "raw" / "article.md"
    src.write_text("original", encoding="utf-8")
    guard = raw_guard_factory(wiki.root)
    assert _denied(await guard(_write(src, tool="Edit"), None, None))


async def test_raw_guard_denies_overwriting_a_source(wiki):
    src = wiki.root / "raw" / "article.md"
    src.write_text("original", encoding="utf-8")
    guard = raw_guard_factory(wiki.root)
    assert _denied(await guard(_write(src, tool="Write"), None, None))


async def test_raw_guard_allows_a_new_source(wiki):
    guard = raw_guard_factory(wiki.root)
    out = await guard(_write(wiki.root / "raw" / "fresh.md"), None, None)
    assert out == {}


async def test_raw_guard_ignores_everything_outside_raw(wiki):
    guard = raw_guard_factory(wiki.root)
    page = wiki.root / "topics" / "llm-wiki.md"
    page.write_text("x", encoding="utf-8")
    assert await guard(_write(page, tool="Edit"), None, None) == {}


async def test_page_touch_records_pages_index_and_log_separately(wiki):
    state = WikiTurnState()
    hook = page_touch_factory(state, wiki.root)
    await hook(_write(wiki.root / "user" / "profile.md"), None, None)
    await hook(_write(wiki.root / "index.md"), None, None)
    await hook(_write(wiki.root / "log.md"), None, None)
    await hook(_write(wiki.root / "raw" / "src.md"), None, None)
    assert state.pages == {"user/profile.md"}      # raw/ is a source, not a page
    assert state.index_written and state.log_written


async def test_page_touch_ignores_writes_outside_the_wiki(wiki, tmp_path):
    state = WikiTurnState()
    hook = page_touch_factory(state, wiki.root)
    await hook(_write(tmp_path / "somewhere" / "else.md"), None, None)
    assert state.pages == set()


async def test_stop_gate_blocks_a_page_written_without_the_index(wiki):
    state = WikiTurnState(pages={"user/profile.md"})
    out = await stop_gate_factory(state)({"stop_hook_active": False}, None, None)
    assert out["decision"] == "block"
    assert "user/profile.md" in out["reason"]
    assert "index.md" in out["reason"] and "log.md" in out["reason"]


async def test_stop_gate_passes_when_index_and_log_were_written(wiki):
    state = WikiTurnState(pages={"user/profile.md"}, index_written=True, log_written=True)
    assert await stop_gate_factory(state)({"stop_hook_active": False}, None, None) == {}


async def test_stop_gate_passes_when_no_page_was_touched(wiki):
    assert await stop_gate_factory(WikiTurnState())({"stop_hook_active": False}, None, None) == {}


async def test_stop_gate_never_blocks_twice(wiki):
    """stop_hook_active is the loop breaker: one nudge, then let the turn finish."""
    state = WikiTurnState(pages={"user/profile.md"})
    assert await stop_gate_factory(state)({"stop_hook_active": True}, None, None) == {}


async def test_webfetch_hint_fires_once_per_turn(wiki):
    state = WikiTurnState()
    hook = webfetch_hint_factory(state)
    first = await hook({"tool_name": "WebFetch"}, None, None)
    second = await hook({"tool_name": "WebFetch"}, None, None)
    assert "wiki-ingest" in first["hookSpecificOutput"]["additionalContext"]
    assert second == {}


async def test_index_refresh_injects_only_when_the_index_moved(wiki):
    seen: dict[str, float] = {"k": wiki.index_mtime()}
    hook = index_refresh_factory(wiki, seen, "k")
    assert await hook({"prompt": "hi"}, None, None) == {}      # unchanged → silence

    index = wiki.root / "index.md"
    index.write_text("# Wiki index\n- topics/new.md — fresh\n", encoding="utf-8")
    import os
    later = wiki.index_mtime() + 10
    os.utime(index, (later, later))

    out = await hook({"prompt": "hi"}, None, None)
    assert "topics/new.md" in out["hookSpecificOutput"]["additionalContext"]
    assert await hook({"prompt": "hi"}, None, None) == {}      # and only once
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_wiki_hooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'claude_hermes.hooks'`

- [ ] **Step 3: Write `src/claude_hermes/hooks.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .wiki import WikiStore

log = logging.getLogger(__name__)

_INDEX = "index.md"
_LOG = "log.md"

_INGEST_HINT = (
    "You just fetched an external source. If it is worth keeping, load the `wiki-ingest` "
    "skill and file it: the text under wiki/raw/, a page under wiki/topics/, links into the "
    "pages it touches, then the index and the log. If it was throwaway, ignore this."
)


@dataclass
class WikiTurnState:
    """What this turn did to the wiki. One instance per turn; the hooks share it."""

    pages: set[str] = field(default_factory=set)
    index_written: bool = False
    log_written: bool = False
    fetch_hinted: bool = False


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _target(input_data: dict) -> Path | None:
    raw = str((input_data.get("tool_input") or {}).get("file_path", ""))
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except OSError:
        return None


def raw_guard_factory(wiki_root: Path):
    """PreToolUse(Write|Edit): wiki/raw/ is immutable.

    A source that can be quietly amended is not a source — and lint's ability to
    check a claim against its origin depends on the origin being untouched.
    """
    raw_root = (wiki_root / "raw").resolve()

    async def guard(input_data: dict, tool_use_id, context) -> dict:
        path = _target(input_data)
        if path is None or not path.is_relative_to(raw_root):
            return {}
        if input_data.get("tool_name") == "Edit":
            return _deny(
                "wiki/raw/ is immutable: never edit a source. Write a page about it instead."
            )
        if path.exists():
            return _deny(
                f"wiki/raw/{path.name} already exists and sources are immutable. "
                "Use a new filename, or write a page instead."
            )
        return {}

    return guard


def page_touch_factory(state: WikiTurnState, wiki_root: Path):
    """PostToolUse(Write|Edit): remember what this turn did to the wiki."""
    root = wiki_root.resolve()

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        path = _target(input_data)
        if path is None or not path.is_relative_to(root):
            return {}
        rel = path.relative_to(root).as_posix()
        if rel == _INDEX:
            state.index_written = True
        elif rel == _LOG:
            state.log_written = True
        elif not rel.startswith("raw/"):
            state.pages.add(rel)
        return {}

    return hook


def stop_gate_factory(state: WikiTurnState):
    """Stop: a page changed without the index and the log is a wiki that lies."""

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        if not state.pages:
            return {}
        missing = [
            name
            for name, done in (("wiki/index.md", state.index_written),
                               ("wiki/log.md", state.log_written))
            if not done
        ]
        if not missing:
            return {}
        if input_data.get("stop_hook_active"):
            # Already nudged once this turn. Blocking again would trap the turn;
            # let it finish and leave the drift to the lint pass.
            log.warning(
                "wiki drift: pages %s written without %s",
                sorted(state.pages), ", ".join(missing),
            )
            return {}
        pages = ", ".join(sorted(state.pages))
        return {
            "decision": "block",
            "reason": (
                f"You changed wiki pages ({pages}) but did not update "
                f"{' and '.join(missing)}. Do that now — one line per page in the index, "
                "one line in the log — then finish."
            ),
        }

    return hook


def webfetch_hint_factory(state: WikiTurnState):
    """PostToolUse(WebFetch): point at the ingest skill. Once per turn, not per fetch."""

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        if state.fetch_hinted:
            return {}
        state.fetch_hinted = True
        return {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": _INGEST_HINT,
            }
        }

    return hook


def index_refresh_factory(wiki: WikiStore, seen: dict[str, float], session_key: str):
    """UserPromptSubmit: the index in the prompt is a snapshot; keep it honest.

    In an eternal thread the system prompt is frozen, so a page written by a
    background pass would otherwise stay invisible. Fires only when the index
    actually moved — an unchanged wiki costs zero tokens.
    """

    async def hook(input_data: dict, tool_use_id, context) -> dict:
        mtime = wiki.index_mtime()
        if mtime <= seen.get(session_key, 0.0):
            return {}
        seen[session_key] = mtime
        index = wiki.index()
        if not index:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    "The wiki changed since this session started. Current wiki/index.md:\n\n"
                    + index
                ),
            }
        }

    return hook
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_wiki_hooks.py -v`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/hooks.py tests/test_wiki_hooks.py
git commit -m "feat(wiki): hooks — raw is immutable, the index cannot drift, the fetch hint"
```

---

### Task 6: Wire the hooks into the turn

**Files:**
- Modify: `src/claude_hermes/runner.py:133-158` (`_execute`)
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: everything from Task 5, plus `self._wiki` / `self._index_seen` from Task 4.
- Produces: `AgentRunner._execute` now passes four hook events. No signature change.

- [ ] **Step 1: Write the failing test**

`tests/test_runner.py` already has `make_runner(tmp_path)` (line 31) and `make_fake_query(captured)`
(line 22). Task 4 changed `make_runner` to build a `WikiStore`; it now returns `(runner, db, wiki)`.

```python
# tests/test_runner.py — add

async def test_turn_wires_all_four_wiki_hook_events(tmp_path, monkeypatch):
    runner, db, wiki = await make_runner(tmp_path)
    captured = []
    monkeypatch.setattr(runner_mod, "query", make_fake_query(captured))
    await runner.run_turn("tg:private:1", "hi")

    hooks = captured[0].hooks
    assert set(hooks) == {"PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit"}
    pre = [m.matcher for m in hooks["PreToolUse"]]
    assert "Bash" in pre and "Write|Edit" in pre        # the bash guard is still there
    post = [m.matcher for m in hooks["PostToolUse"]]
    assert "Write|Edit" in post and "WebFetch" in post
    await db.close()


async def test_a_fresh_session_records_the_index_it_injected(tmp_path, monkeypatch):
    """The UserPromptSubmit hook compares against this. If it is not recorded at
    append-build time, the hook re-injects the index on every single message."""
    runner, db, wiki = await make_runner(tmp_path)
    wiki.seed()
    captured = []
    monkeypatch.setattr(runner_mod, "query", make_fake_query(captured))
    await runner.run_turn("tg:private:1", "hi")
    assert runner._index_seen["tg:private:1"] == wiki.index_mtime()
    await db.close()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_runner.py::test_turn_wires_all_four_wiki_hook_events -v`
Expected: FAIL — `hooks` has only `PreToolUse`.

- [ ] **Step 3: Wire the hooks in `_execute`**

Add the import in `src/claude_hermes/runner.py`:

```python
from .hooks import (
    WikiTurnState,
    index_refresh_factory,
    page_touch_factory,
    raw_guard_factory,
    stop_gate_factory,
    webfetch_hint_factory,
)
```

and, in `_execute`, replace the single-line `hooks={...}` argument to `base_options` with:

```python
        state = WikiTurnState()
        wiki_root = self._wiki.root
        hooks = {
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[bash_guard]),
                HookMatcher(matcher="Write|Edit", hooks=[raw_guard_factory(wiki_root)]),
            ],
            "PostToolUse": [
                HookMatcher(matcher="Write|Edit",
                            hooks=[page_touch_factory(state, wiki_root)]),
                HookMatcher(matcher="WebFetch", hooks=[webfetch_hint_factory(state)]),
            ],
            "Stop": [HookMatcher(hooks=[stop_gate_factory(state)])],
            "UserPromptSubmit": [HookMatcher(
                hooks=[index_refresh_factory(self._wiki, self._index_seen, session_key)]
            )],
        }
        options = base_options(
            ...                       # unchanged
            hooks=hooks,
            resume=resume_id,
        )
```

`state` is per-turn by construction — a fresh `WikiTurnState()` on every `_execute`, shared by the three hooks that need it.

- [ ] **Step 4: Run the suite**

Run: `python -m pytest tests/test_runner.py -v`
Expected: all green, including the new test.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/runner.py tests/test_runner.py
git commit -m "feat(runner): wire the wiki hooks into every turn"
```

---

### Task 7: The ingest fork

The background review pass stops saving bullets through an MCP tool and starts writing wiki pages.

**Files:**
- Modify: `src/claude_hermes/learning/review.py` (guard + fork), `src/claude_hermes/learning/prompts.py` (`_MEMORY_SECTION` → `_WIKI_SECTION`)
- Test: `tests/test_review.py`, `tests/test_learning_prompts.py`

**Interfaces:**
- Consumes: `WikiTurnState`, `page_touch_factory`, `stop_gate_factory` (Task 5).
- Produces: `write_guard_factory(roots: list[Path], *, skills_root: Path | None = None, touched: set[str] | None = None)`. `skills_write_guard_factory(skills_dir, touched=None)` stays as a thin wrapper (the curator calls it). `build_review_prompt(kinds)` where `kinds ⊆ {"wiki", "skills"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_review.py — add
from pathlib import Path

from claude_hermes.learning.review import write_guard_factory


def _write(path):
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def _denied(out) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


async def test_guard_allows_both_roots(tmp_path):
    skills, wiki = tmp_path / "skills", tmp_path / "wiki"
    skills.mkdir(); wiki.mkdir()
    guard = write_guard_factory([skills, wiki])
    assert await guard(_write(skills / "s" / "SKILL.md"), None, None) == {}
    assert await guard(_write(wiki / "user" / "profile.md"), None, None) == {}


async def test_guard_denies_outside_every_root(tmp_path):
    skills, wiki = tmp_path / "skills", tmp_path / "wiki"
    skills.mkdir(); wiki.mkdir()
    guard = write_guard_factory([skills, wiki])
    assert _denied(await guard(_write(tmp_path / "escape.md"), None, None))


async def test_guard_still_protects_usage_and_archive(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    guard = write_guard_factory([skills], skills_root=skills)
    assert _denied(await guard(_write(skills / ".usage.json"), None, None))
    assert _denied(await guard(_write(skills / ".archive" / "x" / "SKILL.md"), None, None))


async def test_guard_fails_closed_on_an_unparseable_path(tmp_path):
    guard = write_guard_factory([tmp_path])
    assert _denied(await guard({"tool_name": "Write", "tool_input": {"file_path": "\0"}},
                               None, None))


async def test_guard_records_touched_skill_dirs_only(tmp_path):
    skills, wiki = tmp_path / "skills", tmp_path / "wiki"
    skills.mkdir(); wiki.mkdir()
    touched: set[str] = set()
    guard = write_guard_factory([skills, wiki], skills_root=skills, touched=touched)
    await guard(_write(skills / "my-skill" / "SKILL.md"), None, None)
    await guard(_write(wiki / "topics" / "x.md"), None, None)
    assert touched == {"my-skill"}          # wiki pages are not skills


async def test_guard_denying_everything_when_no_roots(tmp_path):
    """A dry-run lint gets roots=[] — read-only by construction, not by prompt."""
    guard = write_guard_factory([])
    assert _denied(await guard(_write(tmp_path / "anything.md"), None, None))
```

```python
# tests/test_learning_prompts.py — add
from claude_hermes.learning.prompts import build_review_prompt


def test_wiki_section_replaces_the_memory_tool_section():
    out = build_review_prompt({"wiki"})
    assert "memory_save" not in out          # the tool is gone
    assert "wiki/index.md" in out and "wiki/log.md" in out
    assert "SKILLS" not in out               # kinds are independent


def test_skills_section_alone_has_no_wiki():
    out = build_review_prompt({"skills"})
    assert "wiki/" not in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_review.py tests/test_learning_prompts.py -v`
Expected: FAIL — `cannot import name 'write_guard_factory'`

- [ ] **Step 3: Generalize the guard**

In `src/claude_hermes/learning/review.py`, replace `skills_write_guard_factory` with:

```python
def write_guard_factory(
    roots: list[Path], *, skills_root: Path | None = None,
    touched: set[str] | None = None,
):
    """Confine a background fork's writes to an explicit set of roots.

    An empty root list denies everything — that is how a dry-run lint is made
    read-only by construction rather than by asking the model nicely.
    """
    allowed = [r.resolve() for r in roots]
    skills = skills_root.resolve() if skills_root is not None else None

    async def guard(input_data: dict, tool_use_id, context) -> dict:
        raw = (input_data.get("tool_input") or {}).get("file_path", "")
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError):
            return _DENY  # unparseable path → fail closed
        if not any(path.is_relative_to(root) for root in allowed):
            return _DENY
        if any(p in str(path) for p in _PROTECTED):
            return _DENY
        if skills is not None and touched is not None and path.is_relative_to(skills):
            rel = path.relative_to(skills)
            if rel.parts:
                touched.add(rel.parts[0])
        return {}

    return guard


def skills_write_guard_factory(skills_dir: Path, touched: set[str] | None = None):
    """Skills-only guard — kept for the curator's consolidation pass."""
    return write_guard_factory([skills_dir], skills_root=skills_dir, touched=touched)
```

and widen the deny reason:

```python
_DENY = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason":
            "This pass may only write inside the skills directory and the wiki.",
    }
}
```

- [ ] **Step 4: Point the fork at both roots and give it the Stop gate**

In `run_review`, after `skills_dir = ...`:

```python
    wiki_root = home / "agent_home" / "wiki"
    state = WikiTurnState()

    options = base_options(
        home,
        model=cfg.review_model or cfg.model,
        max_turns=cfg.review_max_turns,
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": row["system_append"]},
        tools=REVIEW_BUILTIN_TOOLS,
        allowed_tools=REVIEW_TOOLS,
        hooks={
            "PreToolUse": [HookMatcher(
                matcher="Write|Edit",
                hooks=[write_guard_factory([skills_dir, wiki_root],
                                           skills_root=skills_dir, touched=touched)],
            )],
            "PostToolUse": [HookMatcher(
                matcher="Write|Edit", hooks=[page_touch_factory(state, wiki_root)],
            )],
            "Stop": [HookMatcher(hooks=[stop_gate_factory(state)])],
        },
        resume=row["sdk_session_id"],
        fork_session=True,
        env=NO_ADVISOR_ENV,
    )
```

with `from ..hooks import WikiTurnState, page_touch_factory, stop_gate_factory` at the top. The `mcp_servers` argument is dropped entirely — the fork has no MCP tools now.

- [ ] **Step 5: Rewrite the prompt section**

In `src/claude_hermes/learning/prompts.py`, replace `_MEMORY_SECTION` with:

```python
_WIKI_SECTION = """\
WIKI — this is the ingest pass over the conversation above. Distil what is durable into the
wiki at ./wiki/ (you have Read, Glob, Grep, Write, Edit):
- Find the pages that should hold each fact — `Glob` the namespaces, `Grep` for the entity.
  Prefer updating an existing page over creating a near-duplicate one.
- `user/` who the user is; `projects/` a project and its decisions; `topics/` a concept;
  `env/` machines, paths, conventions. Sources live in `raw/` and are immutable.
- A page is frontmatter (`title`, `summary`, `tags`, `updated`) + prose + `[[links]]`.
  Keep `summary` to one line: it is what the agent sees in the index.
- Declarative facts only ("The user prefers concise replies"), never imperatives.
  If a fact will be stale in a week, it does not belong in the wiki. Procedures belong
  in skills, not here.
- Finish by updating `wiki/index.md` (one line per page) and appending to `wiki/log.md`.
  A hook will stop you if you forget."""
```

and swap the dispatch in `build_review_prompt`:

```python
    if "wiki" in kinds:
        parts.append(_WIKI_SECTION)
```

- [ ] **Step 6: Run the suite**

Run: `python -m pytest tests/test_review.py tests/test_learning_prompts.py tests/test_curator.py -v`
Expected: all green — `test_curator.py` proves the wrapper kept the consolidation pass working.

- [ ] **Step 7: Commit**

```bash
git add src/claude_hermes/learning/review.py src/claude_hermes/learning/prompts.py tests/
git commit -m "feat(learning): the review pass becomes wiki ingest"
```

---

### Task 8: The nudge that fires it

**Files:**
- Modify: `src/claude_hermes/learning/observe.py`, `src/claude_hermes/learning/nudges.py`, `src/claude_hermes/learning/loop.py:43-45`, `src/claude_hermes/config.py`
- Test: `tests/test_learning_usage.py` (observe), `tests/test_nudges.py`

**Interfaces:**
- Consumes: `TurnStats` (existing).
- Produces: `TurnStats.wrote_wiki: bool` (replaces `used_memory`). `NudgeTracker.observe_turn(wrote_wiki: bool, wrote_skill: bool, tool_uses: int) -> set[str]` returning `{"wiki"}` and/or `{"skills"}`. Config: `wiki_nudge_interval: int = 4` (replaces `memory_nudge_interval: int = 10`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_learning_usage.py — add
from claude_hermes.learning.observe import TurnStats, observe_block


class _Block:
    def __init__(self, name, **inp):
        self.name = name
        self.input = inp


def test_a_write_under_wiki_counts_as_a_wiki_write():
    stats = TurnStats()
    observe_block(stats, _Block("Write", file_path="/data/agent_home/wiki/user/profile.md"))
    assert stats.wrote_wiki is True
    assert stats.wrote_skill is False


def test_a_windows_path_under_wiki_counts_too():
    stats = TurnStats()
    observe_block(stats, _Block("Edit", file_path=r"C:\data\agent_home\wiki\topics\x.md"))
    assert stats.wrote_wiki is True


def test_a_skill_write_is_still_a_skill_write():
    stats = TurnStats()
    observe_block(stats, _Block("Write",
                                file_path="/data/agent_home/.claude/skills/foo/SKILL.md"))
    assert stats.wrote_skill is True
    assert stats.wrote_wiki is False
```

In `tests/test_nudges.py`, `make(tmp_path, **kw)` (line 6) passes its kwargs straight into `Config`,
so every `memory_nudge_interval=` becomes `wiki_nudge_interval=` and every `{"memory"}` becomes
`{"wiki"}`. The four existing tests keep their shape; rename them and their kwargs:

```python
# tests/test_nudges.py — the two memory tests, rewritten
async def test_wiki_nudge_fires_and_resets(tmp_path):
    db, nt = await make(tmp_path, wiki_nudge_interval=3, skill_nudge_interval=100)
    assert await nt.observe_turn(False, False, 0) == set()
    assert await nt.observe_turn(False, False, 0) == set()
    assert await nt.observe_turn(False, False, 0) == {"wiki"}
    assert await nt.observe_turn(False, False, 0) == set()   # counter reset on fire
    await db.close()


async def test_an_organic_wiki_write_resets_the_counter(tmp_path):
    db, nt = await make(tmp_path, wiki_nudge_interval=3, skill_nudge_interval=100)
    await nt.observe_turn(False, False, 0)
    await nt.observe_turn(False, False, 0)
    assert await nt.observe_turn(True, False, 0) == set()    # wrote a page → reset
    assert await nt.observe_turn(False, False, 0) == set()   # 1 of 3 again
    await db.close()
```

`test_skill_nudge_counts_tool_iterations` and `test_counters_survive_restart` only need their
`memory_nudge_interval=` kwarg renamed and, in the latter, `{"memory"}` → `{"wiki"}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_nudges.py tests/test_learning_usage.py -v`
Expected: FAIL — `AttributeError: 'TurnStats' object has no attribute 'wrote_wiki'`

- [ ] **Step 3: Rewrite the observation**

In `src/claude_hermes/learning/observe.py`:

```python
_SKILLS_MARKER = ".claude/skills"
_WIKI_MARKER = "/wiki/"


@dataclass
class TurnStats:
    tool_uses: int = 0
    wrote_wiki: bool = False           # was: used_memory (an MCP call)
    wrote_skill: bool = False
    skill_invocations: list[str] = field(default_factory=list)
    skill_patches: list[str] = field(default_factory=list)


def observe_block(stats: TurnStats, block) -> None:
    stats.tool_uses += 1
    name = getattr(block, "name", "")
    args = getattr(block, "input", None) or {}
    if name in ("Write", "Edit"):
        path = str(args.get("file_path", "")).replace("\\", "/")
        if _SKILLS_MARKER in path:
            stats.wrote_skill = True
            skill = _skill_dir_name(path)
            if skill:
                stats.skill_patches.append(skill)
        elif _WIKI_MARKER in path:
            stats.wrote_wiki = True
    elif name == "Skill":
        skill = args.get("skill") or args.get("name")
        if skill:
            stats.skill_invocations.append(str(skill))
```

(The `mcp__hermes__memory_save` branch is deleted — the tool no longer exists.)

- [ ] **Step 4: Rewrite the counter**

In `src/claude_hermes/learning/nudges.py`: rename `_MEMORY_KEY = "turns_since_memory_review"` to `_WIKI_KEY = "turns_since_wiki_review"`, and:

```python
    async def observe_turn(
        self, wrote_wiki: bool, wrote_skill: bool, tool_uses: int
    ) -> set[str]:
        due: set[str] = set()

        turns = 0 if wrote_wiki else await self._get(_WIKI_KEY) + 1
        if turns >= self._cfg.wiki_nudge_interval:
            due.add("wiki")
            turns = 0
        await self._db.set_meta(_WIKI_KEY, str(turns))
        ...                                   # the skills half is unchanged
```

In `src/claude_hermes/learning/loop.py`, `after_turn` passes the renamed field:

```python
        kinds = await self._tracker.observe_turn(
            stats.wrote_wiki, stats.wrote_skill, stats.tool_uses
        )
```

In `src/claude_hermes/config.py`, replace `memory_nudge_interval: int = 10` with:

```python
    wiki_nudge_interval: int = 4      # turns without a wiki write before the ingest pass fires
```

- [ ] **Step 5: Run the suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/learning src/claude_hermes/config.py tests/
git commit -m "feat(learning): the nudge counts wiki writes, not memory_save calls"
```

---

### Task 9: The lint phase

**Files:**
- Modify: `src/claude_hermes/learning/backup.py` (a `kind` parameter), `src/claude_hermes/learning/curator.py` (a second phase), `src/claude_hermes/learning/prompts.py` (the lint prompt), `src/claude_hermes/config.py`, `src/claude_hermes/tools.py` (one line: the snapshot listing)
- Test: `tests/test_backup.py`, `tests/test_curator_pass.py`

**Interfaces:**
- Consumes: `write_guard_factory` (Task 7), `base_options` (existing).
- Produces: `backup.snapshot(source_dir, logs_dir, reason, keep=5, _protect=None, kind="skills")`, `backup.list_snapshots(logs_dir, kind="skills")`, `backup.rollback(target_dir, logs_dir, backup_id=None, keep=5, kind="skills")`. `curator.run_wiki_lint(cfg, home, wiki_root, dry_run=False) -> str | None`. `prompts.build_wiki_lint_prompt(dry_run: bool) -> str`. Config: `wiki_lint_enabled: bool = True`, `wiki_lint_dry_run: bool = False`. `curator_pass` result gains `"wiki_lint": str | None` and `"wiki_snapshot_id": str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backup.py — add
from claude_hermes.learning import backup


def test_wiki_snapshots_live_in_their_own_store(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "user").mkdir(parents=True)
    (wiki / "user" / "profile.md").write_text("hi", encoding="utf-8")
    logs = tmp_path / "logs"

    snap = backup.snapshot(wiki, logs, reason="pre-wiki-lint", kind="wiki")
    assert snap is not None
    assert (logs / "wiki_backups").is_dir()
    assert not (logs / "skills_backups").exists()      # the two stores never mix
    assert len(backup.list_snapshots(logs, kind="wiki")) == 1
    assert backup.list_snapshots(logs, kind="skills") == []


def test_wiki_rollback_restores_the_tree(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "user").mkdir(parents=True)
    page = wiki / "user" / "profile.md"
    page.write_text("original", encoding="utf-8")
    logs = tmp_path / "logs"
    backup.snapshot(wiki, logs, reason="pre-wiki-lint", kind="wiki")

    page.write_text("lint ate it", encoding="utf-8")
    ok, msg = backup.rollback(wiki, logs, kind="wiki")
    assert ok, msg
    assert page.read_text(encoding="utf-8") == "original"
```

`tests/test_curator_pass.py` already has `freeze(monkeypatch)` (line 23) and
`make_home(tmp_path) -> (home, db, skills, sidecar)` (line 35). The gates are opened by passing a
`Config(...)` and pre-seeding the last-run meta key — copy the pattern from
`test_gates_pass_runs_snapshot_ages_and_reports` (line 97).

```python
# tests/test_curator_pass.py — add
from claude_hermes.learning.curator import LAST_RUN_META_KEY
from claude_hermes.wiki import WikiStore


async def _ready_home(tmp_path, monkeypatch, **cfg_kw):
    """A home whose curator gates are all open, with a seeded wiki."""
    freeze(monkeypatch)
    home, db, skills, sidecar = await make_home(tmp_path)
    WikiStore(home).seed()
    long_ago = (NOW - timedelta(days=30)).isoformat()
    await db.set_meta(LAST_RUN_META_KEY, long_ago)      # interval elapsed
    cfg = Config(curator_interval_hours=1.0, curator_min_idle_hours=0.0, **cfg_kw)
    return cfg, home, db, skills, sidecar


async def test_pass_snapshots_the_wiki_and_runs_lint(tmp_path, monkeypatch):
    cfg, home, db, skills, sidecar = await _ready_home(
        tmp_path, monkeypatch, wiki_lint_enabled=True
    )
    calls = {}

    async def fake_lint(cfg_, home_, wiki_root, dry_run=False):
        calls["wiki_root"] = wiki_root
        calls["dry_run"] = dry_run
        return "3 orphans, 1 contradiction"

    monkeypatch.setattr(curator_mod, "run_wiki_lint", fake_lint)

    result = await curator_pass(cfg, home, db, skills, sidecar)
    assert result["ran"] is True
    assert result["wiki_lint"] == "3 orphans, 1 contradiction"
    assert result["wiki_snapshot_id"]                   # snapshotted before the pass
    assert calls["dry_run"] is False
    assert calls["wiki_root"] == home / "agent_home" / "wiki"
    await db.close()


async def test_lint_is_skipped_when_disabled(tmp_path, monkeypatch):
    cfg, home, db, skills, sidecar = await _ready_home(
        tmp_path, monkeypatch, wiki_lint_enabled=False
    )

    async def boom(*a, **k):
        raise AssertionError("lint must not run when wiki_lint_enabled is false")

    monkeypatch.setattr(curator_mod, "run_wiki_lint", boom)
    result = await curator_pass(cfg, home, db, skills, sidecar)
    assert result["ran"] is True
    assert result["wiki_lint"] is None
    await db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_backup.py tests/test_curator_pass.py -v`
Expected: FAIL — `snapshot() got an unexpected keyword argument 'kind'`

- [ ] **Step 3: Parameterize `backup.py`**

Replace the two module constants with a kind table and thread `kind` through:

```python
_KINDS = {
    "skills": ("skills_backups", "skills.tar.gz"),
    "wiki": ("wiki_backups", "wiki.tar.gz"),
}


def _backups_root(logs_dir: Path, kind: str = "skills") -> Path:
    return logs_dir / _KINDS[kind][0]


def _archive_name(kind: str) -> str:
    return _KINDS[kind][1]
```

`snapshot(source_dir, logs_dir, reason, keep=5, _protect=None, kind="skills")` — rename the first parameter from `skills_dir` to `source_dir`, use `_backups_root(logs_dir, kind)` and `_archive_name(kind)`, and rename the manifest counter from `"skill_files"` to `"files"`. `list_snapshots(logs_dir, kind="skills")` and `rollback(target_dir, logs_dir, backup_id=None, keep=5, kind="skills")` take the same parameter; `rollback`'s internal pre-rollback `snapshot(...)` call must pass `kind=kind` through. `_read_manifests` and `_prune` take the already-resolved `backups_root`, so they are unchanged.

In `src/claude_hermes/tools.py`, the `skill_admin` `snapshots` branch reads the counter — make it tolerate manifests written before the rename:

```python
            return reply("\n".join(
                f"{s['id']}: {s['reason']} ({s.get('files', s.get('skill_files', 0))} files)"
                for s in snaps
            ))
```

- [ ] **Step 4: Write the lint prompt**

Append to `src/claude_hermes/learning/prompts.py`:

```python
def build_wiki_lint_prompt(dry_run: bool) -> str:
    action = (
        "Report only. You have NO write access this pass — the guard denies every write. "
        "Describe what you WOULD change."
        if dry_run else
        "Fix what you find, with Write/Edit, directly in ./wiki/."
    )
    return (
        "You are linting the wiki at ./wiki/. Nobody is watching; there is no user to ask.\n\n"
        "Read wiki/index.md, then Glob and Read the pages. Hunt for:\n"
        "- CONTRADICTIONS: two pages asserting incompatible things. The newer `updated:` wins; "
        "say so on the page and delete the dead claim.\n"
        "- STALE CLAIMS: a page distilled from a source (`source: raw/...`) that no longer "
        "matches its source. Re-read the source in raw/ and correct the page — the source is "
        "immutable and always wins.\n"
        "- ORPHANS: pages nothing links to, and pages missing from the index.\n"
        "- BROKEN LINKS: a `[[link]]` whose page does not exist. Either write the page or drop "
        "the link.\n"
        "- AN OVERGROWN INDEX: if index.md has become a long flat list, group it by namespace "
        "and shorten the summaries. The index is injected into the agent's prompt — it has to "
        "stay skimmable.\n"
        "- PAGES CARRIED OVER FROM THE OLD FLAT MEMORY (`tags: [migrated]`): split them into "
        "real pages, one entity or concept each, and drop the migrated tag.\n\n"
        f"{action}\n\n"
        "Finish by appending one line to wiki/log.md, then reply with a short summary: what "
        "you found and what you did. If the wiki is clean, say exactly: Nothing to fix."
    )
```

- [ ] **Step 5: Add the phase to the curator**

In `src/claude_hermes/learning/curator.py`:

```python
WIKI_LINT_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit"]


async def run_wiki_lint(
    cfg: Config, home: Path, wiki_root: Path, dry_run: bool = False
) -> str | None:
    """Health-check the wiki. Fresh context — there is no conversation to fork from.

    A dry run gets an empty root list, so the guard denies every write: read-only
    by construction, not by asking the model nicely.
    """
    if not wiki_root.is_dir():
        return None
    roots = [] if dry_run else [wiki_root]
    options = base_options(
        home,
        model=cfg.curator_model or cfg.review_model or cfg.model,
        max_turns=cfg.curator_max_turns,
        tools=WIKI_LINT_TOOLS,
        allowed_tools=WIKI_LINT_TOOLS,
        hooks={"PreToolUse": [HookMatcher(
            matcher="Write|Edit", hooks=[write_guard_factory(roots)],
        )]},
        env=NO_ADVISOR_ENV,
    )
    final: str | None = None
    try:
        async for msg in query(prompt=build_wiki_lint_prompt(dry_run), options=options):
            if getattr(msg, "result", None):
                final = msg.result
    except Exception:
        log.exception("curator: wiki lint fork failed")
        return None
    return final
```

with `from .prompts import build_curator_prompt, build_wiki_lint_prompt` and
`from .review import skills_write_guard_factory, write_guard_factory` at the top.

In `curator_pass`, after the skills phase and before `result = {...}`:

```python
    wiki_root = home / "agent_home" / "wiki"
    wiki_snapshot_id = None
    wiki_lint: str | None = None
    if cfg.wiki_lint_enabled:
        dry = cfg.curator_dry_run or cfg.wiki_lint_dry_run
        if not dry:
            snap = backup.snapshot(wiki_root, home / "logs", reason="pre-wiki-lint",
                                    keep=cfg.backup_keep, kind="wiki")
            if snap is not None:
                wiki_snapshot_id = snap.name
            else:
                log.warning("curator: pre-lint wiki snapshot failed (non-fatal)")
        wiki_lint = await run_wiki_lint(cfg, home, wiki_root, dry_run=dry)
```

Add both to the `result` dict:

```python
        "wiki_lint": wiki_lint,
        "wiki_snapshot_id": wiki_snapshot_id,
```

and a section to `_write_report`:

```python
        f"## Wiki lint\n{result.get('wiki_lint') or '(not run)'}",
```

Finally, in `src/claude_hermes/config.py`:

```python
    wiki_lint_enabled: bool = True     # the wiki half of the curator pass
    wiki_lint_dry_run: bool = False    # report only; the guard denies every write
```

- [ ] **Step 6: Run the suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(curator): lint the wiki — contradictions, orphans, broken links"
```

---

### Task 10: The ingest skill, the docs, the smoke

**Files:**
- Create: `skills/wiki-ingest/SKILL.md`
- Modify: `config.yaml.example`, `README.md`
- Test: manual Docker smoke (below)

**Interfaces:**
- Consumes: everything. The skill is seeded into `agent_home/.claude/skills/` by the existing `paths.seed_skills` — which copies any bundled directory that is **absent** from the destination, so a new skill *does* reach a live deployment (unlike an edited `CLAUDE.md`).

- [ ] **Step 1: Write the skill**

`skills/wiki-ingest/SKILL.md`:

```markdown
---
name: wiki-ingest
description: File an external source into the wiki as a page.
---

# Wiki ingest

Turn a source the user handed you — a URL, a file, a long pasted note — into a page in your wiki,
without losing the source itself. Follow this whenever a source is worth remembering; skip it for
anything you read once and throw away.

## When to Use

- The user sends a link or a file and the content is durable knowledge, not a passing question.
- You fetched something you will want to reason from again (a spec, a doc, an article).
- Ask first when it is ambiguous: "worth filing in the wiki?" A one-line question is cheaper than
  a wrong page.

## Prerequisites

- `wiki/` exists next to you, with `index.md`, `log.md` and the namespaces `user/ projects/
  topics/ env/ raw/`.
- Tools: `WebFetch` (or `Read` for a file), `Write`, `Edit`, `Glob`, `Grep`.

## Procedure

1. **Fetch.** `WebFetch` the URL, or `Read` the file.
2. **Store the source, verbatim.** Write it to `wiki/raw/<slug>.md`, where `<slug>` is
   lowercase-hyphenated. `raw/` is immutable: if the file already exists, pick a new slug — never
   overwrite one, and never `Edit` one.
3. **Find where it belongs.** `Grep` the wiki for the entities it names. An existing page you can
   deepen beats a new page that duplicates it.
4. **Write the page** under `topics/` (or `projects/`, `env/` if that is what it is about):

   ```markdown
   ---
   title: <what this is>
   summary: <one line — this is what lands in the index>
   tags: [<topic>, ...]
   updated: <YYYY-MM-DD>
   source: raw/<slug>.md
   ---
   <your distillation — the claims that matter, not a retelling>

   See also [[topics/related-page]].
   ```

5. **Link it in.** Add a `[[link]]` to the new page from every page that should point at it. A page
   nothing links to is an orphan, and lint will flag it.
6. **Update `wiki/index.md`** — one line: `- topics/<slug>.md — <summary>`.
7. **Append to `wiki/log.md`** — one line: `- <date> ingest: <source> → topics/<slug>.md`.

## Pitfalls

- **Do not skip step 2.** A page whose source is gone cannot be re-checked, and lint's staleness
  check dies with it.
- **Do not retell the source.** Distil it: what is now true, what changed your mind, what you will
  need. The full text is already in `raw/`.
- **Do not create a near-duplicate page.** Grep first (step 3).
- **Do not stop halfway.** A page without an index line is invisible to your next session — and a
  hook will block the turn until you fix it.

## Verification

`Read wiki/index.md` and confirm your page is listed. `Grep` for `[[<your page>]]` and confirm at
least one other page points at it.
```

- [ ] **Step 2: Update the config example and the README**

In `config.yaml.example`: remove `memory_max_chars`, `user_max_chars`, `memory_nudge_interval`; add the four wiki keys with their defaults and a one-line comment each (`wiki_nudge_interval: 4`, `wiki_index_max_chars: 8000`, `wiki_lint_enabled: true`, `wiki_lint_dry_run: false`).

In `README.md`, replace the description of the memory files with the wiki: where it lives, that the model owns it, that the index is what reaches the prompt, and that `raw/` is immutable.

- [ ] **Step 3: Run the full suite one more time**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 4: Docker smoke (manual, live Telegram)**

```bash
docker compose up -d --build
```

Walk the eight checks:

1. First start on a **fresh** volume: `wiki/` exists with the five namespaces, `index.md`, `log.md`.
2. First start on an **existing** volume that has `MEMORY.md`/`USER.md`: both were migrated into
   `user/profile.md` and `env/notes.md`, and both are listed in `index.md`.
3. Say "remember that I hate emoji in replies" → the agent writes the page **in the turn** and says
   it did.
4. Have a normal conversation for `wiki_nudge_interval` turns → the ingest pass fires in the
   background (check `docker compose logs agent | grep review`) and pages appear.
5. Open a **second** thread → the agent knows the preference from step 3 without being told.
6. Send a link and ask the agent to keep it → `wiki/raw/<slug>.md` appears and a `topics/` page
   links to it.
7. Ask the agent to edit a file under `raw/` → it is denied, and it says so.
8. Set `wiki_lint_dry_run: true`, force a curator pass, and read
   `logs/curator/<ts>/REPORT.md` → the "Wiki lint" section is there and nothing on disk changed.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(wiki): the ingest skill, the config example and the README"
```

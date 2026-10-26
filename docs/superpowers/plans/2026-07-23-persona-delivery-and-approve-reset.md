# Persona Delivery & /approve Lane Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Managed persona updates reach existing volumes automatically, and a successful `/approve` resets the thread's session lane so the agent installs approved plugins instead of refusing.

**Architecture:** Split the persona: `bundle/persona.md` (renamed from `bundle/CLAUDE.md`) is runtime-owned and rewritten into `agent_home/.claude/persona.md` on every boot; a once-seeded `agent_home/CLAUDE.md` stub pulls it via the CLI's `@.claude/persona.md` import (verified working in CLI 2.1.197). A one-time boot migration converts legacy files: known-shipped content (frozen sha256 set, CRLF-normalized) is swapped for the stub; unknown content keeps everything and gains the import line plus a loud warning. Separately, `_handle_approve`'s verified-success branch resets the session lane via the existing `runner.reset`.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode=auto`), no new dependencies.

Spec: `docs/superpowers/specs/2026-07-23-persona-delivery-and-approve-reset-design.md`

## Global Constraints

- All state stays under `CH_HOME` (`/data`); the frozen hash set is a code constant, not state (NFR-5).
- `/approve` stays gateway-intercepted and owner-gated; no model-reachable path may trigger promotion or reset.
- Import detection is line-anchored (`line.strip() == "@.claude/persona.md"`), never substring (advisor R3).
- Hash comparison normalizes `\r\n` → `\n` first — the live volume's file is a CRLF copy from the Windows worktree (sha `5c07828a…` CRLF ≡ `6d74d2d8…` LF).
- persona.md writes are atomic (temp file + `os.replace`, matching `secrets.py:62`).
- Reset fires only on the verified-success branch of `_handle_approve` (after `catalog.get` confirms), never on begin/stale/denied/unverified.

---

### Task 1: paths.py — persona split, boot delivery, legacy migration

**Files:**
- Rename: `bundle/CLAUDE.md` → `bundle/persona.md` (`git mv`, content unchanged in this commit)
- Create: `bundle/CLAUDE.md` (new stub)
- Modify: `src/claude_hermes/paths.py`
- Test: `tests/test_paths_config.py`

**Interfaces:**
- Produces: `paths.write_persona(claude_dir: Path, source: Path | None = None) -> None` (always-overwrite, atomic); `paths.seed_claude_md(agent_home: Path, source: Path | None = None) -> None` (stub seed + migration); constant `paths.PERSONA_IMPORT = "@.claude/persona.md"`. `get_home()` calls `write_persona(claude_dir)` before `seed_claude_md(agent_home)`.

- [ ] **Step 1: `git mv bundle/CLAUDE.md bundle/persona.md`, then write the new stub `bundle/CLAUDE.md`:**

```markdown
@.claude/persona.md

The import above is the managed persona — the runtime rewrites `.claude/persona.md`
at startup, so never edit that file. Everything below this line is yours: standing
notes and rules you add here survive updates.
```

- [ ] **Step 2: Write the failing tests** (replace `test_get_home_seeds_claude_md_with_the_persona` and `test_seed_claude_md_never_overwrites_existing`; adapt the bundle fixture inside `test_ch_assets_overrides_where_the_bundled_assets_live` to write `bundle/persona.md` + a stub `bundle/CLAUDE.md` and assert both land):

```python
def test_get_home_writes_persona_and_seeds_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    persona = home / "agent_home" / ".claude" / "persona.md"
    stub = home / "agent_home" / "CLAUDE.md"
    body = persona.read_text(encoding="utf-8")
    assert "personal agent" in body.lower() and "`refuter` subagent" in body
    assert any(l.strip() == "@.claude/persona.md"
               for l in stub.read_text(encoding="utf-8").splitlines())


def test_stub_import_resolves_to_where_the_persona_is_written(tmp_path, monkeypatch):
    """Advisor R2: the stub's import string and get_home()'s write path are one fact
    in two spellings; tie them through the real artifacts, not a shared constant."""
    monkeypatch.setenv("CH_HOME", str(tmp_path / "home"))
    home = get_home()
    agent_home = home / "agent_home"
    line = next(l.strip() for l in (agent_home / "CLAUDE.md")
                .read_text(encoding="utf-8").splitlines() if l.strip().startswith("@"))
    imported = (agent_home / line[1:]).resolve()
    assert imported == (agent_home / ".claude" / "persona.md").resolve()
    assert imported.is_file()


def test_write_persona_overwrites_on_change_but_leaves_claude_md_alone(tmp_path):
    from claude_hermes.paths import write_persona
    claude_dir = tmp_path / ".claude"; claude_dir.mkdir()
    old, new = tmp_path / "v1.md", tmp_path / "v2.md"
    old.write_text("persona v1", encoding="utf-8")
    new.write_text("persona v2", encoding="utf-8")
    write_persona(claude_dir, source=old)
    write_persona(claude_dir, source=new)
    assert (claude_dir / "persona.md").read_text(encoding="utf-8") == "persona v2"


def test_seed_claude_md_leaves_a_migrated_file_alone(tmp_path):
    from claude_hermes.paths import seed_claude_md
    agent_home = tmp_path / "agent_home"; agent_home.mkdir()
    mine = "@.claude/persona.md\n\nMy own standing notes.\n"
    (agent_home / "CLAUDE.md").write_text(mine, encoding="utf-8")
    seed_claude_md(agent_home, source=tmp_path / "stub.md")
    assert (agent_home / "CLAUDE.md").read_text(encoding="utf-8") == mine


def test_seed_claude_md_swaps_a_known_shipped_persona_for_the_stub(tmp_path):
    """A legacy CLAUDE.md that byte-matches something the runtime once shipped has no
    local additions — replace it with the stub. CRLF copies (hand-synced from a
    Windows worktree, which is exactly what the live volume holds) must match too."""
    from claude_hermes.paths import _BUNDLE_DIR, seed_claude_md
    shipped = (_BUNDLE_DIR / "persona.md").read_bytes()
    stub = tmp_path / "stub.md"; stub.write_text("@.claude/persona.md\n", encoding="utf-8")
    for variant in (shipped, shipped.replace(b"\n", b"\r\n")):
        agent_home = tmp_path / f"h{len(variant)}"; agent_home.mkdir()
        (agent_home / "CLAUDE.md").write_bytes(variant)
        seed_claude_md(agent_home, source=stub)
        assert (agent_home / "CLAUDE.md").read_text(encoding="utf-8") == "@.claude/persona.md\n"


def test_seed_claude_md_preserves_unknown_content_and_prepends_import(tmp_path, caplog):
    import logging
    from claude_hermes.paths import seed_claude_md
    agent_home = tmp_path / "agent_home"; agent_home.mkdir()
    (agent_home / "CLAUDE.md").write_text("Rules I wrote myself.\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        seed_claude_md(agent_home, source=tmp_path / "stub.md")
    body = (agent_home / "CLAUDE.md").read_text(encoding="utf-8")
    assert body.splitlines()[0].strip() == "@.claude/persona.md"
    assert "Rules I wrote myself." in body
    assert any("reconcile" in r.message for r in caplog.records)


def test_a_prose_mention_of_the_import_path_does_not_count_as_migrated(tmp_path):
    """Advisor R3: substring matching would mistake a mention for the directive."""
    from claude_hermes.paths import seed_claude_md
    agent_home = tmp_path / "agent_home"; agent_home.mkdir()
    (agent_home / "CLAUDE.md").write_text(
        "See @.claude/persona.md for details.\n", encoding="utf-8")
    seed_claude_md(agent_home, source=tmp_path / "stub.md")
    body = (agent_home / "CLAUDE.md").read_text(encoding="utf-8")
    assert body.splitlines()[0].strip() == "@.claude/persona.md"
```

- [ ] **Step 3: Run to verify they fail** — `python -m pytest tests/test_paths_config.py -x -q`. Expected: FAIL (`write_persona` not defined; stub not seeded).

- [ ] **Step 4: Implement in `paths.py`:**

```python
PERSONA_IMPORT = "@.claude/persona.md"

# sha256 (with \r\n normalized to \n) of every bundle/CLAUDE.md ever committed.
# A legacy agent_home/CLAUDE.md matching one of these is a pure runtime seed with
# no local additions and is safe to swap for the import stub. Migration artifact:
# frozen forever, never grows — new persona versions ship via persona.md overwrite.
_SHIPPED_PERSONA_SHA256 = frozenset({
    "6d74d2d826f540836658b66efc6ce3810f6eed03c00c2aaa94646ed2bde55591",  # 2204c96
    "74ad3deb86c81ce002c2555ffd3364916d409b738615e7b540c81872b8d8b100",  # a7d8d2a
    "1df70941edde7dd2b48cc437b17349787019924f9384edd25ed332a0f56159c6",  # 93a5fa2
    "b95789fe3dc2d72c7602adac53f6d4b6c69ec9939a02b2dc1f6a3b977b6b2b12",  # 0d18166
    "46d669d9d86e195dcfe9a333bc1ab99ab4baee52833d381393cc75facb689ccf",  # 18a08c4
    "281975f5ecd38faa8d933b367fdf0e4df109fb3eb13a9634f04964a8698ca133",  # c871081
})


def _normalized_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _has_persona_import(text: str) -> bool:
    return any(line.strip() == PERSONA_IMPORT for line in text.splitlines())


def write_persona(claude_dir: Path, source: Path | None = None) -> None:
    """The managed persona — runtime-owned, rewritten every boot. CLAUDE.md pulls it
    in via the CLI's @-import, so a persona fix reaches every volume on the next
    deploy without touching the agent's own file."""
    src = (_BUNDLE_DIR / "persona.md") if source is None else source
    if not src.exists():
        log.warning("cannot write persona: bundled source %s does not exist "
                    "(is CH_ASSETS set and does the image ship it?)", src)
        return
    data = src.read_bytes()
    dest = claude_dir / "persona.md"
    if dest.exists() and dest.read_bytes() == data:
        return
    tmp = dest.with_suffix(".md.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


def seed_claude_md(agent_home: Path, source: Path | None = None) -> None:
    """Seed the CLAUDE.md stub once, and migrate legacy files to the import scheme.

    CLAUDE.md is the agent's own file; the managed persona reaches it through the
    @.claude/persona.md import. A legacy file that byte-matches a version the
    runtime once shipped (CRLF-normalized — hand-synced copies from a Windows
    worktree are CRLF) carries no local additions and is swapped for the stub.
    Anything else is kept whole and only gains the import line, loudly."""
    src = (_BUNDLE_DIR / "CLAUDE.md") if source is None else source
    dest = agent_home / "CLAUDE.md"
    if not dest.exists():
        _copy_missing(dest, src)
        return
    raw = dest.read_bytes()
    if _has_persona_import(raw.decode("utf-8", errors="replace")):
        return
    if _normalized_sha256(raw) in _SHIPPED_PERSONA_SHA256:
        if src.exists():
            dest.write_bytes(src.read_bytes())
        return
    dest.write_bytes(PERSONA_IMPORT.encode() + b"\n\n" + raw)
    log.warning(
        "CLAUDE.md at %s had local content and no persona import; the import line "
        "was prepended. Stale rule text may now duplicate the managed persona — "
        "reconcile by hand.", dest)
```

In `get_home()`, before `seed_claude_md(agent_home)`, add `write_persona(claude_dir)`. Add `import hashlib` and `import os` at the top.

- [ ] **Step 5: Run the paths suite, then the full suite** — `python -m pytest tests/test_paths_config.py -q` then `python -m pytest -q`. Expected: PASS (adapt any test elsewhere that assumed `bundle/CLAUDE.md` holds the persona).

- [ ] **Step 6: Commit** — `feat(persona): managed persona.md delivered every boot; CLAUDE.md becomes an importing stub`

---

### Task 2: /approve confirm resets the thread's session lane

**Files:**
- Modify: `src/claude_hermes/gateway/telegram.py`
- Test: `tests/test_approve_command.py`

**Interfaces:**
- Consumes: `runner.reset(session_key)` (exists, used by `/new` at telegram.py:99-101); `build_session_key` (imported already).
- Produces: `_handle_approve(rest, event, *, acl, home, writer, runner, session_key)` — two new required kwargs.

- [ ] **Step 1: Extend `SpyRunner` in the test file to record resets, and write the failing tests:**

```python
class SpyRunner:
    def __init__(self):
        self.turns = []
        self.resets = []

    async def run_turn(self, key, text, progress=None, user_id=0):
        self.turns.append(text)
        return "reply"

    async def reset(self, key):
        self.resets.append(key)


async def test_a_confirmed_approve_resets_the_thread_lane(tmp_path, monkeypatch):
    """The model never sees the gateway-intercepted /approve; its lane still holds
    'the draft is queued and I cannot install it'. A fresh lane re-reads the persona
    and the catalog — this is what turns 'Say add X' from a promise into behavior."""
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    db, acl = await setup(tmp_path)
    drafts.save(tmp_path, STDIO)
    runner, writer = SpyRunner(), SpyWriter(tmp_path)
    shown = await handle_event(runner, Config(), event("/approve someserver"),
                               acl=acl, home=tmp_path, writer=writer)
    assert runner.resets == []                       # step one only shows the surface
    token = shown.split("/approve someserver")[1].split()[0]
    reply = await handle_event(runner, Config(), event(f"/approve someserver {token}"),
                               acl=acl, home=tmp_path, writer=writer)
    assert runner.resets == ["tg:private:1"]
    assert "starts fresh" in reply
    await db.close()


async def test_a_failed_confirm_does_not_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    db, acl = await setup(tmp_path)
    drafts.save(tmp_path, STDIO)
    runner, writer = SpyRunner(), SpyWriter(tmp_path)
    await handle_event(runner, Config(), event("/approve someserver"),
                       acl=acl, home=tmp_path, writer=writer)
    await handle_event(runner, Config(), event("/approve someserver 00000000"),
                       acl=acl, home=tmp_path, writer=writer)
    assert runner.resets == []
    await db.close()


async def test_an_unverified_write_does_not_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    db, acl = await setup(tmp_path)
    drafts.save(tmp_path, STDIO)
    runner, writer = SpyRunner(), SpyWriter()        # writes nowhere → P5 error path
    shown = await handle_event(runner, Config(), event("/approve someserver"),
                               acl=acl, home=tmp_path, writer=writer)
    token = shown.split("/approve someserver")[1].split()[0]
    await handle_event(runner, Config(), event(f"/approve someserver {token}"),
                       acl=acl, home=tmp_path, writer=writer)
    assert runner.resets == []
    await db.close()
```

- [ ] **Step 2: Run to verify the new tests fail** — `python -m pytest tests/test_approve_command.py -q`. Expected: 3 new FAIL (unexpected-kwarg or missing reset), existing tests PASS.

- [ ] **Step 3: Implement.** In `handle_event`, the `/approve` branch becomes:

```python
    if command == "/approve":
        # Handled here, before the runner, for the same reason as /secret: approving a
        # catalog entry makes something runnable in this container, and a prompt-injected
        # turn must not be able to enact its own draft. Only a literal owner message does.
        key = build_session_key(event.chat_type, event.chat_id,
                                user_id=event.user_id, thread_id=event.thread_id)
        answer = await _handle_approve(rest, event, acl=acl, home=home, writer=writer,
                                       runner=runner, session_key=key)
        return None if verdict == "ignore" else answer
```

`_handle_approve` gains `runner` and `session_key` kwargs; its verified-success tail becomes:

```python
    approvals.clear(home, m.kind, name)
    # The model never saw this exchange — the lane it would resume still believes the
    # draft is queued and installing is not its step. A fresh lane re-reads the persona
    # and the catalog. Approvals are rare; one cold prefix is the whole cost.
    await runner.reset(session_key)
    return (f"'{m.label}' is now in your catalog. Say 'add {name}' and I'll install it "
            "(you may need to send its secret first). This thread starts fresh on "
            "your next message.")
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_approve_command.py -q` then `python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `fix(gateway): a confirmed /approve resets the thread lane the command was typed in`

---

### Task 3: teach the post-approval step in persona and tool hints

**Files:**
- Modify: `bundle/persona.md` (Extensions section), `src/claude_hermes/tools.py` (draft reply, drafts listing)
- Test: `tests/test_ext_manage.py` (adjust/extend hint assertions)

**Interfaces:** none new — wording only.

- [ ] **Step 1: Write the failing test** (in `tests/test_ext_manage.py`, following its existing call pattern for the `draft` and `drafts` actions):

```python
async def test_draft_and_drafts_hints_name_add_as_the_post_approval_step(...):
    # call ext_manage action='draft' with a valid manifest, then action='drafts';
    # both replies must tell the agent that an approved entry appears in
    # action='catalog' and is installed by the agent with action='add'.
    assert "action='add'" in draft_reply
    assert "action='add'" in drafts_reply
```

- [ ] **Step 2: Run to verify it fails**, then edit `tools.py`:
  - Draft reply: after the `Once they approve, it is live immediately…` sentence, replace with: `"Once they approve, the entry appears in action='catalog' immediately — no restart, no rebuild — and this thread's context resets. Installing it is then YOUR step: action='add'.\n"`
  - Drafts listing line: `f"{m.name} [{m.kind}] {m.label} — waiting for the owner to approve it in chat: /approve {m.name}; once approved, install it with action='add'"`

- [ ] **Step 3: Edit `bundle/persona.md` Extensions section.** Replace the sentence `It takes effect immediately — no restart, no rebuild, so never tell them to restart anything.` with:

```
Approving puts the entry in the catalog immediately — no rebuild, nothing for the
user to restart — and resets this thread's context, so a conversation that drafted
something may not remember doing so. Installing from the catalog is still your
step: when the user then asks for it, run `action="add"` yourself.
```

- [ ] **Step 4: Run the full suite** — `python -m pytest -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `fix(persona,tools): name action='add' as the agent's post-approval step`

---

### Task 4: wiki + docs sync

**Files:**
- Modify: `docs/llm-wiki/components/extensions.md` (ext_manage section: `/approve` now resets the lane of the thread it was typed in; post-approval install step), `docs/llm-wiki/flows/extension-install.md` (draft→approve steps 2 and 4; the "No restart either way" line), `docs/llm-wiki/components/core-runtime.md` if it describes seeding.
- Commit: spec + plan + wiki.

- [ ] **Step 1: Update both wiki pages** to match the shipped behavior (persona split + boot migration in the core-runtime/extensions pages; lane reset in the flow page).
- [ ] **Step 2: Re-fingerprint and validate** — `.llm-wiki/llm-wiki fingerprint --page <page>` for each touched page, then `.llm-wiki/llm-wiki validate`, then `.llm-wiki/llm-wiki receipt write --kind synced`.
- [ ] **Step 3: Commit** — `docs: spec, plan and wiki for persona delivery + /approve lane reset`

---

### Task 5: deploy and live verification (no commit)

- [ ] **Step 1:** `python -m pytest -q` green; `docker compose build` then `docker compose up -d`.
- [ ] **Step 2:** In the container verify: `/data/agent_home/.claude/persona.md` byte-equals `/app/bundle/persona.md`; `/data/agent_home/CLAUDE.md` is the stub (import line first); boot logs carry no persona warnings.
- [ ] **Step 3:** End-to-end install proof without touching `/data`: run `clone_plugin` against the approved plugin-dev manifest into a throwaway `/tmp` home inside the container; expect the pinned-sha clone with `subdir: plugins/plugin-dev` to verify `.claude-plugin/plugin.json`.
- [ ] **Step 4:** Check live `config.yaml` for `idle_reset_hours`/`daily_reset_hour`; if the 16.07 drafting lane can still resume, tell the owner to send `/new` in that thread (or note that the daily reset already retired it).

# hermes-agent skills system vs. claude-hermes — analysis and integration proposal

Date: 2026-07-13
Reference: `NousResearch/hermes-agent` @ `e4ea0a0` (verified against repo source, not the README)
Subject: the "auto skill creator" and "curator" capabilities, and how to add their value to claude-hermes on SDK-native skills.

---

## 0. Headline correction

The task framed these as two *skills* in hermes-agent. They are not. Verified against the repo tree:

- **There is no skill named "auto skill creator."** What plays that role is a Python subsystem: `agent/background_review.py` — a self-improvement fork that fires on nudge counters and writes skills via the `skill_manage` tool. Its user-invoked sibling is `agent/learn_prompt.py` (`/learn`).
- **The curator is not a skill either.** It is `agent/curator.py` (2016 lines) + `agent/curator_backup.py` + the `hermes_cli/curator.py` CLI. The only thing named "curator" under `skills/` is `optional-skills/mlops/nemo-curator`, which is NVIDIA NeMo Curator — an unrelated data-curation tool.
- The one genuinely *skill*-shaped artifact in this area is `skills/software-development/hermes-agent-skill-authoring/SKILL.md` — the house style guide for writing skills.

This matters for the proposal: **neither capability can be adopted as "just a skill."** Both are scheduled forks with restricted toolsets and write guards. In our architecture they belong in `learning/`, with skills used for the parts that are genuinely *knowledge* rather than *machinery*.

The second headline: **we have already ported most of this.** `learning/nudges.py` even carries the comment "Hermes-style self-nudges." The gap is not the loop — it's the *safety and consolidation half*: backup/rollback, reachable pinning, umbrella-building, and run reports.

---

## 1. hermes-agent skills architecture

### 1.1 Where skills live

Single runtime root, seeded from the repo at install (`tools/skills_tool.py:139-144`):

```python
# All skills live in ~/.hermes/skills/ (seeded from bundled skills/ on install).
# This is the single source of truth -- agent edits, hub installs, and bundled
# skills all coexist here without polluting the git repo.
HERMES_HOME = get_hermes_home()
SKILLS_DIR = HERMES_HOME / "skills"
```

Four provenance classes coexist in that one tree, distinguished by sidecar files rather than by location:

| Class | Marker | Curator may touch? |
|---|---|---|
| bundled (ships with repo, `skills/<category>/<name>/`) | `.bundled_manifest` (`tools/skills_sync.py:41`) | archive only, and only if `prune_builtins: true` |
| hub-installed (agentskills.io) | `.hub/lock.json` (`tools/skills_hub.py:3390`) | never |
| agent-created | `.usage.json` → `created_by: "agent"` | yes — full lifecycle |
| user/foreground-created | `.usage.json` → `created_by: null` | never |

Plus `skills.external_dirs` from config — discoverable and foreground-editable, but read-only to autonomous maintenance (`agent/skill_utils.py:579`, `is_external_skill_path`).

### 1.2 File format

`SKILL.md` with YAML frontmatter. Validator is `tools/skill_manager_tool.py::_validate_frontmatter:524`. Enforced limits (`tools/skills_tool.py:161-163`, `tools/skill_manager_tool.py:471-478`):

```python
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}
```

Frontmatter fields actually consumed beyond `name`/`description`: `platforms`, `environments`, `tags`, `related_skills`, and a `metadata.hermes.*` block carrying `config`, `requires_toolsets`, `fallback_for_tools`, etc.

### 1.3 Discovery and prompt injection

Scanned by `agent/skill_utils.iter_skill_index_files:785` (os.walk, pruning `EXCLUDED_SKILL_DIRS`), cached two ways: a 30s in-process TTL cache in the tool layer (`_SKILLS_CACHE_TTL_SECONDS = 30.0`) and a disk snapshot at `<HERMES_HOME>/.skills_prompt_snapshot.json` keyed on an mtime+size manifest of every `SKILL.md`.

**The system prompt gets only name + a 60-char description.** The truncation is in `agent/skill_utils.py:771-779` — and note it is *unrelated* to `MAX_DESCRIPTION_LENGTH = 1024`, which is a file-layer cap:

```python
def extract_skill_description(frontmatter):
    desc = str(raw_desc).strip().strip("'\"")
    if len(desc) > 60:
        return desc[:57] + "..."
    return desc
```

Emitted by `agent/prompt_builder.build_skills_system_prompt:1445` into an `<available_skills>` block under a "## Skills (mandatory)" header telling the model to `skill_view(name)` anything even partially relevant. Bodies are **never** in the system prompt — they load lazily. The design intent is stated plainly in `agent/skill_commands.reload_skills`:

> "This does NOT invalidate the skills system-prompt cache. Skills are called by name via `/skill-name`, `skills_list`, or `skill_view` — they don't need to be in the system prompt for the model to use them. Keeping the prompt cache intact preserves prefix caching across the reload."

### 1.4 Invocation and mutation

- `skill_view(name, file_path=None)` (`tools/skills_tool.py:961`) — loads a body or a support file; bumps `view_count`.
- `/skill-name` slash commands (`agent/skill_commands.py`) — injects the full body as a user message; bumps `use_count`.
- `skill_manage(action=...)` (`tools/skill_manager_tool.py:1320`) — actions `create | patch | edit | delete | write_file | remove_file`. This is the single write choke point, and it is where all telemetry and provenance hang.

### 1.5 Provenance — the load-bearing invariant

A `ContextVar` decides who is writing (`tools/skill_provenance.py`):

```python
_write_origin = contextvars.ContextVar("skill_write_origin", default="foreground")
BACKGROUND_REVIEW = "background_review"
def is_background_review() -> bool: ...
```

And in `skill_manage` (`tools/skill_manager_tool.py:1389-1416`):

```python
if action == "create":
    if is_background_review():
        mark_agent_created(name)          # → created_by: "agent"
elif action in {"patch", "edit", "write_file", "remove_file"}:
    bump_patch(name)
elif action == "delete":
    if not result.get("_archived"):
        forget(name)
```

**Only the background fork's creations become curator-eligible.** A skill the user asked the foreground agent to write stays user-owned forever. Every other guard — delete-requires-`absorbed_into`, read-before-write, external-dirs-read-only — keys off this same "who wrote this" question.

---

## 2. The "auto skill creator": `agent/background_review.py`

### Trigger

Two independent counters on the `AIAgent` instance, both defaulting to 10:

| Counter | Unit | Init | Threshold config |
|---|---|---|---|
| `_turns_since_memory` | user turns | `agent/agent_init.py:1361` | `memory.nudge_interval` |
| `_iters_since_skill` | **tool iterations** | `agent/agent_init.py:1362` | `skills.creation_nudge_interval` |

Memory fires at turn start (`agent/turn_context.py:306-314`); skills fire at turn end (`agent/turn_finalizer.py:484-490`). Both are checked in `finalize_turn` and spawn one fork (`agent/turn_finalizer.py:500-510`):

```python
# Background memory/skill review — runs AFTER the response is delivered
# so it never competes with the user's task for model attention.
if final_response and not interrupted and (_should_review_memory or _should_review_skills):
    agent._spawn_background_review(
        messages_snapshot=list(messages),
        review_memory=_should_review_memory,
        review_skills=_should_review_skills,
    )
```

Not signal-based — two clocks.

### Mechanism

`spawn_background_review_thread` → `_run_review_in_thread` (`agent/background_review.py:617-940`) builds a **second `AIAgent`** and replays the transcript (`conversation_history=messages_snapshot`), appending the review prompt as a synthetic user message. Key parameters:

- `max_iterations=16`, `quiet_mode=True`, `skip_memory=True`
- Runs on the **main model by default** so it reuses the parent's warm prefix cache — `_cached_system_prompt` and `session_id` are pinned to make `tools[]` + system prompt byte-identical (cited as ~26% cost reduction). Routing to a cheap aux model is opt-in via `auxiliary.background_review.*`, and when routed it switches to a digest of the last 24 messages instead of a full replay. The policy comment: *"Same model -> full replay; different model -> digest. That's the whole policy."*
- Toolset restricted at dispatch by a thread-scoped whitelist: `["skills"]` plus `["memory"]` if enabled. Everything else denied.
- Re-entrancy prevented by zeroing the fork's own counters (`_memory_nudge_interval = 0`, `_skill_nudge_interval = 0`) and hard-isolating persistence (`_persist_disabled = True`, `_session_db = None`) — the comment calls the latter "the curator-takeover root cause": without it the fork writes its harness prompt into the user's real session and the agent *becomes* the curator on the next live turn.

### The prompt (`_SKILL_REVIEW_PROMPT`, `agent/background_review.py:181-284`)

The substance worth stealing:

- **Bias to act:** *"Be ACTIVE — most sessions produce at least one skill update, even if small. A pass that does nothing is a missed learning opportunity, not a neutral outcome."*
- **Target shape:** *"CLASS-LEVEL skills, each with a rich SKILL.md and a `references/` directory... Not a long flat list of narrow one-session-one-skill entries."*
- **Signals:** user corrections to style/tone/verbosity are first-class — *"Frustration signals like 'stop doing X', 'this is too verbose'... are FIRST-CLASS skill signals"*; a loaded skill that turned out wrong → *"Patch it NOW."*
- **Strict preference order:** (1) patch the skill that was actually in play this session, (2) patch an existing umbrella, (3) add a `references/`/`scripts/` file to an umbrella, (4) only then create a new class-level umbrella — *"If the proposed name only makes sense for today's task, it's wrong."*
- **NEVER-capture rules**, framed as self-defence: environment-dependent failures, and especially *"Negative claims about tools or features ('X tool is broken')... These harden into refusals the agent cites against itself for months after the actual problem was fixed."*
- Overlap detection is explicitly delegated: *"the background curator handles consolidation at scale."*

### Storage and lifecycle

Writes land in `~/.hermes/skills/<name>/SKILL.md` via `skill_manage(action="create")`. Because the fork runs under write origin `background_review`, `mark_agent_created()` stamps `created_by: "agent"` — which is precisely what hands the skill to the curator. New skills are visible to the next session (the prompt cache is busted with `clear_skills_system_prompt_cache(clear_snapshot=True)`).

### The manual path: `/learn` (`agent/learn_prompt.py:99-150`)

No fork, no aux model — `build_learn_prompt(user_request)` builds one prompt that runs as a normal foreground turn. It embeds `_AUTHORING_STANDARDS` (the "HARDLINE" rules) and is notably careful about a failure mode we should copy:

> "prose that comes after a path or link is NOT incidental — it is the user telling you what they want from that source. A request like `<url> focus on the auth flow, skip the deprecated endpoints` means: gather the URL AND honor 'focus on auth, skip deprecated' as authoring requirements. Never fetch the first source and ignore the rest."

Because it is foreground, `is_background_review()` is false → the skill is **not** marked agent-created → the curator will never auto-prune it.

---

## 3. The curator: `agent/curator.py`

### Trigger

Inactivity check, not a cron daemon. `maybe_run_curator(idle_for_seconds=...)` (`agent/curator.py:1998`) is called from CLI session start and from the gateway's cron-ticker thread. Gates in `should_run_now:233`:

1. `curator.enabled` (default true), not `paused`
2. `now - last_run_at >= interval_hours` (default **168h / 7 days**)
3. idle for `min_idle_hours` (default 2) — enforced at the call site, where it's known whether an agent is running

State in `~/.hermes/skills/.curator_state`: `{last_run_at, last_run_duration_seconds, last_run_summary, last_report_path, paused, run_count}`.

**First-run deferral** (`:264-276`) — the first observation *seeds* `last_run_at = now` and returns False, buying the user a full interval to pin things before the curator ever touches the library.

### Phase 1 — deterministic transitions (always on, no LLM)

`apply_automatic_transitions:305-383`. Cutoffs: stale at 30d, archive at 90d. Skips, in order:

- `if row.get("pinned"): continue`
- `if name in cron_referenced: continue` — skills referenced by cron jobs are treated like pinned, because a paused job's skill would otherwise age out from under it
- never-used grace floor (`:363-369`): a `use_count == 0` skill younger than the stale cutoff is skipped and *reactivated* if stale — *"A use=0 skill is absence of evidence, not evidence of staleness."*

The ladder, with a real reactivation branch:

```python
if anchor <= archive_cutoff and current != STATE_ARCHIVED:   archive_skill(name)
elif anchor <= stale_cutoff and current == STATE_ACTIVE:     set_state(name, STATE_STALE)
elif anchor > stale_cutoff and current == STATE_STALE:       set_state(name, STATE_ACTIVE)
```

### Phase 2 — LLM consolidation (**off by default**)

`DEFAULT_CONSOLIDATE = False` (`:78`). When off, `_llm_pass` returns before spawning anything — deterministic prune only, zero aux-model cost. The comment is explicit that this is a cost/blast-radius decision.

When on, `_run_llm_review:1825` forks an `AIAgent` with `max_iterations=9999` (the docs still say 8 — stale; the code comments target "50-100 API calls against hundreds of candidate skills"), `platform="curator"`, `skip_memory=True`, own counters zeroed. It is deliberately given write origin `background_review` so that `skill_manage`'s stricter guards fire against it.

`CURATOR_REVIEW_PROMPT` (`:417-568`) is an **umbrella-building** brief, not an audit:

> "This is an **UMBRELLA-BUILDING consolidation pass, not a passive audit and not a duplicate-finder.** ... A collection of hundreds of narrow skills where each one captures one session's specific bug is a FAILURE of the library — not a feature."

Hard rules: never delete (archive is the maximum destructive action); skip pinned; skip protected built-ins (currently just `plan`, because it backs `/plan`); don't use usage counters as an excuse — *"'use=0' is ALSO not a reason to PRUNE."* And an aggression clause: *"If you end the pass with fewer than 10 archives, you stopped too early."*

The machine-readable contract is the key design idea:

> "`skill_manage action=delete` — archive a skill. **MUST pass `absorbed_into=<umbrella>`** when you've merged its content into another skill, or `absorbed_into=""` when you're truly pruning with no forwarding target."

Plus a required YAML block (`consolidations:` / `prunings:`) in the final message. Three signals — the declared `absorbed_into`, the YAML block, and a tool-call audit that greps the deleted skill's name out of other skills' writes — are reconciled by `_reconcile_classification:872` on a first-match-wins precedence, so every archived skill lands in exactly one bucket even when the model lies or forgets.

### Backups

`agent/curator_backup.py`. Before **every** mutating pass: `snapshot_skills(reason="pre-curator-run")` → `~/.hermes/skills/.curator_backups/<utc-id>/{skills.tar.gz, cron-jobs.json, manifest.json}`, keeping 5. `rollback()` takes a *pre-rollback* snapshot first (with `protect_ids` so it can't evict the very snapshot it's about to extract), stages the current tree aside, extracts with path-traversal rejection + `filter="data"`, and restores the staged tree on any failure.

### Reports and CLI

Every run writes `~/.hermes/logs/curator/<ts>/{run.json, REPORT.md}` — transitions, tool-call counts, consolidations with reasons, the LLM's final summary, and a `## Recovery` section. The user-facing summary includes a rename map (`pdf-extraction → document-tools`). `hermes curator status|run|backup|rollback|pause|pin|unpin|restore|list-archived|prune` — plus `/curator` as a slash command in-session.

---

## 4. Comparison with claude-hermes

### 4.1 Skills mechanism

| | hermes-agent | claude-hermes |
|---|---|---|
| Storage | `~/.hermes/skills/<cat>/<name>/SKILL.md` | `data/agent_home/.claude/skills/<name>/SKILL.md` |
| Discovery | custom walker + 2-layer cache (`skill_utils`, `prompt_builder`) | **SDK**, via `setting_sources=["project"]` + `cwd=agent_home` (`runner.py:112-113`) |
| Prompt index | hand-built `<available_skills>` block, 60-char descriptions | **SDK** builds and injects it |
| Load a body | `skill_view` tool / `/slash` command | **SDK** `Skill` tool |
| Write a skill | `skill_manage` tool (6 actions) | plain `Write`/`Edit` into the skills dir |
| Write guard | inside the tool (`_background_review_write_guard`) | **PreToolUse hook** — `learning/review.py:33`, `skills_write_guard_factory` |

**We do not have and do not need** `skill_view` / `skills_list` / `skill_manage` / the loader / the prompt-index builder / the caches / bundles. The SDK gives us discovery, injection, invocation, and cache invalidation for free. That is ~4000 lines of hermes-agent we correctly skipped.

The important structural translation: **hermes enforces at the tool layer; we enforce at the hook layer.** `skills_write_guard_factory` (`learning/review.py:33-51`) denies any `Write`/`Edit` outside the skills root, fails closed on unparseable paths, protects `.usage.json` and `.archive`, and records which skill dirs the pass actually touched. That is a strictly *broader* choke point than hermes's — it catches every write tool, not just the one blessed API.

### 4.2 The self-improvement loop (auto skill creator)

| Capability | hermes-agent | claude-hermes | Verdict |
|---|---|---|---|
| Nudge counters | `_turns_since_memory` / `_iters_since_skill` on the agent instance | `learning/nudges.py` — same two counters, **persisted in SQLite** via `db.get_meta`/`set_meta` | **we're better** — survives restart; hermes rehydrates approximately (`prior_user_turns % interval`) |
| Fires after reply | `turn_finalizer.py:500` | `runner.py:95-99` → `LearningLoop.after_turn` | parity |
| Conversation fork | hand-rolled second `AIAgent` + `messages_snapshot` replay | **SDK** `resume=<id>, fork_session=True` (`learning/review.py:83-84`) | **we're better** — ~10 lines vs ~300 |
| Warm-cache reuse | pins `_cached_system_prompt`, `session_id` | `review_model: ""` → same model, resumes same session (`config.py:22`) | parity, ours simpler |
| Restricted toolset | thread-scoped whitelist | `tools=REVIEW_BUILTIN_TOOLS` (SDK restricts what *exists*) (`learning/review.py:19-20`) | parity |
| Re-entrancy | zeroes the fork's counters | `LearningLoop.review_running` single-flight guard (`learning/loop.py:32-48`) | parity, ours more explicit |
| Review prompt substance | `_SKILL_REVIEW_PROMPT` | `learning/prompts.py:16-31` — **we already ported** the signals, strict preference order, and the NEVER-capture rules verbatim in spirit | parity |
| Provenance | `ContextVar` + `mark_agent_created` in `skill_manage` | guard-recorded `touched` set → `sidecar.mark_created(name, "agent")` (`learning/review.py:93-99`) | parity — and ours correctly refuses to claim dirs that appeared concurrently from a foreground `/learn` |
| `/learn` | `build_learn_prompt` (`agent/learn_prompt.py`) | `learning/prompts.py:64` + `gateway/telegram.py:24` | parity; ours is thinner |
| Combined memory+skill pass | `_COMBINED_REVIEW_PROMPT` | `build_review_prompt(kinds)` composes sections (`learning/prompts.py:50`) | parity, ours cleaner |

**Assessment: the auto skill creator is essentially at parity, and architecturally cleaner on our side.** No significant gap. The only upgrade worth making is enriching `build_learn_prompt` with hermes's "sources vs. requirements" framing.

### 4.3 The curator

This is where the gap is real.

| Capability | hermes-agent | claude-hermes | Gap |
|---|---|---|---|
| Aging active→stale→archived | `apply_automatic_transitions:305` | `learning/curator.py:31` `apply_aging` | **have it** |
| Archive-only, never delete | prompt rule #2 + no delete path | `shutil.move` to `.archive/` (`curator.py:46`) | **have it** |
| Pinned skills exempt | `if row.get("pinned"): continue` | `curator.py:37` | **have it — but see below** |
| Only agent-created | `created_by == "agent"` | `curator.py:37` | **have it** |
| **Pinning reachable by a human** | `hermes curator pin <skill>` | ❌ `UsageSidecar.set_pinned` (`usage.py:69`) **has no production caller** — only `tests/test_curator.py:23`. The exemption exists but nothing can ever set it. | **GAP — and it's a live bug** |
| **Backup / rollback** | `curator_backup.py`, snapshot before every mutating pass | ❌ none | **GAP** |
| **LLM consolidation / umbrella-building** | `CURATOR_REVIEW_PROMPT` + fork | ❌ prune-only | **GAP — the main one** |
| **Run reports** | `run.json` + `REPORT.md` + rename map | ❌ `log.info` only (`curator.py:59`) | **GAP** |
| **Idle gating** | `min_idle_hours` (default 2) | ❌ fixed 24h sleep (`curator.py:52-62`) — can fire mid-conversation | **GAP** |
| **First-run deferral** | seeds `last_run_at`, defers one interval | ❌ runs on first tick | **GAP** |
| Reactivation stale→active | explicit branch (`:379`) | ❌ **a stale skill that gets used again stays `stale` forever** — neither branch of `apply_aging` fires when idle is small | **GAP — minor bug** |
| Never-used grace floor | `use=0` + recent → skip/reactivate | ❌ anchors on `created_at`, so a never-used skill is aged purely on age | **GAP — minor** |
| Telemetry richness | `use_count`, `view_count`, `patch_count`, `last_*_at`, `archived_at` | `use_count`, `last_used`, `state`, `pinned` (`usage.py:11-19`) | **partial** |
| Cron-referenced skill protection | `_cron_referenced_skills:290` | n/a — our cron jobs carry a prompt, not skill refs | **N/A by design** |
| Prune bundled built-ins | `prune_builtins: true` | ❌ agent-created only | **deliberate non-goal** (see §5) |
| Hub / external-dir provenance | `.hub/lock.json`, `skills.external_dirs` | n/a — no hub | **N/A** |

### 4.4 What we do that hermes-agent doesn't

- **SDK-native skills.** No loader, no prompt-index builder, no 30s TTL cache, no disk snapshot, no cache-invalidation bugs. This is the single biggest architectural win and the reason the direction decision was right.
- **Hook-layer write enforcement** rather than tool-layer — a broader choke point (`learning/review.py:33`).
- **Durable nudge counters** in SQLite rather than instance state.
- **SDK `fork_session`** instead of a hand-rolled transcript replay.
- **Prompt-injection scanning on cron job creation** (`cron/scan.py`, wired at `tools.py:50-52`) — hermes has cron but no equivalent scan on the prompt at creation time.
- **`cost_report` as an agent-facing MCP tool** (`tools.py:94`) — hermes's `agent/insights.py` is a human CLI dashboard; ours is something the agent can consult about its own spend.
- **Single-flight review guard** (`loop.py:32-34`) — explicit, rather than relying on counter arithmetic.

---

## 5. Integration proposal

Principle: **the LLM proposes, the code disposes.** hermes lets the consolidation fork perform its own `mv` into `.archive/` and then *reconstructs* what happened from three unreliable signals (declared `absorbed_into`, a YAML block, and a tool-call grep audit reconciled across 130 lines in `_reconcile_classification`). We don't have to. Our `skills_write_guard_factory` already denies writes to `.archive/`, so our fork *cannot* archive anything — it can only rewrite skill content and then *report* what it merged. Our code then does the archiving. That collapses hermes's three-signal reconciliation into one parse, and makes an unparseable response fail safe (nothing is archived) instead of fail open.

Ordered changes. Steps 1–4 are the safety floor and should land before step 5 turns on anything that mutates at scale.

---

### Step 1 — Make pinning reachable *(bug fix; do this first)*

**Target:** `tools.py`, `learning/usage.py`

`set_pinned` is dead code today, so the pinned-exemption in `curator.py:37` can never be exercised in production. Expose skill administration as an MCP tool on the existing `hermes` server — for a chat-only agent, "pin that skill" in conversation is the right surface, not a CLI we don't have.

Add `skill_admin` to `build_hermes_server` with actions `list | pin | unpin | archive | restore`, backed by `UsageSidecar`. `list` renders name/state/pinned/use_count. `restore` moves a dir back out of `.archive/` and resets state to `active`.

Note the ACL consideration: this tool mutates the skill library, so it should be registered only on the foreground turn server (where `ctx` is present), not on the context-free server the review fork builds (`review.py:66`).

---

### Step 2 — Snapshot before every destructive pass

**Target:** new `learning/backup.py`; called from `learning/curator.py`

Straight adoption of `curator_backup.py`, minus the cron-link reconciliation (our cron jobs don't reference skills):

- `snapshot(skills_dir, reason) -> Path | None` → `data/logs/skills_backups/<utc-id>/{skills.tar.gz, manifest.json}`, `arcname` relative to the skills root.
- Manifest: `{id, reason, created_at, archive_bytes, skill_files}`.
- `list_snapshots()`, `rollback(id=None)` — with hermes's two hard-won details: take a **pre-rollback snapshot with the target protected from pruning**, and **stage the current tree aside** so a failed extract restores rather than destroys.
- Retention `backup_keep: int = 5` in `Config`.
- Extraction hardening: reject absolute paths and `..` members; `filter="data"`.

Call `snapshot(reason="pre-curator-run")` at the top of any curator pass that will mutate. Failure to snapshot is logged, not fatal — hermes's reasoning applies: *"the alternative is that a transient disk issue silently disables curator forever, which is worse."*

Expose rollback via the `skill_admin` tool from step 1 (`action="rollback"`).

---

### Step 3 — Fix and enrich the aging pass

**Target:** `learning/curator.py`, `learning/usage.py`, `learning/observe.py`

Three defects and one enrichment:

1. **Reactivation.** Add the missing branch: `stale` + recently used → back to `active`. Today a revived skill is stuck at `stale` forever.
2. **Never-used grace floor.** Skip (and reactivate) a `use_count == 0` skill whose `created_at` is inside the stale window. Adopt hermes's reasoning verbatim: absence of evidence is not evidence of staleness.
3. **Idle gating + interval state.** Replace the fixed `asyncio.sleep(24h)` loop with a gate: run only if `now - last_run_at >= curator_interval_hours` **and** the newest row in `messages` is older than `curator_min_idle_hours`. Persist `last_run_at` / `paused` / `run_count` via the existing `db.get_meta`/`set_meta`. Add first-run deferral (seed `last_run_at`, don't run). New config: `curator_interval_hours: float = 168`, `curator_min_idle_hours: float = 2`.
4. **Telemetry.** Extend the sidecar entry with `patch_count`, `last_patched`, `archived_at`, and a derived `last_activity = max(last_used, last_patched, created_at)` used as the aging anchor. To feed `patch_count`, extend `observe.py:16` — it already detects `wrote_skill` from the path but discards the name; extract the skill dir name from the path and record it in `TurnStats.skill_patches`, then have `LearningLoop.after_turn` call `sidecar.bump_patch(name)` alongside the existing `bump_use`.

---

### Step 4 — Run reports

**Target:** `learning/curator.py`

Write `data/logs/curator/<ts>/{run.json, REPORT.md}` per pass: counts (checked / stale / archived / reactivated), per-skill transitions, and — once step 5 lands — the consolidation map with reasons and the fork's final summary. Persist `last_report_path` in meta. Surface a human summary through `skill_admin(action="status")`: last run, counts, pinned list, five least-recently-used skills.

This is cheap and it is what makes step 5 auditable enough to enable. Reporting must be individually try/except'd — a formatting bug must never break a curation pass.

---

### Step 5 — The consolidation pass *(the actual new capability)*

**Target:** `learning/curator.py` (new `run_consolidation`), `learning/prompts.py` (new `build_curator_prompt`), `config.py`

This cannot be a skill: it needs a scheduled fork with a restricted toolset and a write guard. It is supporting code, and it mirrors `review.run_review()` closely enough to share most of its shape.

**Fork.** A `query()` with:
- `model = cfg.curator_model or cfg.review_model or cfg.model`
- `tools = ["Read", "Glob", "Grep", "Write", "Edit"]` — **no `memory_save`**, and no MCP server at all; this pass touches skills only
- `hooks = {"PreToolUse": [skills_write_guard_factory(skills_dir, touched)]}` — reuse it as-is; it already denies `.archive/` and `.usage.json`
- **no `resume`** — unlike the review fork, there is no conversation to fork from. This is a fresh context over the library, so there is no warm cache to preserve and `curator_model` can freely point at a cheaper model.
- `cwd = agent_home`, `permission_mode="bypassPermissions"`, `max_turns = cfg.curator_max_turns` (start at 30)

**Prompt** (`build_curator_prompt(candidates)`): adopt hermes's framing — umbrella-building, not audit; class-level library as the goal; explicit "N narrow one-session skills is a failure of the library." Include the candidate table (name, state, pinned, use_count, patch_count, last_activity) and the hard rules: don't touch pinned, don't touch non-agent-created, **you cannot archive anything yourself — you rewrite content and report.**

**Contract.** The fork's final message must end with:

```yaml
consolidations:
  - from: <old-skill>
    into: <umbrella-skill>
    reason: <one sentence>
prunings:
  - name: <skill>
    reason: <one sentence>
```

`run_consolidation` parses that block, and **applies the archiving itself** — validating that every `into:` target actually exists on disk before archiving the corresponding `from:`. A missing umbrella means the merge didn't happen, so the source is left alone. An unparseable or absent block archives nothing. No `_reconcile_classification` equivalent is needed.

**Config.** `curator_consolidate: bool = False` — opt-in, matching hermes's `DEFAULT_CONSOLIDATE = False` for the same reason: it costs tokens on every run and makes broad structural changes. Also `curator_dry_run` for a report-only pass. Guard the whole pass on `LearningLoop.review_running` being false so it never races the review fork.

---

### Step 6 — Ship the authoring standards as a real skill

**Target:** new `data/agent_home/.claude/skills/skill-authoring/SKILL.md`; `learning/prompts.py`

hermes maintains the standards in *both* places, and that's correct: `_AUTHORING_STANDARDS` inlined in the prompts (so the fork cannot fail to see them) **and** `skills/software-development/hermes-agent-skill-authoring/SKILL.md` for depth.

Do the same. Keep `AUTHORING_STANDARDS` (`prompts.py:3-14`) inline as the hard floor — a background fork that has to *choose* to load a skill is a fork that will sometimes not. Move the *deep* material into an SDK-native skill the agent (and the review fork) can load on demand: the validation checklist, peer structure, the writing-quality principles ("optimize for process predictability", "prune duplication and no-ops", "watch for premature completion"), and the common-failure taxonomy (premature completion / duplication / sediment / sprawl / no-op prose).

This is the one capability in the whole analysis that is genuinely *knowledge* rather than *machinery* — so it is the one that belongs in a skill.

---

### Step 7 — Enrich `/learn`

**Target:** `learning/prompts.py:64`

Adopt hermes's sources-vs-requirements framing (`agent/learn_prompt.py:118-146`). Our `build_learn_prompt` today says "gather what you need, then author one skill." Hermes's explicitly warns that prose *after* a path or URL is load-bearing requirements, not incidental — and that the agent must never fetch the first source and ignore the rest. That's a real failure mode with a one-paragraph fix.

---

## 6. Explicitly not adopting

- **The custom skill loader, `skill_view`/`skills_list`/`skill_manage`, the prompt-index builder, and the two-layer cache.** The SDK does all of it. This was the direction decision and the source review confirms it was right — it is the bulk of hermes-agent's skills code.
- **Skill bundles** (`agent/skill_bundles.py`). An alias that eagerly loads N full skill bodies into one message. No SDK equivalent, and a Telegram agent has no use for it.
- **`prune_builtins`.** hermes archives unused *bundled* skills by default. We have almost no bundled skills and the ones we do have are project-level (`.claude/skills/parallel-plan-execution`). Keep the curator's jurisdiction to `created_by == "agent"` — it is the safer invariant and it is already what `curator.py:37` enforces.
- **The hub, `skills.external_dirs`, `platforms`/`environments` frontmatter gating.** No use case.
- **`_reconcile_classification`.** Superseded by the LLM-proposes/code-disposes inversion in step 5 — we don't need to reverse-engineer what the fork did, because we won't let it do it.
- **A `/curator` CLI.** We have no CLI; the surface is `skill_admin` over Telegram.

---

## 7. Summary

The self-improvement loop — the "auto skill creator" — is already at parity and is architecturally cleaner on our side, mostly because `fork_session` and SDK-native skills let us delete code hermes had to write. Nothing urgent there beyond a richer `/learn` prompt.

The curator is where the substance is. We have the skeleton (aging, archive-only, agent-created-only, pin exemption) but three things are missing and one is broken:

- **broken:** pinning has no caller, so the exemption is unreachable (step 1)
- **missing:** no backup before a destructive pass (step 2)
- **missing:** no consolidation — the library can only shrink by age, never improve by merging (step 5)
- **missing:** no reports, so nothing about the curator is auditable (step 4)

Steps 1–4 are small and independently valuable. Step 5 is the real feature and should not ship before them.

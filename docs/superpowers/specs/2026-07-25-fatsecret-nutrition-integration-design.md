# FatSecret nutrition capability: extension, skills, scripts

Date: 2026-07-25
Status: draft — not yet run past implementation-advisor

Source material: `D:/projects/fatsecret_mcp` (git remote
`https://github.com/hbs-1991/fitmeai.git`, head `8be7105`) — a FastMCP stdio server
for the FatSecret Platform API plus `nutrition_agent/`, an aiogram bot that predates
claude-hermes and duplicates it.

## Problem

The nutrition capability exists and works, but in a shape claude-hermes cannot absorb
as-is. Three separate defects:

**1. The tool surface is too wide for the narrow waist (P2).** The server registers 20
tools — `foods_tools.py` 8, `diary_tools.py` 5, `exercise_tools.py` 5, `weight_tools.py`
2 — and their docstrings total **18,522 characters (~4.6k tokens)**, before per-parameter
JSON schema overhead. `fatsecret_food_create` alone declares 25 parameters;
`fatsecret_diary_add_entry`'s docstring spends ~50 lines
(`diary_tools.py:171-218`) explaining that gram-based servings take
`number_of_units = grams`, not servings. The runtime today carries 11 builtins plus 7
hermes tools; installing this unchanged roughly triples the tool count and adds ~5-7k
tokens to every cached prefix — paid on every turn of every session, including cron
passes that will never log a meal. Under P1 the prefix is cached, so this is cheap per
turn, but it is never free and it dilutes tool selection permanently.

Critically, the expensive part of that docstring is *procedural knowledge* — when to pick
which serving, how to confirm before writing. That belongs in a skill, loaded when the
task arrives, not in a schema paid for on every unrelated turn.

**2. The package is not installable anywhere but its dev host.** Four blockers:

- `pyproject.toml:35` — `build-backend = "setuptools.build_backend"` is a typo for
  `setuptools.build_meta`. No build backend by that name exists.
- No `[project.scripts]` entry point, so there is nothing for `uvx` to execute.
- `main.py:15` imports `from src.fatsecret_mcp.server import create_server` — resolvable
  only from the repo root.
- `dependencies` (`pyproject.toml:10-22`) mixes the server with the retired bot:
  `aiogram`, `openai`, `claude-agent-sdk`, `keyring`, `pyzbar`, `Pillow`. `pyzbar`
  installs cleanly and then fails at import without `libzbar0`, which
  `python:3.12-slim` does not ship.

**3. `nutrition_agent/` is a second, weaker claude-hermes.** `agent.py` wires
`query()` with `mcp_servers` + `setting_sources=["project"]` and a memory-loading
`UserPromptSubmit` hook; `bot.py`, `services/session_manager.py`, `services/whisper.py`
and `handlers/` reimplement the gateway. All of it is superseded — hermes has sessions
as Telegram topics, voice, the learning loop, cron and cross-session recall. Keeping it
means maintaining two agents against one API.

## Design

Three layers, split by what each is actually good at. The dividing rule:

> **Anything that needs FatSecret credentials runs inside the MCP server subprocess.
> Skills carry procedure. Scripts carry local computation and never see a credential.**

That rule is what keeps the script layer from becoming a second secrets surface. Note
that `bash_guard`'s `.env` rule (`src/claude_hermes/guard.py:11`,
`(^|[\s/])\.env\b`) does **not** match `/data/secrets.env` — a Bash-invoked script
*could* read every extension secret. That is pre-existing and out of scope here, but it
is the reason this design gives scripts no reason to go looking.

### 1. Repackage `fatsecret-mcp` as a server-only, uvx-runnable distribution

Stays in the `fitmeai` repo — no split needed. Changes to `pyproject.toml`:

- Fix the build backend to `setuptools.build_meta`.
- `[tool.setuptools.packages.find] where = ["src"]` so src-layout discovery is explicit
  and `nutrition_agent/` + `tests/` cannot confuse it.
- Trim `dependencies` to the server's real needs: `fastmcp`, `requests`,
  `requests-oauthlib`, `pydantic`, `python-dotenv`. Move `keyring` to an optional
  `[project.optional-dependencies] local = [...]` (it is a dev-host convenience; the
  container path is env vars, `auth/oauth_manager.py:47-51`) and everything aiogram/
  openai/pyzbar-shaped to `bot = [...]` or delete with `nutrition_agent/`.
- Add entry points:

  ```toml
  [project.scripts]
  fatsecret-mcp = "fatsecret_mcp.__main__:main"
  fatsecret-chart = "fatsecret_mcp.cli.chart:main"
  ```

- Add `src/fatsecret_mcp/__main__.py` carrying `main.py`'s logic with absolute imports.

Then `uvx --from git+https://github.com/hbs-1991/fitmeai@<sha> fatsecret-mcp` works, and
`uv` is already in the hermes image (`Dockerfile:14`). Tag a release commit and pin its
**40-hex sha** in the manifest — `args` are not ref-validated for stdio entries
(`catalog.py:263-267` only checks list-of-strings), so the pin is a discipline we impose,
matching the plugin rules in [[contract.catalog-manifest]].

### 2. Narrow 20 tools to 4 action-dispatch tools

Same shape as hermes' own `ext_manage` / `cron_manage` / `skill_admin`:

| Tool | Actions |
|---|---|
| `fatsecret_food` | `search`, `get`, `barcode`, `create`, `recipe_search`, `recipe_get` |
| `fatsecret_diary` | `get`, `month`, `add`, `edit`, `delete`, `report` |
| `fatsecret_exercise` | `search`, `get`, `month`, `add`, `edit` |
| `fatsecret_weight` | `update`, `month` |

Rules for the rewrite:

- `action` is a required enum; all other parameters optional and validated per action,
  returning a plain error string on mismatch (the existing tools already return
  `{"error": ...}` rather than raising — keep that, `diary_tools.py:85-90`).
- `fatsecret_food_search_v3` and `fatsecret_food_autocomplete` are **dropped**, not
  folded in: v3 duplicates `search`, and an agent does not need autocomplete. If
  region/language turn out to matter, they become optional params on `search`.
- `food_create`'s 25 flat parameters collapse into one optional `nutrition` object;
  only `calories/fat/carbohydrate/protein` are required inside it.
- **Docstring budget: ≤1.2k tokens across all four tools combined** (from ~4.6k). Each
  action gets one line saying what it does and what it returns. The gram-serving lore
  moves verbatim into the `nutrition-log` skill (§4) — it is procedure, not schema.
- New: `action="report"` on `fatsecret_diary` takes `start`/`end` dates and returns
  *computed* daily and period totals — calories, macros, macro ratios, deviation from a
  target passed in by the caller. This is where the biggest token win lives: a week of
  raw entries is ~30 verbose JSON rows the model must sum by hand; `report` returns one
  compact table. It runs server-side because it needs credentials.

### 3. Catalog manifest

`catalog/mcp/fatsecret.yaml` in the hermes repo — the reviewed, `:ro`-mounted side
([[decision.readonly-catalog-boundary]]), which is correct for a first-party extension:

```yaml
manifest_version: 1
name: fatsecret
label: FatSecret
description: >
  Nutrition tracking against the FatSecret Platform API — food search, food diary,
  exercise log and weight history, as four action-dispatch tools.
version: 0.2.0
transport: stdio
command: uvx
args: ["--from", "git+https://github.com/hbs-1991/fitmeai@<40-hex-sha>", "fatsecret-mcp"]
scopes: [turn, cron]
env:
  FATSECRET_CLIENT_ID: "${FATSECRET_CLIENT_ID}"
  FATSECRET_CLIENT_SECRET: "${FATSECRET_CLIENT_SECRET}"
  FATSECRET_ACCESS_TOKEN: "${FATSECRET_ACCESS_TOKEN}"
  FATSECRET_ACCESS_SECRET: "${FATSECRET_ACCESS_SECRET}"
auth:
  - name: FATSECRET_CLIENT_ID
    prompt: FatSecret application Client ID (platform.fatsecret.com/api)
  - name: FATSECRET_CLIENT_SECRET
    prompt: FatSecret application Client Secret
  - name: FATSECRET_ACCESS_TOKEN
    prompt: OAuth 1.0 access token from setup_oauth.py
  - name: FATSECRET_ACCESS_SECRET
    prompt: OAuth 1.0 access token secret from setup_oauth.py
```

Every `${VAR}` is declared in `auth`, satisfying `_check_declared_vars`
(`catalog.py:199-208`); `command: uvx` is on the stdio allowlist
(`catalog.py:258`); `server_spec` refuses to spawn if any secret is absent
(`compose.py:61-63`).

`scopes: [turn, cron]` is deliberate — the cron scope is what makes §6's automatic
weekly report possible, and it is the capability the old bot never had.

**Install path** (unchanged machinery, [[flow.extension-install]]): owner sends the four
`/secret NAME=value` lines → says "add fatsecret" → `ext_manage(action="add")` probes the
server with `get_mcp_status()` (no model call, no cost, `probe.py:23`) → registry row →
`_flag_reset()` resets the lane because tool schemas are cached prefix
([[decision.prompt-cache-discipline]]).

**OAuth stays a one-time host operation.** `setup_oauth.py` needs a browser and a
`localhost:8080` callback (`oauth_manager.py:224`), neither of which exists on the VPS.
Run it locally, then carry the two resulting values in via `/secret`. OAuth 1.0 access
tokens do not expire (`oauth_manager.py:73-82`), so this happens once until the user
revokes access — there is no refresh path to build.

### 4. Skills — four umbrellas, not eight

The eight skills in `fatsecret_mcp/.claude/skills/` are ~20-line Russian sketches naming
the old thin tool names. They are sprawl by this project's own taxonomy
(`skills/skill-authoring/SKILL.md:99`) and every command in them is now wrong. Rewrite,
consolidated, to the house standard — description one sentence ≤60 chars ending in a
period; body order `# Title` / intro / `## When to Use` / `## Prerequisites` /
`## How to Run` / `## Quick Reference` / `## Procedure` / `## Pitfalls` /
`## Verification`:

| Skill | Absorbs | Carries |
|---|---|---|
| `nutrition-log` | quick-log | **The gram-serving rule** lifted from `diary_tools.py:171-218`; the confirm-before-write protocol; the КБЖУ table format |
| `nutrition-analysis` | nutrition-analysis, weekly-report, weight-dynamics | `action="report"` usage, the >20% deviation warning, chart handoff (§5) |
| `meal-planning` | daily-menu, meal-planner | Target-driven planning, recipe lookup |
| `training` | workout-analysis, workout-planner | Exercise log and plan |

These ship in the hermes repo's `skills/` and seed into `agent_home/.claude/skills/`.
**Seeding is copy-if-absent and never updates** (`paths.py:27-43`) — the same seed-once
trap as the persona. Accepted here, as the persona spec accepted it: skills are the
agent's procedural memory and the learning loop rewrites them. But it means the initial
seed must be right, because a later fix will not reach a live volume without a hand edit.

### 5. Scripts — local computation only, zero credentials

Scripts ship in the **same pinned distribution** as the server and are invoked over Bash
via `uvx --from git+…@<sha> fatsecret-chart`. Not in the hermes image (avoids coupling
the repos) and emphatically **not inside a seeded skill directory** — a `scripts/` dir
under `.claude/skills/<name>/` is written once by `copytree` and never updated again.

The one script this design needs:

- **`fatsecret-chart`** — reads JSON on stdin (a weight series or a daily-calorie
  series, both already small: `action="month"` returns ~30 numbers), writes a PNG,
  prints its path. The model then delivers it with the hermes `send_document` tool. No
  API call, no credential, no network.

Aggregation deliberately did **not** become a script. It needs credentials to fetch the
raw diary, and a credentialed Bash script is a strictly worse boundary than a
credentialed subprocess the model can only reach through a typed schema. It is
`fatsecret_diary(action="report")` instead — same token reduction, credentials stay
contained.

### 6. Retiring `nutrition_agent/` and migrating its state

- `about_me.md` (user parameters, goals, preferences) → hermes `USER.md`.
- `memory/favorite_foods.md`, `eating_patterns.md`, `corrections.md`,
  `weekly_insights.md` → seed content into hermes `MEMORY.md`. From then on the learning
  loop maintains them; the hand-written `Write` to `memory/*.md` in the old
  `CLAUDE.md:12-18` is replaced by `memory_save` plus the review pass.
- `bot.py`, `handlers/`, `services/session_manager.py`, `services/whisper.py`,
  `agent.py`, `run_bot.py`, `sessions.json` → delete. Voice, sessions and photo intake
  are hermes' gateway.
- **Cron job**: a weekly `fatsecret_diary(action="report")` + chart, delivered into its
  own topic. This is new capability, not a migration.

**Barcode is the one real gap.** `services/barcode.py` uses `pyzbar`, which needs
`libzbar0` in the image. Decision: **do not add the native dependency yet.** The photo
already reaches the model as an image, and EAN-13 digits are printed under the bars —
read them with vision and pass them to `fatsecret_food(action="barcode")`, which is a
tool that already exists. Revisit only if that fails in practice on curved or blurry
labels, where decoding the bars beats reading the digits; the fallback is `apt-get
install libzbar0` in the hermes image plus a `fatsecret-barcode` entry point alongside
`fatsecret-chart`.

## Out of scope

- The `bash_guard` `.env` rule not covering `secrets.env`. Real, pre-existing, and
  unrelated to this integration — worth its own decision, not a rider on this one.
- Reworking skill seeding so bundled skill updates reach live volumes. Same known trap
  the persona spec explicitly deferred; this spec inherits that decision rather than
  reopening it.
- Multi-user FatSecret accounts. One owner, one token pair, per the existing ACL.
- Publishing `fatsecret-mcp` to PyPI. The pinned git sha is enough and keeps the review
  surface in one place.

## Testing

- **`fitmeai` side**: `pip install .` from a clean checkout succeeds (proves the build
  backend fix); `uvx --from git+…@<sha> fatsecret-mcp` starts and answers an MCP
  handshake in a container with no FatSecret env vars beyond the four; each of the four
  tools rejects an unknown `action` with an error string, not an exception; `report`
  totals match a hand-summed fixture week.
- **Token budget is a test, not an aspiration**: assert the four tools' combined
  docstring length stays under the budget set in §2. It is the only guard against
  docstring creep undoing the whole design.
- **hermes side**: `python scripts/validate_manifest.py catalog/mcp/fatsecret.yaml`
  passes; a manifest missing one `auth` entry fails on `_check_declared_vars`;
  `server_spec` with three of four secrets raises `ComposeError`; `ext_manage(action="add")`
  with no secrets returns the four `/secret` lines and installs nothing.
- **End to end in the container**: probe reports `connected`; log one food and read it
  back; the lane resets after `add`.
- Each new skill passes the `skills/skill-authoring/SKILL.md` checklist — in particular
  the 60-character description limit, which every one of the eight originals violates.

## Deployment

1. Land the `fitmeai` packaging changes; tag; note the 40-hex sha.
2. Add `catalog/mcp/fatsecret.yaml` with that sha, plus the four skills, to hermes.
3. Rebuild the image, `docker compose up -d` (the catalog is `:ro`-mounted and re-read
   every turn, but the skills seed at `get_home()`).
4. On the host: run `setup_oauth.py` once, capture the token pair.
5. In Telegram: four `/secret` lines, then "add fatsecret". Verify the lane-reset notice,
   then log one meal end to end before trusting it with a day.

## Open question for review

Whether `recipe_search`/`recipe_get` earn their place under `fatsecret_food`. They are
read-only lookups that `meal-planning` might use, but the old skills never called them —
if a week of use leaves them untouched, drop both actions.

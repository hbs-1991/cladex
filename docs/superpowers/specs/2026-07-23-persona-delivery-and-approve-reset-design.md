# Persona delivery to live volumes, and the /approve → install handoff

Date: 2026-07-23
Status: approved by implementation-advisor (APPROVE-WITH-CHANGES, all changes folded in below)

Advisor gate R1 — verified empirically 2026-07-23: in the live image (CLI 2.1.197), a
fixture `agent_home` whose stub CLAUDE.md consists of the line `@.claude/persona.md`
delivered a sentinel string from `.claude/persona.md` into the model's context
(one-turn haiku probe answered with the sentinel). The import mechanism is real.

## Problem

Two defects combined to produce the 16.07 plugin-dev incident (owner approved a
drafted plugin, then said "Add plugin-dev", and the agent refused: "Adding/approving
it isn't something I can trigger"):

1. **Persona delivery.** `agent_home/CLAUDE.md` is seeded once by `_copy_missing`
   and never updated again. Every persona fix since (b1cf265's flow change,
   a7d8d2a's rewrite) reached live deployments only by hand-editing the volume —
   done twice already, forgotten once. There is no mechanism; the defect is
   recorded in project memory and in a7d8d2a's commit message.
2. **Gateway-intercepted commands are invisible to the model.** `/approve` is
   handled before the runner (by design — a prompt-injected turn must not enact
   its own draft) and does not reset the session lane. The thread that drafted
   the entry therefore keeps two stale beliefs: the persona snapshot its session
   started with, and its own last statement that the draft is "queued, and you
   cannot make it installable". The gateway then tells the owner "Say 'add X'
   and I'll install it" — a promise the model has no way to know about.

At incident time the on-disk persona was already current (hand-fixed at 19:06,
incident at 20:17): the session snapshot and the tool-hint wording, not the file,
carried the stale rules. Both defects need fixing; either alone leaves the trap.

## Design

### 1. Split the persona: managed file, delivered every boot

- `bundle/CLAUDE.md` is renamed to `bundle/persona.md` — content unchanged in the
  rename commit. It is now **runtime-owned**: `get_home()` writes it to
  `agent_home/.claude/persona.md` on every boot (write-if-different, atomic
  replace via temp file + `os.replace`).
- A new, small `bundle/CLAUDE.md` stub is seeded once (still `_copy_missing`):

  ```
  @.claude/persona.md

  The import above is the managed persona — the runtime rewrites
  `.claude/persona.md` at startup, so never edit that file. Everything below
  this line is yours: standing notes and rules you add survive updates.
  ```

- The CLI (2.1.197 in the image) resolves `@relative/path` imports against the
  importing file's directory, so `agent_home/CLAUDE.md` pulls
  `agent_home/.claude/persona.md`. Every fork (turn, cron, review, curator)
  already loads CLAUDE.md via `cwd=agent_home`, so nothing else changes.

### 2. One-time reconciliation of legacy volumes

On boot, when `agent_home/CLAUDE.md` exists but contains no
`@.claude/persona.md` line:

- If its sha256 is in the **known-shipped set** — the hashes of every version of
  `bundle/CLAUDE.md` ever committed (enumerated from git history at
  implementation time, frozen as a constant) — the file is a pure runtime seed
  with no local additions: replace it with the stub. The live deployment is this
  case (its file is byte-identical to the current bundle, verified 2026-07-23).
- Otherwise the file may contain agent- or user-authored content we must not
  destroy: prepend the import line, keep everything else, and log a loud warning
  that stale rule text may now duplicate the managed persona and should be
  reconciled by hand.

The known-shipped set is a migration artifact, not a versioning scheme: future
persona releases need no new hashes, because from now on the managed content
lives in a file the runtime plainly overwrites.

If the agent later deletes the import line from its CLAUDE.md, the same boot
check restores it (prepend + warn). The import is load-bearing; losing it
silently would recreate the original defect.

### 3. `/approve` confirm resets the thread's session lane

On a successful confirm (snapshot promoted, `catalog.get` verified): reset the
session lane of the chat/thread the command was typed in, exactly as `/new`
does, and say so in the reply:

> "'{label}' is now in your catalog. Say 'add {name}' and I'll install it (you
> may need to send its secret first). This thread starts fresh on your next
> message."

The next turn then starts a new SDK session: current persona from disk, current
catalog, no stale "I cannot" claims in context. `begin`, stale-token, denied and
unverified paths do not reset. The reset mirrors the promotion's authorization
(owner check inside the handler) and runs even when the group-mention verdict
suppresses the reply, because the promotion itself already ran.

This amends the earlier decision that `/approve` never restarts anything (made
for prompt-cache discipline): approvals are rare, one lane reset costs one cold
prefix, and the incident showed the stale lane is the real hazard. (Checked by
the advisor: this is not a Locked Decision in either extensions spec — the
command table simply omitted a reset.)

Two documented limits (advisor R4, R5): the reset targets the lane `/approve`
was typed in — if the owner drafts in one thread and confirms in another, the
drafting thread keeps its stale beliefs; the flow is complete only when draft
and approve share a lane, which is the normal Telegram usage. And in a group
where the bot is not addressed, the promotion (and now the reset) still runs
while the acknowledgement is suppressed by the mention gate — the reset can
fire without its message being seen. Both are accepted, not accidental.

Import detection on boot is line-anchored (a stripped line equal to the import
directive), never a substring match — a prose mention of the path must not
count as "already migrated" (advisor R3). Prepending is idempotent under this
rule. If the CLI also honors mid-line imports, the worst case is a harmless
duplicate inclusion.

### 4. Teach the post-approval step everywhere it is worded

- Persona (`bundle/persona.md`), Extensions section: replace the ambiguous "It
  takes effect immediately" with wording that approval **puts the entry in the
  catalog** and installing it is still the agent's own step
  (`action="add"`).
- `ext_manage` draft-action reply and `drafts` listing in `tools.py`: append the
  same fact — once approved, the name appears in `action="catalog"` and the
  agent installs it with `action="add"`.

## Out of scope

- Delivery of seeded **skills** and **subagents**: `.claude/skills/` is the
  agent's procedural memory by design — the live, self-authored
  `ext-manage-drafting` skill is already better than anything the repo ships,
  and overwriting it would destroy learning. Revisit only if a bundled skill
  ever needs a mandatory fix.
- `settings.json` boot-overwrite: same mechanism would apply, but it has never
  drifted in practice; no incident, no change.
- Injecting the approval fact into a live session instead of resetting: no such
  mechanism exists in the SDK; the reset achieves the goal at negligible cost.

## Testing

- `paths`: fresh home seeds stub + persona.md; changed bundle persona overwrites
  persona.md only; CLAUDE.md with import line is never touched; legacy file with
  known hash is swapped to the stub; legacy file with unknown content gets the
  import prepended, content kept, warning logged.
- Advisor R2 — one test parses the import line out of the *stub* (`bundle/CLAUDE.md`),
  resolves it relative to `agent_home/CLAUDE.md`, and asserts it equals the path
  `get_home()` actually writes persona.md to. The two spellings of that path are
  tied together by the test, not by a shared constant on one side.
- `approve`: successful confirm resets the lane; begin/stale/denied do not.
- `tools`: draft and drafts hints name `action="add"` as the post-approval step.
- Full suite green; wiki pages (`components/extensions.md`,
  `flows/extension-install.md`) updated and `llm-wiki validate` clean.

## Deployment

Rebuild the image, `docker compose up -d`. Boot migrates the live volume
(known-hash path). Verify in the container: `persona.md` byte-equals the bundle,
`CLAUDE.md` is the stub, and a throwaway-directory `clone_plugin` of the
approved plugin-dev manifest succeeds end-to-end without touching `/data`.

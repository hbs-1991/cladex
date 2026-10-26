---
name: gpt-image-2
description: Generate images with GPT Image 2 via the Codex CLI.
---

# GPT Image 2

Generates and edits images with GPT Image 2 through the container's logged-in
Codex CLI (the ChatGPT subscription), then delivers the file to the chat. The
generation logic is in `scripts/gen.sh`; prompt-craft guidance is split
across `references/` by use case (see Prompt References).

## When to Use

- The user asks to generate, draw, create, or make an image / picture / poster.
- The user attaches an image and asks to edit, restyle, or remix it.
- The user names "GPT Image 2", "gpt-image-2", or "image 2".

## Prerequisites

- `codex` CLI installed in the container AND logged in (`codex login`), with a
  ChatGPT plan that includes image generation. Without it, `gen.sh` exits 3/5.
- `python3` on PATH (used for the fallback image extractor).
- The `Bash` tool: `gen.sh` runs through it, so on a lane that is not the deploy
  owner's this skill only works when the owner has granted this user a shell
  (Settings → Users).
- The `send_document` tool (mcp__hermes__send_document) for delivery.
- Read the relevant prompt reference (see Prompt References) before writing a
  non-trivial prompt.

## Prompt References

Read `references/fundamentals.md` first for any non-trivial prompt (core
structure, text-in-image rules, and how this skill differs from the raw
API), then the file matching the task:

| File                        | When                                          |
|-----------------------------|-----------------------------------------------|
| `references/fundamentals.md`| Always — core principles, no-flags caveats     |
| `references/generate.md`    | Text→image: infographics, photorealism, logos, ads, comics, UI, science, slides. §4.1a is the house recipe for pipeline / state-machine diagrams, §4.1b for architecture / scoped-zones diagrams — use them, do not improvise a layout |
| `references/edit.md`        | `--ref` edits: restyle, try-on, sketch→render, product, text-in-image, weather, removal, insert-person, compositing |
| `references/advanced.md`    | Interior swaps, holiday cards, merch concepts, character consistency via anchor |

## How to Run

Text-to-image (write to a path relative to the agent's home — the cwd — so
`send_document` can reach it afterwards):

    bash .claude/skills/gpt-image-2/scripts/gen.sh \
      --prompt "<the user's prompt>" \
      --out image.png

Image-to-image (repeat `--ref` for multi-reference composition):

    bash .claude/skills/gpt-image-2/scripts/gen.sh \
      --prompt "repaint in watercolor, keep the composition" \
      --ref input.png \
      --out image.png

## Quick Reference

| Flag            | Meaning                                             |
|-----------------|-----------------------------------------------------|
| `--prompt`      | The image prompt (required)                         |
| `--out`         | Output path, relative to the agent's home (required) |
| `--ref`         | Reference image for edits; repeatable               |
| `--timeout-sec` | Per-run timeout, default 300                         |

## Procedure

1. Shape the prompt using the Prompt References (structure first, quote any
   required text, name the medium; state aspect ratio and quality intent in
   words — there are no size/quality flags). Pass the user's intent through —
   do not over-rewrite. Done when you have a concrete prompt string.
2. Run `gen.sh` with `--out image.png`. Done when it prints the
   output path on stdout (exit 0). On a non-zero exit, name the failing layer
   from the exit code (see Pitfalls), do not retry blindly.
3. Look at the result before delivering: open the output PNG with `Read` and
   check it against the prompt — every quoted label present, spelled exactly,
   on the right element; structure and arrows as described; nothing unlabeled
   or extra. Fix a flaw by delta: re-run with the flawed file as `--ref`,
   state what must stay, describe only the change. Done when the image says
   what the prompt meant — exit 0 only means a file exists.
4. Deliver the image with `send_document`:
   `mcp__hermes__send_document(path="image.png")`. Done when it
   replies `Sent image.png ... to the chat`.
5. Reply with one short line. Do NOT describe the image at length — the user can
   see it.

## Pitfalls

- Exit 3 = `codex`/`python3` missing → the container is not set up for this
  skill; tell the user, do not pretend it worked.
- Exit 5 = `codex exec` failed → auth expired, network, or model. Exit 7 =
  imagegen produced no image (feature off, quota, or capability refused). In
  both cases the user got NO image — never claim success.
- **Two files with the same byte size are the same file.** Before delivering a
  batch, `md5sum` every output: identical hashes mean a run collected another
  run's image. That is what `gen.sh`'s lock now prevents, and the check costs a
  second — a wrong-but-valid PNG passes every other test you might run.
- Do NOT pass `--ephemeral` and do NOT remove `--enable image_generation` from
  the script; the first loses the payload, the second turns the tool off.
- Write `--out` relative to the cwd (the agent's home); `send_document` resolves
  a relative path against it and refuses anything outside it.
- Generation can take tens of seconds. That is normal, not a hang. Run each call in
  the FOREGROUND and wait for it — never `run_in_background`, never `&`. Nothing in
  this harness notifies you when a background job ends, so a backgrounded generation
  is a generation you never collect.
- A batch is N sequential calls in ONE turn, not N turns. `gen.sh` serializes itself
  (exit 8 = waited too long for another run), so concurrent calls are safe but not
  faster. Ten images is ten foreground calls back to back; that turn runs for many
  minutes, which is fine and expected.
- Each run is a fresh Codex session — "like before, but warmer" has no
  context to refer to. For a follow-up tweak, pass the previous output as
  `--ref` and describe only the delta, restating what must not change.

## Verification

Done when: `gen.sh` exited 0 and printed a path, the file exists and is
non-empty, and `send_document` replied `Sent ... to the chat`. If any of those
is missing, the user did not receive an image — say so plainly.

# The image system

One image per post, chosen from four archetypes, generated through
`.claude/skills/gpt-image-2/scripts/gen.sh`. Read that skill's
`references/fundamentals.md` before writing a non-trivial prompt, and its
`references/generate.md` for the per-use-case recipes — this file decides
*what* to draw and in what visual language; those cover prompt craft.

## Hard constraints of the pipeline

- **`gen.sh` has no `--size` flag.** Aspect ratio and quality are folded into the
  prompt wording: "a square 1:1 image, high detail". Stating it is not optional —
  an unspecified ratio comes back in whatever shape the model felt like.
- **Default ratio: square 1:1.** LinkedIn gives a square native image close to
  full mobile width. Use portrait 4:5 only for a tall stacked diagram.
- **Output path** goes under `content/img/` so `send_document`
  can reach it (it refuses anything outside the agent's home).
- **In-image text is a liability past ~6 words per label.** Image 2 handles
  typography well but not reliably at length, and it is worst at Cyrillic. Keep
  every label short and **in English**, even for the RU version of the post — a
  clean English diagram beats a mangled Russian one.

## Archetypes

**1. Concept diagram** — default for a technical claim. Boxes, arrows, one
highlighted path. Use when the post explains how something works. When the
subject is a pipeline or a state machine (often a mermaid diagram already in the
spec), build it with the house recipe in `gpt-image-2`'s
`references/generate.md` §4.1a: one card per state with an anchor icon, a ≤4-word
caption of what happens there, one amber-filled card for the node the post is
about, and sub-loops in a second section under a divider. When the subject is
structure without sequence — scoped workspaces, a shared channel, a
client/server split — use §4.1b (scoped zones) instead: a card chain forced
onto an architecture comes back as stacked clones. For a cyclic
process (a loop the reader repeats, as opposed to a one-way pipeline), lay the
steps in a circle with arrows closing clockwise back to step one, rather than a
left-to-right chain — a chain reads as one-shot even when the prompt says
"loop." If a person/human role sits inside the loop, put one icon at the
center connected to every step by thin dashed lines, not as one of the
circle's steps — that reads instantly as "the human is the connective tissue,"
which a step-in-the-chain placement does not.

**2. Before / after** — a split frame, the old way left, the new way right. Use
when the post's turn is "this replaced that".

**3. Single metaphor object** — one physical object standing in for the idea (a
narrow waist of a pipe for a tool budget, a ratchet for a one-way migration). Use
when the post is conceptual and a diagram would be fake precision.

**4. Meme** — a recognizable format with the situation substituted in. Use when
the post is a self-aware complaint about developer reality, never for an
announcement or a claim about results. A meme on a serious technical claim reads
as not believing the claim.

## Diagram discipline (archetypes 1–2)

The failures that survive a plausible-looking prompt — all three shipped in one
image on the qm post, 01.08.2026:

- **Every element gets a label.** An unlabeled bar, box or icon is a guess the
  reader has to make; if it has no name it does not belong in the diagram. The
  prompt must name each element AND quote its label — 'a long horizontal bar
  labeled "shared channel"' — never geometry alone.
- **Show multiplicity with two named instances, not N clones.** Asking for
  "three person+memory boxes" returns three identical tiles that read as
  wallpaper and eat half the canvas. Two instances with different names
  ("Alice", "Bob") say "many" better than three clones, and give the model
  less to mangle.
- **No physical metaphors inside a diagram.** A door for "access", a funnel
  for "filtering" — dropped between boxes and arrows they read literally and
  break the structure. Metaphor is archetype 3's job and a whole-image
  commitment, not a spare part.
- **The amber accent marks the post's point, exactly once.** Amber on every
  memory chip highlights nothing. Decide which single element the post is
  about before writing the prompt.
- **State the reading order and what a connection means.** "Left to right",
  "each line means writes-to" — otherwise the model connects whatever happens
  to be adjacent.

**Self-check before generating:** could a stranger who has not read the post
state its claim from the diagram alone? If not, the diagram illustrates
nothing yet — fix the structure, not the style words.

## House style

Every prompt carries the same style block so a month of posts looks like one
account rather than four stock-image vendors:

    flat vector illustration, thick clean outlines, limited palette of deep
    navy, off-white and one warm accent (amber), generous negative space,
    no gradients, no photorealism, no stock-photo people

The meme archetype is the one exception: it uses whatever the recognizable format
requires, or the style above rendered as a two-panel comic.

## Prompt recipe

Assemble in this order — subject, structure, labels, style, ratio:

    <archetype and subject in one clause>. <Explicit structure: what is where,
    what connects to what>. Text labels, exactly: "<label 1>", "<label 2>".
    <house style block>. A square 1:1 image, high detail.

Worked example for a post about a scheduled agent that researches overnight:

    bash .claude/skills/gpt-image-2/scripts/gen.sh \
      --prompt 'A concept diagram of an overnight automation loop. A clock at the
    top left feeds an arrow into a central rounded box; three arrows leave the box
    to three small document icons on the right; a single arrow returns from the
    documents to a phone at the bottom. Text labels, exactly: "10:00", "agent",
    "brief". Flat vector illustration, thick clean outlines, limited palette of
    deep navy, off-white and one warm accent (amber), generous negative space, no
    gradients, no photorealism, no stock-photo people. A square 1:1 image, high
    detail.' \
      --out content/img/overnight-brief-01.png

## Series consistency

To keep a recurring visual motif, pass a previous post's image as a reference
instead of re-describing the style:

    bash .claude/skills/gpt-image-2/scripts/gen.sh \
      --prompt 'Keep the exact visual style, palette and line weight. New subject:
    <the new diagram>.' \
      --ref content/img/<previous>-01.png \
      --out content/img/<new>-01.png

## Pitfalls

- **Long or Cyrillic in-image text.** The failure is silent — the image looks fine
  at a glance and the letters are wrong up close. Look at every label before
  sending.
- **Delivering unseen.** Exit 0 means a file exists, not a correct diagram. Open
  the PNG (`Read` renders it) and check every label and every connection against
  the intended structure before `send_document`. Fix a flaw by delta — the flawed
  file as `--ref`, state what stays, describe only the change — never by
  re-rolling the whole prompt.
- **Restating the post inside the image.** The image is a second entry point, not
  a summary. If the diagram is the post reworded, cut the words from it.
- **Fake precision.** A diagram implies the structure is real. If the post is
  opinion, use archetype 3, not archetype 1.
- **A meme on a results claim.** Undercuts the claim it illustrates.
- **Regenerating on a bad exit code.** Exits 3/5/7 are environment failures (CLI
  missing, auth, imagegen off or out of quota), not prompt problems. Rewording and
  re-running burns minutes and changes nothing.

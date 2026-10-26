# Prompting fundamentals

Distilled from OpenAI's official image-gen prompting guide
(https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide).
Read this file before writing any non-trivial prompt; then pick the use-case
file: `generate.md` (text→image, §4), `edit.md` (`--ref` edits and
compositing, §5), or `advanced.md` (interior swaps, cards, merch, character
consistency, §6).

## How this skill differs from the raw API

`gen.sh` drives generation through the Codex CLI's `imagegen` tool, which
takes the prompt as free text. None of the API parameters exist here — no
`quality`, no `size`, no `input_fidelity`, no `n`, no `background`. Everything
those parameters control must be expressed in the prompt wording itself:

- size/ratio → "a 16:9 landscape poster", "vertical 2:3 portrait format"
- quality → "high detail, print-quality", "crisp legible small text"
- fidelity → "preserve the composition and identity exactly"

Also: each `gen.sh` run is a **fresh Codex session**. There is no "same style
as before" or "make it warmer" follow-up — the model remembers nothing between
runs. To iterate, either re-run with an edited prompt, or pass the previous
output as `--ref` and describe only the delta.

## Core principles

1. **Structure first.** Order the prompt as
   `background / scene → subject → key details → constraints`. For complex
   requests use short labeled segments or line breaks, not one long paragraph.
   Name the intended use ("an ad", "a UI mock", "an infographic") — it sets
   the model's mode and polish level.
2. **Format is flexible.** Minimal phrases, full descriptions, JSON-like, or
   instruction-style prompts all work. Pick whatever expresses the intent most
   precisely — do not force prose if a structured list is clearer.
3. **Specificity + quality cues.** Name materials, shapes, textures, and the
   medium ("photorealistic", "watercolor brushstrokes", "matte ceramic").
   Add targeted quality levers only when needed ("subtle film grain",
   "textured brushstrokes", "macro detail"). Vague prompts get generic
   renders.
4. **Choose aspect ratio early, state it in words.** Decide the ratio up
   front and put it in the prompt ("landscape 16:9", "square", "tall poster
   format"). There is no size flag to reinforce it — the wording is the only
   signal, so make it unambiguous.
5. **One hero, supporting cast.** In a complex scene, name the primary
   subject explicitly and demote the rest to background, or the model spreads
   focus evenly and nothing reads as the point.
6. **Ask for legibility on dense content.** Text-heavy layouts, diagrams,
   multi-panel comics, and infographics need explicit cues: "readable
   labels", "clean typography", "high print-quality detail" — otherwise fine
   detail smears.

## Text in images

- Put literal text in straight quotes or ALL CAPS, with an explicit demand:
  "EXACT, verbatim, no extra characters", "text appears once".
- Specify typography: font style, size, color, placement.
- For brand names or uncommon spellings, spell the word letter-by-letter.
- Close with the standard exclusions: "no extra text, no watermarks, no
  unrelated logos".

## Composition and camera

- Specify framing and viewpoint (close-up, wide, top-down), perspective
  (eye-level, low-angle), and lighting/mood (soft diffuse, golden hour,
  high-contrast).
- Call out placement for layout-critical work: "logo top-right", "subject
  centered with negative space on the left".
- Detailed camera specs are interpreted loosely — use them for the high-level
  look ("shot on 35mm film, shallow depth of field"), not exact optics.
- Wide, cinematic, low-light, rain, or neon scenes: add extra detail about
  scale, atmosphere, and color, or the model trades mood for surface realism.

## People, pose, action

Describe scale, body framing, gaze, and object interactions explicitly:
"full body visible, feet included", "child-sized relative to the table",
"looking down at the open book, not at the camera", "hands naturally gripping
the handlebars". This is what fixes body proportions, action geometry, and
gaze alignment.

## Constraints: what to change vs what to preserve

- State exclusions and invariants explicitly: "no watermark", "no extra
  text", "no logos", "preserve identity / geometry / layout".
- For edits, the universal pattern is **"change only X" + "keep everything
  else the same"** — see `edit.md`.
- Repeat the preserve list on every iteration; nothing carries over between
  runs.

## Iterate instead of overloading

Long prompts work, but debugging is easier with a clean base plus small,
single-change follow-ups. Since every run is a fresh session, iterate one of
two ways: tweak the prompt and regenerate (composition still fluid), or feed
the last output back as `--ref` and describe only the delta ("make the
lighting warmer, change nothing else") once the composition is right.
Re-state critical constraints on every run.

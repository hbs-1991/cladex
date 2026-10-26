# Generate: text → image (use cases 4.1–4.10)

Verbatim use-case recipes from OpenAI's gpt-image prompting guide, §4. Read
`fundamentals.md` first. Example prompts are kept close to the original —
treat them as skeletons and fill with the user's specifics; do not copy
boilerplate the user did not ask for.

Skill adaptation: the guide sets `size` and `quality` per call; here both go
into the prompt as words ("portrait format", "high detail, readable labels").
Where the guide uses `n=4` for variants, run `gen.sh` several times.

## 4.1 Infographics

Explain structured information for a specific audience: students,
executives, customers. Explainers, posters, labeled diagrams, timelines.
Dense layouts and heavy in-image text need explicit legibility cues.

```
Create a detailed Infographic of the functioning and flow of an automatic
coffee machine like a Jura. From bean basket, to grinding, to scale, water
tank, boiler, etc. I'd like to understand technically and visually the flow.
```

## 4.1a Technical flow infographic (house recipe)

The default for a pipeline, a state machine, or anything that arrives as a
mermaid diagram in a spec. It is the shape validated on the loop-orchestrator
post (`content/img/loop-mvp-run-lifecycle-*.png`) — reuse it instead of
inventing a layout per post, so a run of diagrams reads as one account.

**The shape.** A chain of rounded cards, one per state, connected by arrows in
reading order. Each card carries three things: an **anchor icon**, the state
name in bold, and a caption of ≤4 words saying what actually happens there
("fresh sandbox, secrets", "push, fast-forward"). Exactly one card is filled
with the amber accent — the node the post is about. Terminal or error states
(`failed`) hang off a dashed line running alongside the whole chain, with the
arrowhead pointing *into* the error box. Sub-loops (a retry cycle inside one
state) go in a second section under a full-width divider, with its own small
heading.

**Anchor icons** carry the meaning the label cannot: a robot head for an agent,
a robot head with a magnifying glass for a reviewer, a container with a lock for
a sandbox, a check in a circle for done. For a product mark, describe the shape,
never the brand name — "a cat-octopus mark in a circle, GitHub visual style", "a
paper plane in a circle, Telegram visual style". Naming the brand risks a refusal
and buys nothing.

**Orientation.** Landscape 16:9 for a chain of ~7 cards (icon above the name,
caption under the card). Portrait 4:5 for the same content stacked vertically
(icon left, name and caption right, inside the card). Square 1:1 only for a
two-zone diagram. State it in words — there is no size flag.

A long column overrides the stated ratio: asking for "portrait 4:5" with seven
stacked cards plus a sub-loop came back at roughly 1:2.2, far narrower than
asked, because the content dictated the canvas. LinkedIn crops below 4:5, so the
diagram lands smaller in the feed. Either bound it — "portrait 4:5, and no
narrower than 4:5; widen the cards rather than stretching the canvas" — or split
the content into two side-by-side columns. Check the pixel dimensions of the
output, not just that a file appeared.

Prompt skeleton, segmented (structure beats prose here):

```
Use the attached image ONLY as a style reference: same palette, same thick
clean outlines, same typography, same flat vector look. The LAYOUT IS NEW:
<landscape 16:9 | portrait 4:5>.

TOP AREA: small heading at the top left: "<SECTION>". Below it, one
<horizontal row | vertical column> of <N> rounded cards connected by
<right-pointing | downward> arrows. Each card: <icon placement>, the name in
bold, the caption in smaller text. In order:
1. icon: <shape>. Name: "<state>". Caption: "<≤4 words>"
...
The "<accent state>" card is filled with the amber accent and has a slightly
thicker outline; every other card is a navy outline on off-white.
<dashed error line description, arrowhead pointing INTO the error box>

BOTTOM AREA, under a thin full-width divider: small heading "<SUB-LOOP>".
<two boxes, the curved arrows that close the cycle, and the amber exit arrow
that must pass BELOW both boxes without touching them>

Flat vector infographic, thick clean outlines, limited palette of deep navy,
off-white and one warm amber accent, generous negative space, no gradients,
no photorealism, no people. Crisp legible typography, each label rendered
exactly once and spelled exactly as quoted, correct English spelling, high
print-quality detail. A <ratio> image. No extra text, no watermarks.
```

**Always look at the result before delivering it.** Exit 0 means a file, not a
correct diagram. The two failures this recipe actually hit:

- **Labels swap places.** `loop/run-42` landed on the box that should have read
  `PR branch`. The picture looks clean; only reading it catches this.
- **An arrow routes through a box it should bypass.** The "clean, or budget
  spent" exit went *through* `fix task`, inverting the meaning. Say explicitly
  "passing clearly BELOW both boxes without touching them".
- **An arrowhead points the wrong way** into a terminal state, so `failed`
  reads as feeding `done`.

**Fix by delta, never by re-rolling the whole prompt.** Pass the flawed image as
`--ref`, list what stays ("keep the seven cards, their names, captions and the
amber card exactly as they are"), then describe only the change. Re-running the
original prompt loses the parts that were already right.

## 4.1b Architecture / scoped-zones diagram (house recipe)

For structure without sequence — per-user scoped workspaces sharing a channel,
a narrow-waist toolset, a client/server split. §4.1a's card chain assumes an
order; forcing an architecture into it produces a stack of identical clones
with an unlabeled bar underneath (the qm-post failure, 2026-08-01).

**The shape.** Two — never more than three — large rounded zones side by side,
each with a bold header naming a *different* instance ("Alice's workspace",
"Bob's workspace"), each containing 2–3 small labeled icons (the icon carries
the meaning, the ≤3-word label removes the guess). The shared element is drawn
ONCE, spanning or touching every zone, with its own label and the amber fill —
it is usually what the post is about. Every remaining element is navy outline
on off-white. Identical repeated zones are the failure mode, not the point:
two named instances plus negative space read as "many"; three clones read as
wallpaper.

Prompt skeleton:

```
A concept diagram of <the claim in one clause>. Square 1:1.

LEFT AND RIGHT, side by side: two large rounded rectangles. Left header, bold:
"<instance A>"; right header, bold: "<instance B>". Inside each: <2-3 icons,
each with its quoted ≤3-word label>.

SPANNING THE FULL WIDTH BELOW both rectangles, touching each with a short
vertical connector: one horizontal bar filled with the amber accent, labeled
"<shared element>". Each connector means <what the connection means>.

Flat vector illustration, thick clean outlines, limited palette of deep navy,
off-white and one warm amber accent, generous negative space, no gradients,
no photorealism, no people. Each label rendered exactly once, spelled exactly
as quoted, crisp legible typography. No extra text, no watermarks, no extra
icons, no doors, no arrows other than described.
```

The §4.1a rules apply unchanged: look at the result before delivering, and fix
by delta with `--ref`, never by re-rolling the prompt.

## 4.2 Translation in images

Localize an existing design (ad, UI screenshot, packaging, infographic) into
another language without rebuilding the layout. Preserve everything except
the text: typography style, placement, spacing, hierarchy; translate
verbatim, no extra words, no reflow, no edits to logos or imagery.
Needs the source image as `--ref`.

```
Translate the text in the infographic to Spanish. Do not change any other
aspect of the image.
```

## 4.3 Photorealistic images that feel natural

Prompt as if a real photo is being captured in the moment. Photography
language (lens, lighting, framing), explicit real texture (pores, wrinkles,
fabric wear), no words implying studio polish or staging.

```
Create a photorealistic candid photograph of an elderly sailor standing on a
small fishing boat. He has weathered skin with visible wrinkles, pores, and
sun texture, and a few faded traditional sailor tattoos on his arms. He is
calmly adjusting a net while his dog sits nearby on the deck. Shot like a
35mm film photograph, medium close-up at eye level, using a 50mm lens. Soft
coastal daylight, shallow depth of field, subtle film grain, natural color
balance. The image should feel honest and unposed, with real skin texture,
worn materials, and everyday detail. No glamorization, no heavy retouching.
```

## 4.4 World knowledge

The model pairs reasoning with world knowledge: a specific time and place is
enough to infer the event and render period-accurate detail. (Bethel, NY,
August 1969 → Woodstock, without naming it.)

```
Create a realistic outdoor crowd scene in Bethel, New York on August 16,
1969. Photorealistic, period-accurate clothing, staging, and environment.
```

## 4.5 Logo generation

Clear brand constraints + simplicity. Describe the brand's personality and
use case, ask for a clean, original mark with strong shape, balanced
negative space, and scalability. For variants, run `gen.sh` several times
(the guide uses `n=4`).

```
Create an original, non-infringing logo for a company called Field & Flour,
a local bakery. The logo should feel warm, simple, and timeless. Use clean,
vector-like shapes, a strong silhouette, and balanced negative space. Favor
simplicity over detail so it reads clearly at small and large sizes. Flat
design, minimal strokes, no gradients unless essential. Plain background.
Deliver a single centered logo with generous padding. No watermark.
```

## 4.6 Ads generation

Write the prompt like a creative brief, not a technical spec: brand,
audience, culture, concept, composition, and exact copy — then let the model
make taste-driven decisions inside those boundaries. Quote the tagline
exactly and ask for clean, legible typography.

```
Give me a cool in culture ad / fashion shot for a brand called Thread. It's
a hip young street brand. The ad shows a group of friends hanging out
together with the tagline "Yours to Create." Make it feel like a polished
campaign image for a youth streetwear audience: stylish, contemporary,
energetic, and tasteful. Use clean composition, strong color direction,
natural poses, and premium fashion photography cues. Render the tagline
exactly once, clearly and legibly, integrated into the ad layout. No extra
text, no watermarks, no unrelated logos.
```

## 4.7 Story-to-comic strip

Define the narrative as a sequence of clear visual beats, one per panel.
Keep descriptions concrete and action-focused.

```
Create a short vertical comic-style reel with 4 equal-sized panels.
Panel 1: The owner leaves through the front door. The pet is framed in the
window behind them, small against the glass, eyes wide, paws pressed high,
the house suddenly quiet.
Panel 2: The door clicks shut. Silence breaks. The pet slowly turns toward
the empty house, posture shifting, eyes sharp with possibility.
Panel 3: The house transformed. The pet sprawls across the couch like it
owns the place, crumbs nearby, sunlight cutting across the room like a
spotlight.
Panel 4: The door opens. The pet is seated perfectly by the entrance, alert
and composed, as if nothing happened.
```

For a recurring character across strips, see the character anchor workflow
in `advanced.md`.

## 4.8 UI mockups

Describe the product as if it already exists. Layout, hierarchy, spacing,
real interface elements; avoid concept-art language so the result looks
shipped, not sketched.

```
Create a realistic mobile app UI mockup for a local farmers market. Show
today's market with a simple header, a short list of vendors with small
photos and categories, a small "Today's specials" section, and basic
information for location and hours. Design it to be practical, and easy to
use. White background, subtle natural accent colors, clear typography, and
minimal decoration. It should look like a real, well-designed, beautiful app
for a small local market. Place the UI mockup in an iPhone frame.
```

## 4.9 Scientific / educational visuals

Prompt like an instructional design brief: audience, lesson objective,
visual format, required labels, scientific constraints. List required
components explicitly and say what must NOT appear. The guide uses
`quality="high"` here — ask for clean readable labels in words.

```
Create a simple biology diagram titled "Cellular Respiration at a Glance"
for high school students.

Show how glucose turns into energy inside a cell. Include glycolysis, the
Krebs cycle, and the electron transport chain. Use arrows to connect the
steps, and label the main molecules: glucose, pyruvate, ATP, NADH, FADH2,
CO2, O2, and H2O. Make it look like a clean classroom handout or slide, with
a white background, simple icons, clear labels, and easy-to-read text.

Avoid tiny text, extra decoration, or anything that makes the diagram hard
to understand.
```

## 4.10 Slides, diagrams, charts, productivity images

Write the prompt like an artifact spec: name the exact deliverable, define
the canvas and hierarchy, provide the real text and data, describe the
visual language, and add practical constraints. Landscape ratio for
deck-style outputs; include numbers and labels directly in the prompt.

```
Create one pitch-deck slide titled "Market Opportunity" that feels like a
real Series A fundraising slide from a YC-backed startup. Landscape 16:9.

Use a clean white background, modern sans-serif typography like Inter, and a
crisp, minimal layout. The slide should include:
* A TAM/SAM/SOM concentric-circle diagram in muted blues and grays
* Specific, believable market sizing numbers:
  TAM: $42B, SAM: $8.7B, SOM: $340M
* A clean bar chart below showing market growth from 2021 to 2026, with a
  subtle upward trend
* Small footnotes: "AGI Research, 2024" and "Internal analysis"
* A company logo placeholder in the bottom-right corner

The design should look like it belongs in a deck that actually raised money:
highly readable text, clear data hierarchy, polished spacing, and
professional startup-style visual language.

Avoid clip art, stock photography, gradients, shadows, decorative elements,
or anything that feels generic or overdesigned.
```

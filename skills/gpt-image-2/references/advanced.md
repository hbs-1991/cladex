# Additional high-value use cases (6.1–6.4)

Verbatim recipes from OpenAI's gpt-image prompting guide, §6. Read
`fundamentals.md` first; 6.1 and 6.4 take a `--ref` image.

## 6.1 Interior design swap (precision edits)

Visualize furniture or decor changes in a real space without re-rendering
the scene. Surgical realism: swap a single object while preserving camera
angle, lighting, shadows, and surrounding context — a real photograph, not a
redesign. Source photo as `--ref`.

```
In this room photo, replace ONLY the white chairs with chairs made of wood.
Preserve camera angle, room lighting, floor shadows, and surrounding
objects.
Keep all other aspects of the image unchanged.
Photorealistic contact shadows and fabric texture.
```

## 6.2 3D pop-up holiday card (product-style mock)

Seasonal marketing concepts and print previews. Emphasize tactile realism —
paper layers, fibers, folds, soft studio lighting — so the result reads as a
photographed physical product, not a flat illustration. Structure the prompt
in labeled blocks (Scene / Mood / Style / Constraints / text).

```
Create a Christmas holiday card illustration.

Scene:
a cozy Christmas scene with an old teddy bear sitting inside a keepsake box,
slightly worn fur, soft stitching repairs, placed near a window with falling
snow outside. The scene suggests the child has grown up, but the memories
remain.

Mood:
Warm, nostalgic, gentle, emotional.

Style:
Premium holiday card photography, soft cinematic lighting, realistic
textures, shallow depth of field, tasteful bokeh lights, high print-quality
composition.

Constraints:
- Original artwork only
- No trademarks
- No watermarks
- No logos

Include ONLY this card text (verbatim):
"Merry Christmas — some memories never fade."
```

## 6.3 Collectible action figure / plush keychain (merch concept)

Early merch ideation and pitch visuals. Premium product photography cues
(materials, packaging, print clarity) with original, non-infringing designs.
Good for testing multiple character or packaging variants quickly (separate
runs).

```
Create a collectible action figure of a vintage-style toy propeller airplane
with rounded wings, a front-mounted spinning propeller, slightly worn paint
edges, classic childhood proportions, designed as a nostalgic holiday
collectible, in blister packaging.

Concept:
A nostalgic holiday collectible inspired by the simple toy airplanes
children used to play with during winter holidays. Evokes warmth,
imagination, and childhood wonder.

Style:
Premium toy photography, realistic plastic and painted metal textures,
studio lighting, shallow depth of field, sharp label printing, high-end
retail presentation.

Constraints:
- Original design only
- No trademarks
- No watermarks
- No logos

Include ONLY this packaging text (verbatim):
"Christmas Memories Edition"
```

## 6.4 Children's book art with character consistency (multi-image workflow)

For multi-page illustration pipelines where character drift is unacceptable.
A reusable **character anchor** ensures continuity across scenes, poses, and
pages. Because every `gen.sh` run is a fresh session, the anchor image is
the ONLY carrier of consistency — always pass it as `--ref` and always
repeat the "do not redesign" block.

**Step 1 — character anchor** (plain generation, save the output):

```
Create a children's book illustration introducing a main character.

Character:
A young, storybook-style hero inspired by a little forest outlaw, wearing a
simple green hooded tunic, soft brown boots, and a small belt pouch. The
character has a kind expression, gentle eyes, and a brave but warm demeanor.
Carries a small wooden bow used only for helping, never harming.

Theme:
The character protects and rescues small forest animals like squirrels,
birds, and rabbits.

Style:
Children's book illustration, hand-painted watercolor look, soft outlines,
warm earthy colors, whimsical and friendly. Proportions suitable for picture
books (slightly oversized head, expressive face).

Constraints:
- Original character (no copyrighted characters)
- No text
- No watermarks
- Plain forest background to clearly showcase the character
```

**Step 2 — story continuation** (anchor image as `--ref`):

```
Continue the children's book story using the same character.

Scene:
The same young forest hero is gently helping a frightened squirrel out of a
fallen tree after a winter storm. The character kneels beside the squirrel,
offering reassurance.

Character Consistency:
- Same green hooded tunic
- Same facial features, proportions, and color palette
- Same gentle, heroic personality

Style:
Children's book watercolor illustration, soft lighting, snowy forest
environment, warm and comforting mood.

Constraints:
- Do not redesign the character
- No text
- No watermarks
```

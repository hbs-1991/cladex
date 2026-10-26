# Edit: text + image → image (use cases 5.1–5.9)

Verbatim use-case recipes from OpenAI's gpt-image prompting guide, §5. All
of these take the source image(s) via `--ref`. Read `fundamentals.md` first.

The universal edit pattern: **"change only X" + "keep everything else the
same"**, plus explicit exclusions ("no watermark", "no extra text", "no
logos"). Where the guide sets `input_fidelity="high"` for
composition-sensitive edits, compensate in words: "preserve identity,
geometry, camera angle, and object placement exactly". Each `gen.sh` run is
a fresh session — repeat the full preserve list on every iteration.

## 5.1 Style transfer

Keep the visual language of the reference (palette, texture, brushwork,
grain) while changing the subject. Describe what must stay consistent and
what must change; add hard constraints (background, framing, "no extra
elements") to prevent drift.

```
Use the same style from the input image and generate a man riding a
motorcycle on a white background.
```

## 5.2 Virtual clothing try-on

Identity preservation is critical: explicitly lock the person, allow changes
only to garments, require realistic fit and matched lighting so the outfit
looks worn, not pasted. Pass the person photo and each garment as `--ref`.

```
Edit the image to dress the woman using the provided clothing images. Do not
change her face, facial features, skin tone, body shape, pose, or identity
in any way. Preserve her exact likeness, expression, hairstyle, and
proportions. Replace only the clothing, fitting the garments naturally to
her existing pose and body geometry with realistic fabric behavior. Match
lighting, shadows, and color temperature to the original photo so the outfit
integrates photorealistically, without looking pasted on. Do not change the
background, camera angle, framing, or image quality, and do not add
accessories, text, logos, or watermarks.
```

## 5.3 Drawing → image (rendering)

Turn rough drawings into photorealistic concepts while keeping the original
intent. Treat the prompt like a spec: preserve layout and perspective, add
realism via plausible materials and lighting, forbid reinterpretation.

```
Turn this drawing into a photorealistic image.
Preserve the exact layout, proportions, and perspective.
Choose realistic materials and lighting consistent with the sketch intent.
Do not add new elements or text.
```

## 5.4 Product mockups (clean background + label integrity)

For catalogs and marketplaces. Success = edge quality (clean silhouette, no
fringing/halos) + label integrity (text sharp and unchanged). Keep the
background opaque; if a transparent asset is needed, remove the background
in a downstream step.

```
Extract the product from the input image and place it on a plain white
opaque background.
Output: centered product, crisp silhouette, no halos/fringing.
Preserve product geometry and label legibility exactly.
Add only light polishing and a subtle realistic contact shadow.
Do not restyle the product; only remove background and lightly polish.
```

## 5.5 Marketing creatives with real text in-image

Typography needs explicit constraints: exact copy in quotes, verbatim
rendering demanded, placement and font style described. If text fidelity is
imperfect, keep the prompt strict and iterate — small wording/layout tweaks
usually improve legibility.

```
Create a realistic billboard mockup of the shampoo on a highway scene during
sunset.
Billboard text (EXACT, verbatim, no extra characters):
"Fresh and clean"
Typography: bold sans-serif, high contrast, centered, clean kerning.
Ensure text appears once and is perfectly legible.
No watermarks, no logos.
```

## 5.6 Lighting and weather transformation

Re-stage a photo for a different mood, season, or time of day. Change only
environmental conditions — lighting direction/quality, shadows, atmosphere,
precipitation, ground wetness — preserving identity, geometry, camera angle,
and object placement so it reads as the same original photo.

```
Make it look like a winter evening with snowfall. Preserve the composition,
camera angle, and all objects exactly.
```

## 5.7 Object removal

Short and strict. State what to remove and that nothing else changes.

```
Remove the flower from man's hand. Do not change anything else.
```

## 5.8 Insert the person into a scene

For storyboards, campaigns, "what if" scenarios where identity preservation
matters. Anchor realism with a grounded photographic look (natural lighting,
believable detail, no cinematic grading) and lock what must not change about
the subject.

```
Generate a highly realistic action scene where this person is running away
from a large, realistic brown bear attacking a campsite. The image should
look like a real photograph someone could have taken, not an overly enhanced
or cinematic movie-poster image.
She is centered in the image but looking away from the camera, wearing
outdoorsy camping attire, with dirt on her face and tears in her clothing.
She is clearly afraid but focused on escaping, running away from the bear as
it destroys the campsite behind her.
The campsite is in Yosemite National Park, with believable natural details.
The time of day is dusk, with natural lighting and realistic colors.
Everything should feel grounded, authentic, and unstyled, as if captured in
a real moment. Avoid cinematic lighting, dramatic color grading, or stylized
composition.
```

## 5.9 Multi-image referencing and compositing

Combine elements from several `--ref` inputs into one believable image.
Specify what to transplant, where it goes, and what must remain unchanged;
ask for matched lighting, perspective, scale, and shadows. Reference inputs
by index in `--ref` order ("image 1", "the second image").

```
Place the dog from the second image into the setting of image 1, right next
to the woman, use the same style of lighting, composition and background.
Do not change anything else.
```

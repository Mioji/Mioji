# Visual system

The profile uses one palette throughout: near-black plum backgrounds,
electric violet and rose-magenta accents, and off-white text. Frames have a
chamfered top-right corner and accent brackets on two opposite corners;
hairline rules separate content. All tokens live in `configs/theme.json`.

## Panels

Every panel is an SVG generated in two variants. The desktop variant is 960
units wide, the compact variant 480; the README serves the compact one
through `<picture>` on viewports up to the theme's `compact_max_width`.
Text is wrapped by the generator, because SVG has no layout engine, and
panel heights grow with the wrapped copy.

Panels with artwork embed a WebP scene as a `data:` URI, clipped to the
frame and darkened by a per-panel overlay from `configs/theme.json` so text
contrast does not depend on the image. Embedding keeps each SVG
self-contained, which is required: browsers do not load external resources
referenced by an SVG that is displayed through an `<img>` element.

Each panel links to the most relevant destination: project cards to their
repositories, the activity and statistics panels to the GitHub profile, and
the knowledge panel to the first enabled contact.

## Artwork

`assets/profile/banner.webp` (2172 by 724 pixels) is an original anime
illustration of a character in front of a night city, generated from the
profile avatar as a style reference. The panel and project backgrounds
continue the same city without characters, so the banner remains the only
figure on the page.

Every image has its exact generation prompt next to it under
`assets/profile/backgrounds/prompts/`; the generator refuses to embed
artwork whose prompt is missing. Rejected candidates are not kept in the
repository. The images are licensed under CC BY-NC-ND 4.0 and the prompts
under the MIT License, so the prompts can be reused to generate independent
artwork while the images stay protected; see `LICENSE`, Sections 3 and 4.
Banner prompt, with the avatar supplied as the style reference:

```text
Create a NEW ORIGINAL panoramic GitHub banner, approximately 2100x700, 3:1.
The supplied image is ONLY a reference for the anime drawing style, vivid
rose-pink hair colors, violet illumination and expressive sparkling eyes.
Do not copy its composition, face, pose, heart-shaped pupils, clothing or
facial expression.

The new scene: a stylish anime woman with flowing dusty-rose hair on the far
right, shown from the shoulders up in a three-quarter side view, looking
towards the left across an out-of-focus futuristic night city. One clearly
visible bright violet-magenta eye with natural round pupil and crystalline
anime reflections, a subtle confident smile. She wears a high-neck black
technical jacket and one small tasteful violet-lit earpiece; restrained
cyberpunk clothing, no bare chest.

Beautiful polished 2D anime illustration with the same rich magenta rim-light
and luminous fine hair strands as the reference. Hair is gently swept
horizontally towards the center. The left 60% is a dark cinematic futuristic
night scene: deep plum shadows, a few softly blurred pink neon reflections
and delicate violet architectural lines, layered and atmospheric, with ample
calm negative space.

Cyberpunk graphic design integrated at the edges: a few precise thin angled
violet lines and restrained rose scan-light accents. Preserve the reference's
colorful beautiful anime mood; do not make it grim, gory, grungy, armored or
photorealistic. Very carefully composed luxury anime cyberpunk banner,
elegant, visually balanced, not a collage. Palette almost black, deep violet,
rose pink and small lavender-white highlights, absolutely no cyan or green.
The character occupies approximately the right 30% only.

No massive skyline towers, no space ring, no close-up fragment of an isolated
eye, no transparent glass covering the face, no giant polygon plates.
Absolutely NO TEXT, lettering, numbers, names, symbols resembling writing,
watermarks, logos, UI status labels or terminal commands anywhere.
This is the actual banner asset, not a webpage mockup.
```

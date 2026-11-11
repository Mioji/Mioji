# Artwork prompts

Each Markdown file records the exact prompt used to generate the WebP image
named in its `Output` line. `assets/profile/banner.webp` was the visual
reference for every prompt. The backgrounds contain no characters, so the
banner character stays unique on the page.

## License status

The prompt text is licensed under the MIT License. It is a reference for
generating your own, independent artwork, and you may adapt it freely. The
images in this repository are not covered by that grant: they remain under
CC BY-NC-ND 4.0, may not be copied into another profile or adapted, and
should be replaced when this repository is used as a template. See `LICENSE`,
Sections 3 and 4.

Panel artwork lives in `assets/profile/backgrounds/<panel>.webp`; project
artwork in `assets/profile/backgrounds/projects/<key>.webp`, where `<key>` is
the project's `background_key` in `configs/profile.json`. The generator
embeds an image only if its prompt file exists, so replace or regenerate an
image and its prompt together.

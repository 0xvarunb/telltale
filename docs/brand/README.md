# Telltale brand

The logo is the word Telltale with its "ll" drawn as two indicator lamps, green and orange, like the telltale lights on a ship's panel. Tagline: "Someone has done this job before."

## Files

| File | Use |
| --- | --- |
| `telltale-logo.svg` / `.png` | Main logo, for light backgrounds |
| `telltale-logo-on-dark.svg` / `.png` | Main logo, for dark backgrounds |
| `telltale-logo-tagline.svg` / `.png` | Logo with tagline, for infographics, banners and cards |
| `telltale-logo-tagline-on-dark.svg` / `.png` | The same, for dark backgrounds |
| `telltale-icon.svg` / `.png` | The two lamps alone, for profile pictures and favicons |
| `telltale-icon-on-dark.svg` / `.png` | The same, on ink |

The PNGs have transparent backgrounds (logos 2400 px wide, icons 1024 px). Prefer the SVGs wherever they are accepted: they stay sharp at any size. The lettering is drawn as shapes, so no font needs to be installed to view them.

## Rules

- Colours: ink `#13283b`, paper `#f4f1ea`, orange `#e8501a`, green `#2f6b4a`. On dark backgrounds the green lamp is the lighter tint `#4f9a70` so it stays visible.
- The green lamp always comes first and the orange lamp second, never the other way round.
- Leave clear space around the logo of at least the width of one lamp.
- Below about 120 px wide, use the icon instead of the wordmark.
- Do not stretch, recolour, outline or add effects.

## Pages and cards: what to avoid (17 Nov 2026)

Varun's review of the build log page and the post cards found patterns that read as generic,
AI-made design. Avoid them on every page, card and post image:

- **Dark bands and dark boxes.** No navy panels, stat strips or "dashboard" blocks. Everything sits
  on paper. Ink is for type and hairlines, not for filled areas.
- **Paper, not a flat screen colour.** Light off-white paper with a faint grain. One light look;
  no dark mode for these pages.
- **Coloured rails on the side of a box** (a thick left border as decoration). Use a rule or
  nothing.
- **Everything in a card.** Rounded, bordered boxes for each item, tiles in a grid, pills and
  badges. Use typography, spacing and hairline rules instead, like a printed log or report.
- **Uppercase monospace labels everywhere.** At most one small label style, used sparingly.
- **Big-number tiles** unless the numbers are the point; then set them as type in a row with
  hairlines, not as tiles.
- **Decoration that says nothing:** numbered markers on things that are not a sequence, icons as
  section markers, emoji, gradients, glows, shadows.
- **Ship-world conventions are welcome when they carry content:** a deck-log layout with dated
  rows, real units, report page numbers.

## Fonts and rebuilding

Wordmark: EB Garamond SemiBold. Tagline: Geist Medium. Both are under the SIL Open Font License 1.1, which allows their use in logos. The font files are not committed.

`build.py` rebuilds the SVGs from the two font files (TTF), placed in one folder as `garamond600.ttf` and `geist500.ttf`:

```bash
uv run --no-project --with fonttools --with uharfbuzz python -I docs/brand/build.py <font-folder> docs/brand
```

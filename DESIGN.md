# vetstar v2 design system

This file sets vetstar's fonts, colours and component rules. The palette is
generated, so change it in [`docs/design/palette.py`](docs/design/palette.py)
and rerun it; it rewrites `docs/design/palette.json` and `frontend/src/theme.css`.

```
python docs/design/palette.py
```

## Fonts (Google Fonts)

| Role | Family | Weights | Use |
|---|---|---|---|
| Display | [Space Grotesk](https://fonts.google.com/specimen/Space+Grotesk) | 500, 600, 700 | Headings, verdict titles, big numbers |
| Body | [Plus Jakarta Sans](https://fonts.google.com/specimen/Plus+Jakarta+Sans) | 400, 500, 600, 700 | All running text and controls |
| Data | [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono) | 400, 600 | TIC IDs, periods, epochs, code |

Tailwind: `font-display`, `font-sans` (default), `font-mono`. Tables use
tabular figures so columns of numbers line up.

Why these: Space Grotesk has a slightly technical, instrument-panel feel for
headings without being hard to read. Plus Jakarta Sans stays clear at small
sizes, which matters for the dense vetting tables. JetBrains Mono separates
0/O and 1/l, which matters for TIC IDs and epochs.

## Brand colours

Two owner-chosen sets, mixed:

| Warm | Hex | | Cool | Hex |
|---|---|---|---|---|
| Charcoal | `#454545` | | Dusk | `#453C67` |
| Ember | `#FF6000` | | Iris | `#6D67E4` |
| Apricot | `#FFA559` | | Lagoon | `#46C2CB` |
| Cream | `#FFE6C7` | | Lime | `#F2F7A1` |

**How they mix.** The cool set carries structure: dusk for headings and the
dark theme, iris as the primary colour, lagoon for focus and links in dark
mode. The warm set brings energy and warmth: ember is the one call-to-action
colour (Run NEMESIS, Search), apricot is for warnings and the dark-mode accent,
cream is the light-mode page background. In OKLCH, iris (hue 281°) and
ember (42°) are about 120° apart, a triad-like contrast, so an ember button
stands out on any iris or dusk area. Lime (111°) is iris's true complement,
which is why it works as a highlighter. Lime is never used for text.

## Derived sub-colours (colour theory)

| Name | Built from | Theory | Role |
|---|---|---|---|
| `ink` | Charcoal + a trace of dusk's hue | Tinted neutral | Text, borders, greys |
| `jade` | Hue midway between lagoon and lime | Analogous | Success, "passed" |
| `coral` | Ember turned 22° toward red | Neighbouring hue, kept apart from the CTA | Danger, "failed" |
| `orchid` | Iris + 60° toward magenta | Triad with iris and lagoon | Microlensing accents |

Every colour is expanded to an 11-step scale (50 lightest to 950 darkest) in
**OKLCH**, a perceptual colour space: each step looks evenly spaced, and the hue
stays fixed so tints do not drift. Chroma tapers toward white and black, and is
clipped to the sRGB gamut. The brand hex is pinned at its nearest step.

| Scale | 50 | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 950 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `ink` | `#F7F6F9` | `#EDECEF` | `#D9D9DB` | `#BFBFC2` | `#A3A2A7` | `#89888E` | `#706F76` | `#59585F` | `#454545` | `#323137` | `#1E1D23` |
| `dusk` | `#F7F5FF` | `#EDEBFA` | `#D9D7E7` | `#BFBCD0` | `#A39FB9` | `#8984A4` | `#716A91` | `#5A527D` | `#453C67` | `#332A52` | `#1F1738` |
| `iris` | `#F5F6FF` | `#E9EBFF` | `#D2D5FF` | `#B4B8FF` | `#9495FF` | `#7A76F2` | `#6D67E4` | `#4E46B2` | `#3B348B` | `#2A2569` | `#191545` |
| `lagoon` | `#E2FDFF` | `#BBFAFF` | `#88ECF4` | `#5FD4DC` | `#46C2CB` | `#289AA2` | `#267E84` | `#256469` | `#1F4B4F` | `#15383A` | `#072325` |
| `lime` | `#F9FEA8` | `#F2F7A1` | `#DBE08E` | `#C1C57F` | `#A4A872` | `#8A8D65` | `#717356` | `#5A5C45` | `#444533` | `#323322` | `#1E1F10` |
| `ember` | `#FFF4F0` | `#FFE7DE` | `#FFCCB9` | `#FFA683` | `#FF6000` | `#E35400` | `#BB4400` | `#973500` | `#742801` | `#561D02` | `#380E00` |
| `apricot` | `#FFF5ED` | `#FFE8D7` | `#FFCEA9` | `#FFA559` | `#E08C44` | `#BC763B` | `#976236` | `#774F2F` | `#5A3C25` | `#432C19` | `#2C1909` |
| `cream` | `#FFF5E9` | `#FFE6C7` | `#EDD5B7` | `#D1BCA1` | `#B1A08C` | `#938778` | `#786F63` | `#5F5850` | `#48433C` | `#36312A` | `#221D18` |
| `jade` | `#E3FFED` | `#C3FDD8` | `#A5ECC0` | `#7AD6A0` | `#47BD7E` | `#07A362` | `#00864F` | `#006B3E` | `#00522E` | `#003D21` | `#002612` |
| `coral` | `#FFF4F3` | `#FFE6E5` | `#FFCAC9` | `#FFA2A2` | `#FF6C74` | `#E84555` | `#C9263E` | `#A5142E` | `#7F0C21` | `#600817` | `#3F020B` |
| `orchid` | `#FFF3FA` | `#FFE4F4` | `#FFC5E9` | `#FD99DA` | `#E972C2` | `#D050AA` | `#B2368F` | `#922574` | `#701A59` | `#541242` | `#37072A` |

## Roles

Use role names in new components (`bg-surface`, `text-muted`, `bg-primary`
...); they switch with dark mode.

| Role | Light | Dark |
|---|---|---|
| `bg` | `#FFF5E9` | `#1F1738` |
| `surface` | `#FFFFFF` | `#332A52` |
| `surface-muted` | `#F7F6F9` | `#453C67` |
| `border` | `#D9D9DB` | `#5A527D` |
| `text` | `#454545` | `#FFE6C7` |
| `text-muted` | `#706F76` | `#BFBFC2` |
| `heading` | `#453C67` | `#FFE6C7` |
| `primary` | `#4E46B2` | `#9495FF` |
| `primary-text` | `#FFFFFF` | `#1F1738` |
| `accent` | `#FF6000` | `#FFA559` |
| `accent-text` | `#1E1D23` | `#1F1738` |
| `link` | `#4E46B2` | `#5FD4DC` |
| `highlight` | `#F2F7A1` | `#F2F7A1` |
| `focus` | `#46C2CB` | `#46C2CB` |

## Dark mode

Class-based (`<html class="dark">`). It follows the system setting until the
user picks a theme with the header toggle; the choice is kept in
`localStorage` as `vetstar-theme`.

Surfaces and text use separate variable sets (`--ink-700` and `--fg-ink-700`):

- **Surfaces** (background, border, ring): pale tints 50-300 swap to deep
  950-700; 400-950 stay. A pale info box turns deep, while a dark button
  (`bg-blue-700`, white text) is unchanged.
- **Text**: dark shades 600-950 swap to light 400-50; 50-500 stay. Body text
  on a card turns light, while light text on a dark header stays light.

So v1 components adapt without edits. `bg-white` cards take the dark
surface colour through one override in `index.css`.

## v1 class names

v1 used stock Tailwind colours. They are re-pointed in `tailwind.config.js`:
slate/gray → `ink`; blue/indigo/violet → `iris`; purple/fuchsia → `orchid`;
sky/cyan/teal → `lagoon`; emerald/green → `jade`; red/rose → `coral`;
amber/yellow → `apricot`; orange → `ember`. New code should use the brand
or role names directly.

## Contrast (WCAG 2.2)

All text pairs meet AA (4.5:1). Checked by `palette.py`:

| Mode | Pair | Ratio |
|---|---|---|
| light | text on bg | 8.9:1 |
| light | text on surface | 9.59:1 |
| light | text-muted on surface | 4.97:1 |
| light | heading on bg | 9.3:1 |
| light | primary-text on primary | 7.39:1 |
| light | accent-text on accent | 5.52:1 |
| light | link on surface | 7.39:1 |
| dark | text on bg | 14.05:1 |
| dark | text on surface | 10.91:1 |
| dark | text-muted on surface | 7.18:1 |
| dark | heading on bg | 14.05:1 |
| dark | primary-text on primary | 6.46:1 |
| dark | accent-text on accent | 8.71:1 |
| dark | link on surface | 7.49:1 |

White on the brand iris `#6D67E4` is only 4.46:1, so buttons use iris-700
and `#6D67E4` is kept for icons, charts and borders.

## Components

- **Card**: `.card`, surface background, `rounded-card` (14 px), soft
  `shadow-card`, 1 px `line` border.
- **Buttons**: `.btn-accent` (ember) for the one main action on a screen;
  `.btn-primary` (iris) for the rest.
- **Chips**: `.chip` with jade (passed), coral (failed), apricot (warning),
  ink (not applicable).
- **Verdicts**: planet candidate = jade, false alarm / ambiguous = apricot,
  false positive = coral. Always pair colour with a word and an icon; never
  colour alone.
- **Charts**: data in iris, model in ember, bins in dusk/cream (by mode),
  in-transit shading in lime at 30 % opacity.
- **Motion**: transitions 150-200 ms; all motion is off under
  `prefers-reduced-motion`.

"""Generate vetstar v2's colour scales from the eight brand colours.

Run: python docs/design/palette.py  (writes docs/design/palette.json)

Each brand colour becomes a 50-950 scale in OKLCH, a perceptual colour space:
lightness steps look even, and hue stays fixed so a tint never drifts. Chroma
follows a bell curve over lightness (pale tints and deep shades carry less
colour) and is clipped to the sRGB gamut. The brand colour itself is pinned to
the step whose lightness is closest, so the exact hex the owner chose appears
in its scale.

Derived (sub-)colours, by colour theory:
- ink:     tinted neutral. The charcoal #454545 with a trace of dusk's hue,
           so greys sit with the indigo family instead of looking dead.
- jade:    success. Between lagoon and lime on the hue wheel (analogous),
           so "good" reads as part of the cool family.
- coral:   danger. Ember turned toward red; kept apart from ember so a
           warning button is never confused with the main call to action.
- orchid:  the third leg of a triad with iris and lagoon, for the
           microlensing pages (it replaces the old fuchsia).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

BRAND = {
    # warm set
    "charcoal": "#454545", "ember": "#FF6000", "apricot": "#FFA559", "cream": "#FFE6C7",
    # cool set
    "dusk": "#453C67", "iris": "#6D67E4", "lagoon": "#46C2CB", "lime": "#F2F7A1",
}
STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]
L_FOR_STEP = dict(zip(STEPS, [0.975, 0.945, 0.885, 0.805, 0.715, 0.630,
                              0.545, 0.465, 0.385, 0.315, 0.235]))


# --- sRGB <-> OKLab <-> OKLCH (Björn Ottosson's OKLab) ----------------------
def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_oklch(h):
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = (x ** (1 / 3) for x in (l, m, s))
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def oklch_to_rgb(L, C, H):
    a, b = C * math.cos(math.radians(H)), C * math.sin(math.radians(H))
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return r, g, bl


def in_gamut(L, C, H):
    return all(-1e-4 <= c <= 1 + 1e-4 for c in oklch_to_rgb(L, C, H))


def to_hex(L, C, H):
    lo, hi = 0.0, C  # largest chroma that fits sRGB
    if not in_gamut(L, C, H):
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if in_gamut(L, mid, H) else (lo, mid)
        C = lo
    return "#" + "".join(f"{round(min(max(_gam(max(c, 0)), 0), 1) * 255):02X}"
                         for c in oklch_to_rgb(L, C, H))


def scale(L0, C0, H, pin_hex=None):
    """50-950 scale at hue H. Peak chroma C0 near the base lightness L0."""
    out = {}
    for step, L in L_FOR_STEP.items():
        # Bell-shaped chroma: full at L0, tapering toward white and black.
        w = math.exp(-((L - L0) / 0.32) ** 2)
        out[str(step)] = to_hex(L, C0 * (0.25 + 0.75 * w), H)
    if pin_hex:
        nearest = min(STEPS, key=lambda s: abs(L_FOR_STEP[s] - L0))
        out[str(nearest)] = pin_hex.upper()
    return out


def luminance(h):
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def build():
    lch = {k: hex_to_oklch(v) for k, v in BRAND.items()}
    scales = {}
    for name in ("ember", "apricot", "dusk", "iris", "lagoon", "lime", "cream"):
        L, C, H = lch[name]
        scales[name] = scale(L, max(C, 0.02), H, BRAND[name])

    dusk_h = lch["dusk"][2]
    # ink: tinted neutral from charcoal, chroma 0.012 at dusk's hue.
    scales["ink"] = scale(lch["charcoal"][0], 0.012, dusk_h)
    scales["ink"]["800"] = BRAND["charcoal"]  # body text colour, exact brand hex

    lag_h, lime_h = lch["lagoon"][2], lch["lime"][2]
    jade_h = (lag_h + lime_h) / 2  # analogous midpoint, cool green
    scales["jade"] = scale(0.62, 0.15, jade_h)

    ember_h = lch["ember"][2]
    scales["coral"] = scale(0.60, 0.20, (ember_h - 22) % 360)  # toward red

    iris_h = lch["iris"][2]
    scales["orchid"] = scale(0.60, 0.19, (iris_h + 60) % 360)  # iris -> magenta side

    roles = {
        "light": {
            "bg": scales["cream"]["50"], "surface": "#FFFFFF",
            "surface-muted": scales["ink"]["50"], "border": scales["ink"]["200"],
            "text": BRAND["charcoal"], "text-muted": scales["ink"]["600"],
            "heading": BRAND["dusk"], "primary": scales["iris"]["700"],
            "primary-text": "#FFFFFF", "accent": BRAND["ember"],
            "accent-text": scales["ink"]["950"], "link": scales["iris"]["700"],
            "highlight": BRAND["lime"], "focus": BRAND["lagoon"],
        },
        "dark": {
            "bg": scales["dusk"]["950"], "surface": scales["dusk"]["900"],
            "surface-muted": scales["dusk"]["800"], "border": scales["dusk"]["700"],
            "text": scales["cream"]["100"], "text-muted": scales["ink"]["300"],
            "heading": BRAND["cream"], "primary": scales["iris"]["400"],
            "primary-text": scales["dusk"]["950"], "accent": BRAND["apricot"],
            "accent-text": scales["dusk"]["950"], "link": scales["lagoon"]["300"],
            "highlight": BRAND["lime"], "focus": BRAND["lagoon"],
        },
    }
    checks = []
    for mode, r in roles.items():
        for fg, bg in (("text", "bg"), ("text", "surface"), ("text-muted", "surface"),
                       ("heading", "bg"), ("primary-text", "primary"),
                       ("accent-text", "accent"), ("link", "surface")):
            checks.append({"mode": mode, "fg": fg, "bg": bg,
                           "ratio": round(contrast(r[fg], r[bg]), 2)})
    return {"brand": BRAND, "scales": scales, "roles": roles, "contrast": checks}


if __name__ == "__main__":
    data = build()
    out = Path(__file__).with_name("palette.json")
    out.write_text(json.dumps(data, indent=2))
    for name, s in data["scales"].items():
        print(f"{name:8s}", " ".join(s[str(k)] for k in STEPS))
    for c in data["contrast"]:
        flag = "OK " if c["ratio"] >= 4.5 else ("AA-large" if c["ratio"] >= 3 else "FAIL")
        print(f"{c['mode']:5s} {c['fg']:>13s} on {c['bg']:<13s} {c['ratio']:5.2f} {flag}")


# --- CSS variables for the frontend -----------------------------------------
# Two variable sets, so dark mode can treat surfaces and text differently.
# Surfaces (bg, border, ring): pale tints 50-300 swap to deep 950-700, and
# 400-950 stay, so a dark button (bg-700, white text) is unchanged.
# Text: dark shades 600-950 swap to light 400-50, and 50-500 stay, so body
# text on a card turns light while light text on a dark header stays light.
DARK_SURFACE = {50: 950, 100: 900, 200: 800, 300: 700}
DARK_TEXT = {600: 400, 700: 300, 800: 200, 900: 100, 950: 50}


def _channels(h):
    return " ".join(str(int(h[i:i + 2], 16)) for i in (1, 3, 5))


def write_css(data, path):
    light, dark = [], []
    for name, s in data["scales"].items():
        for step in STEPS:
            light.append(f"  --{name}-{step}: {_channels(s[str(step)])};")
            light.append(f"  --fg-{name}-{step}: {_channels(s[str(step)])};")
            dark.append(f"  --{name}-{step}: {_channels(s[str(DARK_SURFACE.get(step, step))])};")
            dark.append(f"  --fg-{name}-{step}: {_channels(s[str(DARK_TEXT.get(step, step))])};")
    for mode, block in (("light", light), ("dark", dark)):
        for role, h in data["roles"][mode].items():
            block.append(f"  --role-{role}: {_channels(h)};")
    nl = "\n"
    css = ("/* Generated by docs/design/palette.py. Do not edit by hand. */" + nl
           + ":root {" + nl + nl.join(light) + nl + "}" + nl + nl
           + ".dark {" + nl + nl.join(dark) + nl + "}" + nl)
    Path(path).write_text(css)


if __name__ == "__main__":
    write_css(build(), Path(__file__).resolve().parents[2] / "frontend" / "src" / "theme.css")

"""Colour themes for the poster templates.

What it does
------------
Every poster style has a few *accent* colours (the pink blob of style 1, the red corner bands of style 4, the gold
frame of style 7 ...). `recolor_source()` takes the source code of one template and swaps exactly those accent
colours for the chosen theme. It never touches

    * the white / light page background        * dark text, greys, shadows
    * the anime art (poster, banner, character) * the Netflix logo colours in style 9

so the poster keeps its design - only the coloured shapes, buttons and highlights change.

It runs while the template is loaded (a text replace + exec), so there is NO extra image processing and NO extra
RAM while rendering: safe for the Render free tier.

Colour menu
-----------
Colour button  ->  two buttons: 🌑 Dark colours / 🌸 Soft colours  ->  a 3 x 3 grid of colours each.
A theme belongs to a group by its `group` field ("dark" or "soft"); the grids are built from THEMES automatically.

Add a colour
------------
Add one line to THEMES below, e.g.
    "ocean": Theme("Ocean", "🌊", hue=200, sat=0.85, light=0.60, group="dark", tint=0.5, deep=0.55)
    "mist":  Theme("Mist",  "☁️", hue=210, sat=0.40, lift=0.12, group="soft", tint=0.7)
It shows up in its group's grid (3 per row, so keep 9 / 12 / 15 per group to fill the rows).
"""
from __future__ import annotations

import colorsys
import logging
import re
from typing import NamedTuple

log = logging.getLogger("themes")

DEFAULT = "default"


class Theme(NamedTuple):
    label: str
    emoji: str
    hue: float | None = None      # target hue in degrees (None = grey scale, only used by "black")
    sat: float = 1.0              # saturation multiplier of the strong accent colours
    light: float = 1.0            # lightness multiplier of the strong accents (< 1 = darker)
    group: str = ""               # "dark" | "soft" -> shown in that grid.  "" = an old colour: still works, has no button
    lift: float = 0.0             # soft colours: lightness ADDED to the strong accents (pastel look)
    tint: float = 1.0             # group colours: saturation of the pale tints (big blobs / panels)
    deep: float = 0.0             # dark colours: how much darker the big pastel blobs become (the lightest panels that carry text stay pale)


# order = order of the buttons in the bot.  "default" = the original colours of every style.
THEMES: dict[str, Theme] = {
    # ------------------------------------------------------------------ 🌑 dark colours
    "black":    Theme("Black",     "⚫", group="dark"),
    "navy":     Theme("Navy",      "🔵", hue=222, sat=0.85, light=0.62, group="dark", tint=0.5, deep=0.55),
    "forest":   Theme("Forest",    "🟢", hue=145, sat=0.70, light=0.58, group="dark", tint=0.4, deep=0.55),
    "brown":    Theme("Brown",     "🟤", hue=25,  sat=0.62, light=0.55, group="dark", tint=0.45, deep=0.55),
    "maroon":   Theme("Maroon",    "🔴", hue=352, sat=0.85, light=0.62, group="dark", tint=0.45, deep=0.55),
    "purple":   Theme("Purple",    "🟣", hue=272, sat=0.80, light=0.62, group="dark", tint=0.5, deep=0.55),
    "deepteal": Theme("Deep Teal", "🩵", hue=182, sat=0.85, light=0.55, group="dark", tint=0.45, deep=0.55),
    "plum":     Theme("Plum",      "🍇", hue=318, sat=0.75, light=0.58, group="dark", tint=0.5, deep=0.55),
    "olive":    Theme("Olive",     "🫒", hue=75,  sat=0.65, light=0.55, group="dark", tint=0.4, deep=0.55),
    # ------------------------------------------------------------------ 🌸 soft colours
    "lavender": Theme("Lavender",  "💜", hue=262, sat=0.50, lift=0.13, group="soft", tint=0.85),
    "lilac":    Theme("Lilac",     "🔮", hue=292, sat=0.50, lift=0.13, group="soft", tint=0.85),
    "softpink": Theme("Soft Pink", "🩷", hue=338, sat=0.60, lift=0.14, group="soft", tint=0.90),
    "peach":    Theme("Peach",     "🍑", hue=18,  sat=0.70, lift=0.14, group="soft", tint=0.90),
    "sand":     Theme("Sand",      "🌾", hue=38,  sat=0.45, lift=0.10, group="soft", tint=0.80),
    "mint":     Theme("Mint",      "🍃", hue=150, sat=0.45, lift=0.10, group="soft", tint=0.80),
    "sage":     Theme("Sage",      "🌿", hue=105, sat=0.28, lift=0.08, group="soft", tint=0.60),
    "softteal": Theme("Soft Teal", "🐬", hue=180, sat=0.45, lift=0.06, group="soft", tint=0.75),
    "sky":      Theme("Sky Blue",  "☁️", hue=205, sat=0.60, lift=0.13, group="soft", tint=0.85),
    # ------------------------------------------------------------------ old colours (no button any more, but a saved choice still renders)
    "blue":   Theme("Blue",   "🔵", hue=218, sat=1.00, light=1.00),
    "red":    Theme("Red",    "🔴", hue=355, sat=1.00, light=1.00),
    "violet": Theme("Violet", "🟣", hue=268, sat=0.95, light=1.00),
    "pink":   Theme("Pink",   "🩷", hue=332, sat=1.00, light=1.03),
    "green":  Theme("Green",  "🟢", hue=150, sat=0.85, light=0.78),
    "orange": Theme("Orange", "🟠", hue=24,  sat=1.00, light=1.00),
    "teal":   Theme("Teal",   "🩵", hue=182, sat=0.90, light=0.80),
}
DEFAULT_LABEL = ("Original", "🎨")
GROUPS: dict[str, tuple[str, str]] = {"dark": ("🌑", "Dark colours"), "soft": ("🌸", "Soft colours")}

DARK_STYLES = {8, 9, 12, 13}      # dark-background styles: "black" turns into silver there (black on black is invisible)

# ---------------------------------------------------------------------------------------------------------------
# TUNING KNOBS for the 🌑 dark colours on the light posters (styles 1-7, 10, 11, 14).
# The dark colours used to be so dark that the small designs drawn on top of the big dark shapes (stars, dots,
# swoosh lines, outlined squares) disappeared. These two numbers set how light the dark shade is - change them
# if you want it a bit darker or a bit lighter, nothing else has to be touched.
#
#   DARK_ACCENT_BOOST  lightness of the dark accents (buttons, circles, bands).  1.0 = the old, very dark look
#                      1.3 = default (dark but the designs show)   1.5 = clearly lighter
#   DARK_BLOB_KEEP     how dark the BIG shapes behind the poster get.  1.0 = old (almost black)
#                      0.62 = default (dark grey / dark colour)   0.4 = lighter    0.0 = stay pale
# ---------------------------------------------------------------------------------------------------------------
DARK_ACCENT_BOOST = 1.30
DARK_BLOB_KEEP = 0.62


def normalize(key: str | None) -> str:
    key = (key or "").strip().lower()
    return key if key in THEMES else DEFAULT


def label(key: str | None) -> str:
    key = normalize(key)
    return DEFAULT_LABEL[0] if key == DEFAULT else THEMES[key].label


def emoji(key: str | None) -> str:
    key = normalize(key)
    return DEFAULT_LABEL[1] if key == DEFAULT else THEMES[key].emoji


def group_of(key: str | None) -> str:
    """'dark' | 'soft' | '' (original colours or an old colour)"""
    key = normalize(key)
    return "" if key == DEFAULT else THEMES[key].group


def keys_in(group: str) -> list[str]:
    """the colours of one grid, in button order"""
    return [k for k, t in THEMES.items() if t.group == group]


# ----------------------------------------------------------------------------------------------- colour maths
RGB = tuple


_WHITE = (255, 255, 255)
SOFT_MIN_CONTRAST = 2.6           # white on a soft accent colour (WCAG-like ratio; the originals are 2.8 - 5.8)


def _smooth(a: float, b: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _to_rgb(r: float, g: float, b: float) -> RGB:
    return tuple(int(round(_clamp01(c) * 255)) for c in (r, g, b))


def _lum(rgb: RGB) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: RGB, b: RGB) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Pale colours that are used for TEXT (not for a shape): they keep the full, dark shade so the words stay readable.
TEXT_TINTS: dict[int, set[RGB]] = {
    1: {(255, 202, 202)},                       # the pale second title word "KISS" of style 1
}


def _blob_keep(style_id: int, rgb: RGB) -> float:
    """1.0 = darken fully (text colours), DARK_BLOB_KEEP = the big shapes (lighter, so the designs on them show)."""
    return 1.0 if tuple(rgb[:3]) in TEXT_TINTS.get(style_id, ()) else DARK_BLOB_KEEP


def recolor_rgb(rgb: RGB, theme_key: str, style_id: int, ref_hue: float | None = None) -> RGB:
    """One colour of one style -> the same colour in the theme (alpha is handled by the caller)."""
    theme_key = normalize(theme_key)
    if theme_key == DEFAULT:
        return tuple(rgb[:3])
    t = THEMES[theme_key]
    r, g, b = (c / 255 for c in rgb[:3])
    h, l, s_hls = colorsys.rgb_to_hls(r, g, b)
    _, s_hsv, _ = colorsys.rgb_to_hsv(r, g, b)

    if t.hue is None:                                           # ---- black
        w = _smooth(0.18, 0.45, s_hsv)                          # 0 = pale tint, 1 = real accent colour
        if style_id in DARK_STYLES:                             # dark poster: accent -> silver, tint -> soft grey
            accent, tint = 0.58 + 0.38 * l, 0.30 + 0.45 * l
        else:                                                   # light poster: accent -> charcoal, tint -> light grey
            accent, tint = (0.10 + 0.22 * l) * DARK_ACCENT_BOOST, l             # charcoal, a little lighter than before
        v = tint + (accent - tint) * w
        if style_id not in DARK_STYLES:                         # the big pastel blobs turn mid-dark grey, the text panels stay pale
            if tuple(rgb[:3]) not in KEEP_PALE.get(style_id, ()):
                v -= 0.52 * _blob_keep(style_id, rgb) * (1.0 - _smooth(0.930, 0.940, l)) * (1.0 - w) * _smooth(0.55, 0.75, l)
        return _to_rgb(v, v, v)

    new_h = t.hue / 360
    if ref_hue is not None:                                     # keep a little of the original gradient / 2nd accent
        d = ((h * 360 - ref_hue + 180) % 360) - 180
        new_h = ((t.hue + max(-40.0, min(40.0, d)) * 0.6) % 360) / 360

    if t.group:                                                 # ---- dark / soft colours
        wa = 1.0 - _smooth(0.70, 0.90, l)                       # 1 = strong accent colour, 0 = pale tint (blob, panel)
        up = t.lift * _smooth(0.20, 0.45, l) * (1.0 - _smooth(0.62, 0.80, l))      # soft: lift the accents, but not the ones that are light already
        if style_id in DARK_STYLES:                             # dark poster: an accent must stay visible on it
            acc_l = l + up if t.group == "soft" else l
        else:                                                   # light poster (white text sits on many accents: never get too pale)
            light = min(1.0, t.light * DARK_ACCENT_BOOST) if t.group == "dark" else t.light     # dark colours: a little lighter
            acc_l = min(l * light + up, max(l, 0.66))
        new_l = l + (acc_l - l) * wa                            # pale tints keep their lightness: text on them stays readable
        if t.group == "soft" and style_id not in DARK_STYLES and wa > 0.5:
            # white button text / icons sit on many accents: a pastel must not get paler than the text can bear
            need = min(SOFT_MIN_CONTRAST, _contrast(_WHITE, tuple(rgb[:3])))
            new_s_acc = s_hls * t.sat
            while new_l > 0.30 and _contrast(_WHITE, _to_rgb(*colorsys.hls_to_rgb(new_h, new_l, new_s_acc))) < need:
                new_l -= 0.01
        if t.deep and style_id not in DARK_STYLES and tuple(rgb[:3]) not in KEEP_PALE.get(style_id, ()):
            # dark colours: the big pastel blobs get darker too, but the lightest panels / chips (they carry text) stay pale
            new_l -= t.deep * _blob_keep(style_id, rgb) * (1.0 - _smooth(0.930, 0.940, l)) * (1.0 - wa) * _smooth(0.55, 0.75, l)
        new_s = s_hls * (t.tint + (t.sat - t.tint) * wa)
        return _to_rgb(*colorsys.hls_to_rgb(new_h, _clamp01(new_l), _clamp01(new_s)))

    return _to_rgb(*colorsys.hls_to_rgb(new_h, _clamp01(l * t.light), _clamp01(s_hls * t.sat)))


# ----------------------------------------------------------------------------------------------- what is recoloured
# style -> accent colours (exact RGB values as written in Templates/posterN.py).  The FIRST one is the style's main
# accent: it sets the hue the others are measured from.  Anything not listed here is never touched.
STYLE_COLORS: dict[int, list[RGB]] = {
    1: [(224, 124, 134), (253, 205, 205), (255, 202, 202), (222, 118, 128), (118, 122, 176), (176, 66, 134), (94, 72, 228)],
    2: [(176, 80, 160), (214, 92, 134), (139, 68, 187), (253, 205, 205), (176, 66, 134), (94, 72, 228)],
    3: [(220, 78, 130), (244, 104, 152), (206, 70, 128), (236, 96, 146), (244, 150, 185), (135, 140, 195),
        (236, 84, 138), (249, 240, 244), (243, 208, 224), (250, 224, 236), (244, 192, 214)],
    4: [(232, 38, 66), (236, 120, 138), (60, 20, 30)],
    5: [(158, 75, 87), (208, 150, 158), (50, 10, 20)],
    6: [(210, 48, 48), (198, 50, 40), (222, 96, 84), (242, 150, 140), (196, 48, 38), (222, 84, 72),
        (120, 40, 30), (40, 20, 20)],
    7: [(238, 160, 0), (250, 205, 90), (250, 190, 20), (210, 136, 0), (176, 108, 0), (60, 40, 0)],
    8: [(214, 40, 57), (255, 112, 124), (120, 40, 45), (178, 46, 58), (206, 64, 74), (150, 40, 52)],
    9: [(176, 32, 44)],                                              # NOT the Netflix logo red / the green match %
    10: [(255, 112, 150), (240, 72, 120), (255, 138, 174), (255, 98, 140), (255, 206, 222), (255, 170, 196),
         (222, 230, 255), (232, 236, 247), (212, 218, 236), (196, 208, 240), (184, 196, 232), (214, 220, 240)],
    11: [(232, 32, 63), (252, 228, 233), (246, 190, 200), (244, 122, 142)],
    12: [(124, 92, 255), (150, 122, 255), (104, 74, 236), (96, 214, 238), (255, 196, 32), (150, 120, 255)],
    13: [(240, 112, 24), (222, 92, 14), (255, 138, 38), (92, 70, 52), (244, 190, 150)],
    14: [(178, 96, 70), (248, 221, 211), (17, 55, 69), (104, 128, 118), (196, 150, 112), (206, 134, 96),
         (166, 88, 64), (252, 238, 231), (238, 208, 196), (98, 118, 108), (253, 243, 238), (30, 60, 72)],
}

# Pale colours that carry TEXT (tag chips ...): the dark colours must not darken these, or the dark text on them gets hard to read.
KEEP_PALE: dict[int, set[RGB]] = {
    10: {(222, 230, 255), (232, 236, 247), (212, 218, 236), (196, 208, 240), (184, 196, 232), (214, 220, 240)},
}

# Text / icons that are drawn ON an accent fill.  They are white (or dark) in the original, which becomes unreadable
# when the fill turns light (black -> silver on a dark style) or dark.  Each entry:
#   (code to find, code to put there, accent the text sits on, ink the original uses)  -  {ink3} / {ink4} = RGB / RGBA
# The patch is only applied when the other ink has the better contrast, so normal colours are left exactly as they were.
_W, _D = (255, 255, 255), (28, 28, 34)
STYLE_INK: dict[int, list[tuple[str, str, RGB, RGB]]] = {
    7: [('cv.text((X + 36, by + 38), button, cv.font("Bold", 22), DARK, anchor="lm")',
         'cv.text((X + 36, by + 38), button, cv.font("Bold", 22), {ink4}, anchor="lm")', (250, 190, 20), (40, 41, 52)),
        ("cv.circle(cx, cy, 23, fill=DARK)", "cv.circle(cx, cy, 23, fill={ink4})", (250, 190, 20), (40, 41, 52))],
    9: [('"PLAY NOW", f_btn, (255, 255, 255), trb)', '"PLAY NOW", f_btn, {ink3}, trb)', (176, 32, 44), _W),
        ("(ccx + S(6.5), ccy)], fill=ACCENT)", "(ccx + S(6.5), ccy)], fill={ink3})", (176, 32, 44), _W)],
    10: [("pill_text, f_btn, TEXT, S(1))", "pill_text, f_btn, {ink3}, S(1))", (255, 138, 174), (14, 18, 58)),
         ("ccx + dia // 2, ccy + dia // 2), fill=TEXT)", "ccx + dia // 2, ccy + dia // 2), fill={ink3})",
          (255, 138, 174), (14, 18, 58))],
    12: [('studio, f_st, (255, 255, 255), S(0.9))', 'studio, f_st, {ink3}, S(0.9))', (124, 92, 255), _W)],
    13: [('a["button_text"], font("Poppins-Bold.ttf", 13), WHITE, gap=1)',
          'a["button_text"], font("Poppins-Bold.ttf", 13), {ink3}, gap=1)', (240, 112, 24), _W),
         ("sp(cy))], fill=WHITE)", "sp(cy))], fill={ink3})", (240, 112, 24), _W)],
}


def _color_re(rgb: RGB) -> re.Pattern:
    r, g, b = rgb
    return re.compile(rf"\(\s*{r}\s*,\s*{g}\s*,\s*{b}\s*(?:,\s*(\d{{1,3}})\s*)?\)")


_RE_CACHE: dict[tuple, re.Pattern] = {}


def recolor_source(style_id: int, code: str, theme_key: str | None) -> str:
    """Source of Templates/poster<style_id>.py -> the same source with the theme's colours. Default = unchanged."""
    theme_key = normalize(theme_key)
    colors = STYLE_COLORS.get(style_id)
    if theme_key == DEFAULT or not colors:
        return code
    ref = colorsys.rgb_to_hls(*(c / 255 for c in colors[0]))[0] * 360

    # 1) contrast patches first (they look for the ORIGINAL code, before any colour is swapped)
    for old, new, fill, orig_ink in STYLE_INK.get(style_id, ()):
        fill2 = recolor_rgb(fill, theme_key, style_id, ref)
        best = max((_W, _D), key=lambda ink: _contrast(ink, fill2))
        if _contrast(best, fill2) > _contrast(orig_ink, fill2) * 1.15 and best != orig_ink:
            if old not in code:
                log.warning("style %s: contrast patch not found: %r", style_id, old[:50])
                continue
            code = code.replace(old, new.format(ink3=repr(best), ink4=repr(best + (255,))))

    # 2) the colours.  All matches are computed on the ORIGINAL text in one pass, so a new colour can never be
    #    recoloured a second time by a later pattern.
    table = {c: recolor_rgb(c, theme_key, style_id, ref) for c in colors}
    pattern = re.compile("|".join(f"(?:{_color_re(c).pattern})" for c in colors))

    def swap(m: re.Match) -> str:
        nums = re.findall(r"\d+", m.group(0))
        rgb = tuple(int(x) for x in nums[:3])
        alpha = nums[3] if len(nums) > 3 else None
        r, g, b = table.get(rgb, rgb)
        return f"({r}, {g}, {b}{', ' + alpha if alpha else ''})"

    return pattern.sub(swap, code)


def map_color(style_id: int, rgb: RGB, theme_key: str | None) -> RGB:
    """For code outside the templates (the channel branding text): recolour rgb if it is one of the style's accents."""
    theme_key = normalize(theme_key)
    colors = STYLE_COLORS.get(style_id)
    if theme_key == DEFAULT or not colors or tuple(rgb) not in colors:
        return tuple(rgb)
    ref = colorsys.rgb_to_hls(*(c / 255 for c in colors[0]))[0] * 360
    return recolor_rgb(tuple(rgb), theme_key, style_id, ref)


def accent_for(style_id: int, rgb: RGB, theme_key: str | None) -> RGB:
    """The poster's main accent colour in the theme (branding text), whatever exact value it had."""
    theme_key = normalize(theme_key)
    colors = STYLE_COLORS.get(style_id)
    if theme_key == DEFAULT or not colors:
        return tuple(rgb)
    ref = colorsys.rgb_to_hls(*(c / 255 for c in colors[0]))[0] * 360
    return recolor_rgb(tuple(rgb), theme_key, style_id, ref)

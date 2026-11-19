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

Add a colour
------------
Add one line to THEMES below, e.g.  "gold": Theme("Gold", "🟡", hue=45, sat=1.0, light=0.85)   and it shows up in
the bot automatically.
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
    sat: float = 1.0              # saturation multiplier
    light: float = 1.0            # lightness multiplier (< 1 = darker)


# order = order of the buttons in the bot.  "default" = the original colours of every style.
THEMES: dict[str, Theme] = {
    "black":  Theme("Black",  "⚫"),
    "blue":   Theme("Blue",   "🔵", hue=218, sat=1.00, light=1.00),
    "red":    Theme("Red",    "🔴", hue=355, sat=1.00, light=1.00),
    "violet": Theme("Violet", "🟣", hue=268, sat=0.95, light=1.00),
    "pink":   Theme("Pink",   "🩷", hue=332, sat=1.00, light=1.03),
    "green":  Theme("Green",  "🟢", hue=150, sat=0.85, light=0.78),
    "orange": Theme("Orange", "🟠", hue=24,  sat=1.00, light=1.00),
    "teal":   Theme("Teal",   "🩵", hue=182, sat=0.90, light=0.80),
}
DEFAULT_LABEL = ("Original", "🎨")

DARK_STYLES = {8, 9, 12, 13}      # dark-background styles: "black" turns into silver there (black on black is invisible)


def normalize(key: str | None) -> str:
    key = (key or "").strip().lower()
    return key if key in THEMES else DEFAULT


def label(key: str | None) -> str:
    key = normalize(key)
    return DEFAULT_LABEL[0] if key == DEFAULT else THEMES[key].label


def emoji(key: str | None) -> str:
    key = normalize(key)
    return DEFAULT_LABEL[1] if key == DEFAULT else THEMES[key].emoji


def next_key(key: str | None) -> str:
    """original -> black -> blue -> ... -> teal -> original"""
    order = [DEFAULT] + list(THEMES)
    return order[(order.index(normalize(key)) + 1) % len(order)]


# ----------------------------------------------------------------------------------------------- colour maths
RGB = tuple


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
            accent, tint = 0.10 + 0.22 * l, l
        v = tint + (accent - tint) * w
        return _to_rgb(v, v, v)

    new_h = t.hue / 360
    if ref_hue is not None:                                     # keep a little of the original gradient / 2nd accent
        d = ((h * 360 - ref_hue + 180) % 360) - 180
        new_h = ((t.hue + max(-40.0, min(40.0, d)) * 0.6) % 360) / 360
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

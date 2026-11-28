"""poster9 - "Glass card" look: black header with a script watermark, banner strip, red nebula synopsis + rating panel,
full-height art on the right and a frosted glass card (title, season, genre chips, watermark) with two thumbnails.

create_poster(raw, poster_bytes, banner_bytes, brand="", thumbs=())  ->  BytesIO (PNG, 2048 x 1152)

 raw           AniList-shaped dict of the title (anime, movie and tv show all use the same keys)
 poster_bytes  the chosen poster (it is the big art on the right)
 banner_bytes  the banner strip on the left (falls back to a crop of the poster)
 brand         the user's watermark: script text top-left AND the small line inside the card
 thumbs        up to two more pictures for the two thumbnails (crops of the poster / banner when there are none)

Everything that is NOT an artwork is drawn here: change a size / a position / a text with the constants right below.
The red of the left panel is the "theme colour": it is a table of colours (GRAD) that themes.py swaps for the colour
the user picked, so every colour of the bot gives the same look.
"""
import html
import math
import os
import random
import re
import urllib.request
from functools import lru_cache
from io import BytesIO

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 2048, 1152
LEFT_W = 708                                           # width of the left column

# ------------------------------------------------------------ texts you may want to change
TAGLINE_LEFT = "A COMPLETE ENTERTAINMENT EXPERIENCE"     # under the script watermark (top-left)
TAGLINE_RIGHT = "YOUR ULTIMATE ANIME DESTINATION"        # under the X mark (top-right)
DEFAULT_BRAND = "Anime Galaxy"                           # used when the user has not set a watermark
TITLE_MAX_W = 350                                        # width of the title in the card (a longer title wraps to line 2)
RATING_NOTE = "The ratings are sourced from a trusted {what} information website called {site}"

# ------------------------------------------------------------ fonts
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONT_FILES = {                                           # style -> (google-fonts folder, file)
    "script": ("yellowtail", "Yellowtail-Regular.ttf"),  # watermark, top-left   (swap for kaushanscript / playball / courgette ...)
    "title": ("anton", "Anton-Regular.ttf"),             # the title in the card
    "bold": ("poppins", "Poppins-Bold.ttf"),
    "regular": ("poppins", "Poppins-Regular.ttf"),
    "light": ("poppins", "Poppins-Light.ttf"),
}
_FALLBACKS = {"script": "DejaVuSerif-Italic.ttf", "title": "DejaVuSans-Bold.ttf", "bold": "DejaVuSans-Bold.ttf"}


def ensure_fonts():
    """Download fonts once (blocking - call with asyncio.to_thread)."""
    os.makedirs(FONT_DIR, exist_ok=True)
    for folder, name in FONT_FILES.values():
        path = os.path.join(FONT_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            continue
        try:
            urllib.request.urlretrieve(f"{_BASE}{folder}/{name}", path)
        except Exception as e:  # noqa
            print(f"[fonts] could not download {name}: {e}")


@lru_cache(maxsize=64)
def get_font(style: str, size: int):
    path = os.path.join(FONT_DIR, FONT_FILES[style][1])
    if not os.path.exists(path):
        ensure_fonts()
    for p in (path, _FALLBACKS.get(style, "DejaVuSans.ttf")):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ------------------------------------------------------------ the theme colour: the red nebula of the left panel
# rows = y inside the panel (503 .. 1152); columns = colour at the left edge / middle / right edge.
# themes.py recolours every colour written here as (r, g, b) - keep that exact form.
GRAD_Y = [512, 545, 620, 700, 740, 800, 830, 900, 940, 1000, 1060, 1100, 1140]
GRAD = [
    [(0, 0, 0), (19, 6, 5), (125, 40, 40)],
    [(0, 0, 0), (31, 10, 9), (133, 43, 42)],
    [(16, 4, 4), (58, 17, 17), (148, 48, 48)],
    [(54, 15, 16), (91, 31, 32), (167, 56, 55)],
    [(74, 23, 22), (108, 34, 35), (181, 64, 64)],
    [(104, 36, 37), (135, 51, 50), (195, 75, 75)],
    [(115, 40, 40), (148, 58, 58), (210, 95, 94)],
    [(162, 86, 86), (189, 109, 109), (225, 112, 113)],
    [(176, 96, 97), (198, 112, 113), (234, 130, 128)],
    [(200, 122, 123), (213, 131, 132), (232, 160, 160)],
    [(221, 147, 148), (229, 148, 149), (234, 175, 176)],
    [(227, 151, 151), (238, 167, 167), (231, 186, 187)],
    [(233, 155, 155), (243, 195, 195), (222, 171, 172)],
]
HEADER_END = (60, 20, 22)                                # right end of the black header's red fade
CLOUD = (255, 214, 214)                                  # soft light clouds near the bottom of the panel

WHITE = (255, 255, 255)
CARD_PANEL = (8, 33, 43)                                 # dark teal panel behind the glass card
NAVY = (11, 36, 51)                                      # text on the white chips
ICON = (24, 52, 68)


# ------------------------------------------------------------ small helpers
def _ss_mask(size, draw_fn, ss=3):
    m = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    draw_fn(ImageDraw.Draw(m), ss)
    return m.resize(size, Image.LANCZOS)


def rr_mask(w, h, r):
    return _ss_mask((w, h), lambda d, s: d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), r * s, fill=255))


def _open(data):
    if not data:
        return None
    try:
        im = Image.open(BytesIO(data))
        im.load()
        return im.convert("RGB")
    except Exception:
        return None


def fit_cover(img, w, h, cx=0.5, cy=0.5):
    """scale + crop so the picture fills w x h (cx / cy = which part to keep, 0..1)"""
    s = max(w / img.width, h / img.height)
    nw, nh = max(w, round(img.width * s)), max(h, round(img.height * s))
    img = img.resize((nw, nh), Image.LANCZOS)
    x = int((nw - w) * cx)
    y = int((nh - h) * cy)
    return img.crop((x, y, x + w, y + h))


def text_w(font, s):
    return font.getlength(s)


def draw_center(d, xy, s, font, fill, anchor="ms"):
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def wrap(text, font, max_w, max_lines):
    """greedy word wrap; text that does not fit in max_lines ends with '…'"""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and text_w(font, t) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    cut = len(lines) > max_lines
    lines = lines[:max_lines]
    if lines:
        last = lines[-1]
        if cut and not last.endswith("…"):
            last = last.rstrip(" ,.;:-") + "…"
        while text_w(font, last) > max_w and len(last) > 1:      # one very long word / the "…" does not fit
            last = last[:-2].rstrip(" ,.;:-") + "…"
        lines[-1] = last
    return lines


# ------------------------------------------------------------ data -> texts
def _score(raw):
    s = raw.get("averageScore") or raw.get("meanScore")
    return f"{s / 10:.1f}" if s else None


def _kind(raw):
    return raw.get("_kind") or "anime"


def _season_line(raw):
    """SEASON 1 / SEASON 2 ... (from the title), MOVIE, OVA, ONA, SPECIAL ..."""
    fmt = (raw.get("format") or "").upper()
    if _kind(raw) == "movie" or fmt == "MOVIE":
        return "MOVIE"
    if fmt in ("OVA", "ONA", "SPECIAL", "MUSIC"):
        return fmt
    t = raw.get("title") or {}
    names = [t.get("english"), t.get("romaji")] + list(raw.get("synonyms") or [])
    for n in filter(None, names):
        m = re.search(r"(?:season|cour)\s*(\d+)|(\d+)(?:st|nd|rd|th)\s*season", n, re.I)
        if m:
            return f"SEASON {m.group(1) or m.group(2)}"
        if re.search(r"final\s+season", n, re.I):
            return "FINAL SEASON"
        m = re.search(r"\s(II|III|IV|V|VI)$", n.strip())
        if m:
            return f"SEASON {['', '', 'II', 'III', 'IV', 'V', 'VI'].index(m.group(1))}"
    return "SEASON 1"


def _season_year(raw):
    season = (raw.get("season") or "").upper()
    year = raw.get("seasonYear") or (raw.get("startDate") or {}).get("year")
    if season and year:
        return f"{season} {year}"
    return str(year) if year else ""


def _synopsis(raw):
    s = html.unescape(raw.get("description") or "")
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\(Source:[^)]*\)|\[Written by[^\]]*\]", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip() or "No synopsis available."


def _title(raw):
    t = raw.get("title") or {}
    return (t.get("english") or t.get("romaji") or t.get("native") or "UNTITLED").strip()


# ------------------------------------------------------------ the left column
def left_panel():
    """black header + the red nebula synopsis panel -> RGB image (LEFT_W x H)"""
    img = np.zeros((H, LEFT_W, 3), np.float32)
    # header: black with a red fade on its right edge
    x = np.arange(LEFT_W, dtype=np.float32)
    t = np.clip((x - 560) / (LEFT_W - 560), 0, 1) ** 1.4
    for c in range(3):
        img[:135, :, c] = (HEADER_END[c] * t)[None, :]
    # panel: colour table (rows y, columns left / middle / right) -> smooth 2D gradient
    y0 = 503
    ys = np.arange(y0, H, dtype=np.float32)
    anchors = np.array(GRAD, np.float32)                     # (rows, 3 columns, rgb)
    rows = np.stack([[np.interp(ys, GRAD_Y, anchors[:, col, ch]) for ch in range(3)] for col in range(3)])  # (col, ch, y)
    u = np.clip(x / (LEFT_W - 1), 0, 1)
    w_mid = np.clip(1 - np.abs(u - 0.5) * 2, 0, 1)
    w_l = np.clip(1 - u * 2, 0, 1)
    w_r = np.clip(u * 2 - 1, 0, 1)
    for ch in range(3):
        img[y0:, :, ch] = (rows[0, ch][:, None] * w_l[None, :] + rows[1, ch][:, None] * w_mid[None, :] +
                           rows[2, ch][:, None] * w_r[None, :])
    panel = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))

    # soft clouds + stars in the lower half
    rnd = random.Random(9)
    cl = np.zeros((H - y0, LEFT_W), np.float32)
    for scale, amp in ((90, 1.0), (40, 0.55), (18, 0.3)):
        small = np.array([[rnd.random() for _ in range(LEFT_W // scale + 2)] for _ in range((H - y0) // scale + 2)], np.float32)
        big = Image.fromarray((small * 255).astype(np.uint8)).resize((LEFT_W + scale * 2, H - y0 + scale * 2), Image.BICUBIC)
        big = big.filter(ImageFilter.GaussianBlur(scale / 3))
        cl += np.asarray(big, np.float32)[:H - y0, :LEFT_W] / 255 * amp
    cl = np.clip((cl / 1.85 - 0.38) * 2.4, 0, 1)
    yy = np.linspace(0, 1, H - y0, dtype=np.float32)[:, None]
    cl *= np.clip((yy - 0.40) / 0.40, 0, 1) * 0.80
    cloud = Image.new("RGB", (LEFT_W, H - y0), CLOUD)
    panel.paste(cloud, (0, y0), Image.fromarray((cl * 255).astype(np.uint8)))

    stars = Image.new("RGBA", (LEFT_W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stars)
    for _ in range(170):
        sx, sy = rnd.randrange(LEFT_W), rnd.randrange(y0 + 20, H)
        a = int(70 + 150 * rnd.random() ** 2)
        r = rnd.choice((0.9, 0.9, 1.2, 1.5, 2.0))
        sd.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(255, 255, 255, a))
    for _ in range(7):                                       # a few bigger glowing stars
        sx, sy = rnd.randrange(30, LEFT_W - 30), rnd.randrange(y0 + 60, H - 60)
        sd.ellipse((sx - 3, sy - 3, sx + 3, sy + 3), fill=(255, 255, 255, 150))
    stars = stars.filter(ImageFilter.GaussianBlur(0.6))
    panel = panel.convert("RGBA")
    panel.alpha_composite(stars)
    return panel.convert("RGB")


def _arrow(d, cx, cy, w, h, direction=1, fill=WHITE):
    """swept-back arrow head, pointing right (direction 1) or left (-1)"""
    pts = [(w / 2, 0), (-w / 2, -h / 2), (-w * 0.28, -h * 0.06), (-w * 0.12, 0), (-w * 0.28, h * 0.06), (-w / 2, h / 2)]
    d.polygon([(cx + direction * px, cy + py) for px, py in pts], fill=fill)


def left_decor(ov):
    """white details of the left column, drawn on a transparent overlay"""
    d = ImageDraw.Draw(ov)
    # the two punched holes on the header's right edge
    for cy in (17, 115):
        d.ellipse((689 - 11, cy - 11, 689 + 11, cy + 11), fill=WHITE + (255,))
    # line + three arrow heads under the synopsis
    line = Image.new("L", (600, 2), 0)
    line.putdata([int(255 * min(1, 0.25 + x / 320)) for x in range(600) for _ in range(2)])
    ov.paste(Image.new("RGBA", (600, 2), WHITE + (255,)), (0, 777), line)
    for i in range(3):
        _arrow(d, 614 + i * 24.5, 778, 24, 22, 1, WHITE + (255,))


def dock(canvas):
    """white rounded dock on the left edge with three small icons"""
    x0, y0, x1, y1 = -30, 852, 58, 1046
    w, h = x1 - x0, y1 - y0
    m = rr_mask(w, h, 26)
    layer = Image.new("RGB", (w, h), (255, 255, 255))
    sh = Image.new("L", (w + 40, h + 40), 0)
    sh.paste(m, (20, 20))
    sh = sh.filter(ImageFilter.GaussianBlur(8)).point(lambda v: int(v * 0.25))
    canvas.paste(Image.new("RGB", sh.size, (0, 0, 0)), (x0 - 20, y0 - 14), sh)
    canvas.paste(layer, (x0, y0), m)
    d = ImageDraw.Draw(canvas)
    cx = 28
    # globe
    cy = 883
    d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=ICON, width=2)
    d.ellipse((cx - 5, cy - 11, cx + 5, cy + 11), outline=ICON, width=1)
    d.line((cx - 11, cy, cx + 11, cy), fill=ICON, width=1)
    d.arc((cx - 11, cy - 6, cx + 11, cy + 6), 0, 180, fill=ICON, width=1)
    d.line((cx - 12, cy + 7, cx + 12, cy - 7), fill=ICON, width=2)          # orbit
    d.line((15, 911, 44, 911), fill=(205, 208, 210), width=2)
    # map pin
    cy = 941
    d.polygon([(cx, cy + 14), (cx - 10, cy - 3), (cx + 10, cy - 3)], fill=ICON)
    d.ellipse((cx - 10, cy - 13, cx + 10, cy + 7), fill=ICON)
    d.ellipse((cx - 4, cy - 7, cx + 4, cy + 1), fill=WHITE)
    d.line((15, 970, 44, 970), fill=(205, 208, 210), width=2)
    # little badge
    d.rounded_rectangle((cx - 9, 988, cx + 9, 1016), 2, fill=(12, 36, 52))
    d.ellipse((cx - 4, 996, cx + 4, 1004), outline=WHITE, width=1)
    d.line((cx - 5, 1010, cx + 5, 1010), fill=(150, 170, 180), width=1)


# ------------------------------------------------------------ the brush X (top-right)
def brush_x(ov, ox=1408, oy=36, size=64):
    ss = 5
    S = size * ss
    m = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(m)
    rnd = random.Random(21)

    def stroke(p0, p1, w0, w1):
        (x0, y0), (x1, y1) = p0, p1
        L = math.hypot(x1 - x0, y1 - y0)
        nx, ny = -(y1 - y0) / L, (x1 - x0) / L
        n = 40
        top, bot = [], []
        for i in range(n + 1):
            t = i / n
            half = (w0 + (w1 - w0) * math.sin(math.pi * t) ** 0.6) / 2 + rnd.uniform(-1, 1) * ss * 0.75
            cx, cy = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            top.append((cx + nx * half, cy + ny * half))
            bot.append((cx - nx * half, cy - ny * half))
        d.polygon(top + bot[::-1], fill=255)
        for end, sgn in ((p0, -1), (p1, 1)):                  # dry-brush bristle streaks at both ends
            for _ in range(11):
                off = rnd.uniform(-0.5, 0.5) * w1 * 1.1
                ln = rnd.uniform(3, 9) * ss
                sx, sy = end[0] + nx * off, end[1] + ny * off
                ex, ey = sx + sgn * (x1 - x0) / L * ln, sy + sgn * (y1 - y0) / L * ln
                d.line((sx, sy, ex, ey), fill=255, width=max(1, int(rnd.uniform(0.7, 1.6) * ss)))

    a = ss
    stroke((7 * a, 7 * a), (57 * a, 58 * a), 22 * a, 17 * a)
    stroke((58 * a, 8 * a), (6 * a, 57 * a), 21 * a, 17 * a)
    for _ in range(16):                                       # splatter
        px = rnd.choice((rnd.uniform(0, 12), rnd.uniform(50, 64))) * a
        py = rnd.choice((rnd.uniform(0, 14), rnd.uniform(48, 64))) * a
        r = rnd.uniform(0.5, 1.6) * a
        d.ellipse((px - r, py - r, px + r, py + r), fill=255)
    m = m.resize((size, size), Image.LANCZOS)
    ov.paste(Image.new("RGBA", (size, size), WHITE + (255,)), (ox, oy), m)


# ------------------------------------------------------------ outline helper (the ghost season text)
def outline_text(ov, xy, s, font, color, alpha):
    m = Image.new("L", ov.size, 0)
    ImageDraw.Draw(m).text(xy, s, font=font, fill=255, anchor="mm")
    edge = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(3)))
    ov.paste(Image.new("RGBA", ov.size, color + (alpha,)), (0, 0), edge)


# ------------------------------------------------------------ main
def create_poster(raw, poster_bytes, banner_bytes=None, brand="", thumbs=()):
    raw = raw or {}
    brand = (brand or "").strip() or DEFAULT_BRAND
    poster = _open(poster_bytes)
    banner = _open(banner_bytes)
    if poster is None and banner is None:
        poster = Image.new("RGB", (800, 1100), (40, 30, 40))
    art = poster or banner
    ban = banner or fit_cover(art, 1010, 368, 0.5, 0.3)

    # ---- 1. canvas: left column + right art
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(left_panel(), (0, 0))
    rw = W - LEFT_W
    bg = fit_cover(art, rw, H, 0.5, 0.33)
    bg = ImageEnhance.Brightness(bg).enhance(0.78)
    bg = ImageEnhance.Color(bg).enhance(0.92)
    shade = np.ones((H, rw), np.float32)
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    xx = np.linspace(0, 1, rw, dtype=np.float32)[None, :]
    shade *= 0.28 + 0.72 * np.clip(yy / 0.30, 0, 1) ** 0.8            # dark at the top
    shade *= 0.55 + 0.45 * np.clip(xx / 0.22, 0, 1)                    # dark next to the left column
    shade *= 1 - 0.30 * np.clip((xx - 0.70) / 0.3, 0, 1)               # slight vignette at the far right
    bg = Image.fromarray(np.clip(np.asarray(bg, np.float32) * shade[:, :, None], 0, 255).astype(np.uint8))
    canvas.paste(bg, (LEFT_W, 0))

    # ---- 2. banner strip (rounded, overlaps the right art)
    bw, bh = 1010, 368
    strip = fit_cover(ban, bw, bh, 0.5, 0.4)
    canvas.paste(strip, (0, 135), rr_mask(bw, bh, 36))

    # ---- 3. glass card + thumbnails
    cx0, cy0, cx1, cy1 = 1190, 660, 1918, 1120
    cw, ch = cx1 - cx0, cy1 - cy0
    region = canvas.crop((cx0, cy0, cx1, cy1)).filter(ImageFilter.GaussianBlur(18))
    region = ImageEnhance.Brightness(region).enhance(0.82)
    px0, px1 = 1335 - cx0, 1855 - cx0                                    # the dark teal panel inside the card
    panel = Image.new("RGB", (px1 - px0, ch), CARD_PANEL)
    region.paste(Image.blend(region.crop((px0, 0, px1, ch)), panel, 0.80), (px0, 0))
    region = Image.blend(region, Image.new("RGB", region.size, WHITE), 0.22)
    canvas.paste(region, (cx0, cy0), rr_mask(cw, ch, 36))
    canvas.paste(Image.new("RGB", (1855 - 1335, H - cy1), CARD_PANEL), (1335, cy1))   # the panel runs to the bottom edge

    d = ImageDraw.Draw(canvas)
    for cy in (697, 1086):                                               # punched holes
        d.ellipse((1875 - 27, cy - 27, 1875 + 27, cy + 27), fill=(235, 235, 235))

    tl = []
    pool = [_open(t) for t in (thumbs or ())]
    pool = [t for t in pool if t is not None]
    fallback = [fit_cover(art, 400, 400, 0.5, 0.18), fit_cover(ban, 400, 400, 0.62, 0.5)]
    for i in range(2):
        tl.append(pool[i] if i < len(pool) else fallback[i])
    for i, ty in enumerate((688, 890)):
        fw, fh = 194, 194
        fm = rr_mask(fw, fh, 30)
        sh = Image.new("L", (fw + 40, fh + 40), 0)
        sh.paste(fm, (20, 20))
        sh = sh.filter(ImageFilter.GaussianBlur(9)).point(lambda v: int(v * 0.35))
        canvas.paste(Image.new("RGB", sh.size, (0, 0, 0)), (1102 - 20, ty - 14), sh)
        base = canvas.crop((1102, ty, 1102 + fw, ty + fh)).filter(ImageFilter.GaussianBlur(6))
        frame = Image.blend(base, Image.new("RGB", (fw, fh), WHITE), 0.42)
        canvas.paste(frame, (1102, ty), fm)
        inner = fit_cover(tl[i], fw - 24, fh - 24, 0.5, 0.35)
        canvas.paste(inner, (1102 + 12, ty + 12), rr_mask(fw - 24, fh - 24, 20))

    canvas = canvas.convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    left_decor(ov)

    # ---- 4. header: script watermark + tagline
    f_script = get_font("script", 44)
    while text_w(f_script, brand) > 430 and f_script.size > 20:
        f_script = get_font("script", f_script.size - 2)
    od.text((63, 77), brand, font=f_script, fill=WHITE + (255,), anchor="ls")
    f15 = get_font("regular", 15)
    od.ellipse((163, 85, 169, 91), fill=WHITE + (255,))
    od.text((180, 88), TAGLINE_LEFT, font=f15, fill=WHITE + (255,), anchor="lm")

    # ---- 5. top-right: brush X, tagline, line
    brush_x(ov)
    for cy in (113, 126):
        od.ellipse((1328 - 3.5, cy - 3.5, 1328 + 3.5, cy + 3.5), fill=WHITE + (255,))
    od.text((1341, 116), TAGLINE_RIGHT, font=f15, fill=WHITE + (255,), anchor="lm")
    od.rounded_rectangle((1328, 134, 1735, 136), 1, fill=WHITE + (255,))

    # ---- 6. season ghost text + arrows + line
    season = _season_year(raw)
    if season:
        f = get_font("light", 26)
        tw = text_w(f, season)
        pw = int(tw + 30)
        pcx = 1481
        pill = rr_mask(pw, 38, 19)
        edge = ImageChops.subtract(pill, pill.filter(ImageFilter.MinFilter(3)))
        ov.paste(Image.new("RGBA", (pw, 38), (125, 125, 125, 215)), (pcx - pw // 2, 583), edge)
        outline_text(ov, (pcx, 602), season, f, (125, 125, 125), 235)
    for i in range(3):
        _arrow(od, 1630 + i * 25, 604, 34, 34, -1, WHITE + (255,))
    od.rounded_rectangle((1717, 603, 1820, 605), 1, fill=WHITE + (255,))

    # ---- 7. synopsis / rating (left column)
    f_h = get_font("bold", 41)
    draw_center(od, (356, 598), "SYNOPSIS", f_h, WHITE + (255,))
    f_s = get_font("regular", 17)
    lines = wrap(_synopsis(raw), f_s, 490, 5)
    for i, ln in enumerate(lines):
        draw_center(od, (354, 632 + round(i * 21.5)), ln, f_s, WHITE + (255,))
    score = _score(raw)
    draw_center(od, (357, 889), f"RATING: {score}/10" if score else "RATING: N/A", f_h, WHITE + (255,))
    kind = _kind(raw)
    what, site = {"movie": ("movie", "TMDB"), "tv": ("tv show", "TMDB")}.get(kind, ("anime", "Anilist"))
    note = wrap(RATING_NOTE.format(what=what, site=site), get_font("light", 19), 400, 2)
    for i, ln in enumerate(note):
        draw_center(od, (358, 961 + i * 23), ln, get_font("light", 19), (255, 255, 255, 205))

    # ---- 8. the card text
    f_t = get_font("title", 54)
    tlines = wrap(_title(raw).upper(), f_t, TITLE_MAX_W, 2)              # too long for two lines -> "…"
    base = 800 if len(tlines) > 1 else 776
    for i, ln in enumerate(tlines):
        draw_center(od, (1604, base - (len(tlines) - 1 - i) * 51), ln, f_t, WHITE + (255,))
    draw_center(od, (1601, 830), _season_line(raw), get_font("light", 25), WHITE + (240,))

    genres = [g.upper() for g in (raw.get("genres") or [])][:5]
    if len(raw.get("genres") or []) > 5:
        genres.append("···")
    chips = []
    for i, g in enumerate(genres):
        f = get_font("bold" if i == 0 else "regular", 19)
        chips.append((g, f, max(88, int(text_w(f, g) + 34)), i % 2 == 0))
    rows = [chips[:3], chips[3:6]] if len(chips) > 3 else [chips]
    for ri, row in enumerate(rows):
        if not row:
            continue
        total = sum(c[2] for c in row) + 8 * (len(row) - 1)
        x = 1590 - total / 2
        top = 839 + ri * 47
        for g, f, w, filled in row:
            box = (round(x), top, round(x) + w, top + 41)
            if filled:
                od.rounded_rectangle(box, 20, fill=(251, 252, 252, 255))
                od.text(((box[0] + box[2]) / 2, top + 21), g, font=f, fill=NAVY + (255,), anchor="mm")
            else:
                od.rounded_rectangle(box, 20, outline=WHITE + (255,), width=2)
                od.text(((box[0] + box[2]) / 2, top + 21), g, font=f, fill=WHITE + (255,), anchor="mm")
            x += w + 8
    od.rounded_rectangle((1535, 979, 1636, 981), 1, fill=WHITE + (255,))
    f_w = get_font("regular", 17)
    wm = brand.upper()
    while text_w(f_w, wm) > 420 and f_w.size > 11:
        f_w = get_font("regular", f_w.size - 1)
    draw_center(od, (1597, 1023), wm, f_w, WHITE + (235,), anchor="mm")

    canvas.alpha_composite(ov)
    canvas = canvas.convert("RGB")
    dock(canvas)

    out = BytesIO()
    canvas.save(out, "PNG", compress_level=1)
    out.seek(0)
    return out

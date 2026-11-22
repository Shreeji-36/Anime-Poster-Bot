from __future__ import annotations

import asyncio
import html
import io
import logging
import os
import random
import re
import time
from collections import defaultdict

from pyrogram import StopPropagation, enums, filters
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import BotCommand, InputMediaPhoto

import chstore
import config
import dataset
import themes
from dataset import BUTTON_PRESETS, POSTERS
from ui import BLUE, GREEN, RED, bq, btn, kb, sc

log = logging.getLogger("cmds")

HTML = enums.ParseMode.HTML
ALLOWED = config.ALLOWED_USERS
N_STYLES = len(POSTERS)
MAIN_CHANNEL_URL = config.MAIN_CHANNEL_URL
SUPPORT_URL = config.SUPPORT_URL
SAMPLES_DIR = config.SAMPLES_DIR
SAMPLE_ANIME_ID = config.SAMPLE_ANIME_ID
MENU_IMAGES = config.MENU_IMAGES

# two different arrow sets so the user can tell the menus apart:
#   /styles viewer  -> ⏪ ⏩        poster message (the png) -> ◀️ ▶️
STYLE_BACK, STYLE_NEXT = "⏪", "⏩"
POSTER_PREV, POSTER_NEXT = "◀️", "▶️"

STATE: dict[int, dict] = defaultdict(dict)
RENDER_TIMEOUT = int(os.environ.get("RENDER_TIMEOUT", "240"))   # seconds; the free 0.1 CPU can be very slow


# ======================================================================
# texts  (every static word goes through sc(); dynamic data is inserted raw)
# ======================================================================
def mention(user) -> str:
    return f'<a href="tg://user?id={user.id}">{html.escape(user.first_name or "there")}</a>'


def start_text(user) -> str:
    return bq(sc("bakkaaa...") + mention(user) + "\n" +
              sc("iam your advance thumbnail creation bot for anime. Iam waiting for you baby ~ alya"))


ABOUT_TEXT = bq(
    sc("🌸 about me") + "\n\n" +
    sc(f"i turn anilist and tmdb data into {N_STYLES} different thumbnails / posters.\n"
       "send me an anime, movie or tv show name, pick the right result and flip through every style.\n"
       "change the button text, the poster / banner or your channel branding, then grab the full quality file.\n\n"
       "data · anilist · tmdb\nlibrary · kurigram")) + "\n" + bq(
    "This product uses the TMDB API but is not endorsed or certified by TMDB.")

HELP_CMDS = [
    ("/start", "wake me up"),
    ("/help", "show this message"),
    ("/poster naruto", "make a poster of any anime"),
    ("/movies inception", "make a poster of any movie"),
    ("/tvshows breaking bad", "make a poster of any tv show"),
    ("/styles", "see every style with a preview"),
    ("/addchnl", "add your channel (i must be full admin there)"),
    ("/mychnls", "your channels"),
    ("/removechnl", "remove a channel"),
    ("/schedules", "your scheduled posts"),
    ("/template", "your caption template ({title}, {score} ...)"),
    ("/timezone", "set your time zone for scheduling"),
]


def help_text() -> str:
    lines = "\n".join(f"<code>{c}</code> ➜ {sc(d)}" for c, d in HELP_CMDS)
    return bq(sc("📖 all commands") + "\n\n" + lines) + "\n" + bq(
        sc("or just send a name and i will search it (anime, movie or tv show - whichever command you used last)."))


def pick_style_text(query: str | None = None) -> str:
    head = sc("🎨 choose a poster style")
    if query:
        head += "\n" + sc("for ") + f"“{html.escape(query)}”"
    lines = "\n".join(f"<b>{s.id}</b> ➜ {sc(s.blurb)}" for s in POSTERS.values())
    return bq(head) + "\n" + bq(lines, expandable=True)


def style_info(n: int) -> str:
    s = POSTERS[n]
    feats = []
    if s.character:
        feats.append("fanart.tv PNG change (◀ ▶), poster if none")
    if not s.character:
        feats.append(f"{dataset.art_kind(s.id)} change (◀ ▶)")
    if s.button:
        feats.append("custom button text")
    feats.append("channel branding")
    return bq(f"🎨 {sc('style')} <b>{n}</b> / {N_STYLES}\n{sc(s.blurb)}\n\n"
              f"{sc('options')} ➜ {sc(', '.join(feats) or 'none')}")


# ======================================================================
# keyboards
# ======================================================================
def start_keyboard():
    return kb([btn("ℹ️ about", "ab", color=BLUE), btn("📖 help", "hp", color=GREEN)])


def help_keyboard(back: bool = False):
    rows = [[btn("📢 main channel", url=MAIN_CHANNEL_URL, color=BLUE),
             btn("💬 support", url=SUPPORT_URL, color=GREEN)]]
    if back:
        rows.append([btn("🔙 back", "home", color=RED)])
    return kb(*rows)


def about_keyboard():
    return kb([btn("🔙 back", "home", color=RED)])


def styles_keyboard(current: int | None = None, back: bool = False):
    rows, row = [], []
    for sid in POSTERS:
        row.append(btn(f"{'✅ ' if sid == current else ''}{sid}", f"st:{sid}", raw=True,
                       color=GREEN if sid == current else BLUE))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    last = [btn("👀 previews", f"sv:{current or 1}", color=GREEN)]
    if back:
        last.insert(0, btn("🔙 back", "bk", color=RED))
    rows.append(last)
    return kb(*rows)


def viewer_keyboard(n: int):
    return kb(
        [btn(f"{STYLE_BACK} back", f"sv:{(n - 2) % N_STYLES + 1}", color=BLUE),
         btn(f"{n}/{N_STYLES}", "noop", raw=True),
         btn(f"next {STYLE_NEXT}", f"sv:{n % N_STYLES + 1}", color=BLUE)],
        [btn("✅ use this style", f"st:{n}", color=GREEN)],
    )


def result_keyboard(style: int):
    return kb(
        [btn(POSTER_PREV, "pv", raw=True, color=BLUE), btn(f"🎨 style {style}/{N_STYLES}", "sl", color=GREEN),
         btn(POSTER_NEXT, "nx", raw=True, color=BLUE)],
        [btn("⚙️ options", "op", color=BLUE), btn("🔄 redo", "rg", color=GREEN)],
        [btn("📁 full quality file", "fl", color=BLUE), btn("🔍 new title", "na", color=GREEN)],
        [btn("📣 post to channel", "ch_post", color=GREEN), btn("⏰ schedule", "ap_new", color=BLUE)],
    )


def options_keyboard(style: int, opts: dict, anime=None):
    s = POSTERS[style]
    rows = []
    if s.character and anime is not None and anime.characters:
        n, _ = dataset.char_state(anime, opts.get("char", 0))
        idx = opts.get("char", 0)
        shown = f"🧍 {idx + 1}/{n}" if 0 <= idx < n else sc(f"🃏 cover card ({n} art)")
        rows.append([btn(POSTER_PREV, "ocp", raw=True, color=BLUE), btn(shown, "occ", raw=True, color=GREEN),
                     btn(POSTER_NEXT, "ocn", raw=True, color=BLUE)])
    if not s.character and anime is not None:                       # poster / banner styles: back / next
        n, label, i, listed = dataset.art_state(anime, style, opts.get("pic"))
        shown = (f"🖼 {i + 1}/{n} · {label}" if listed else f"🖼 {label} · ▶ more")[:40]
        rows.append([btn(POSTER_PREV, "ipp", raw=True, color=BLUE), btn(shown, "noop", raw=True, color=GREEN),
                     btn(POSTER_NEXT, "ipn", raw=True, color=BLUE)])
    if s.button:
        rows.append([btn(sc("🔘 button: ") + opts["button"], "obt", raw=True, color=BLUE),
                     btn("✏️ custom", "obc", color=GREEN)])
    theme = themes.normalize(opts.get("theme"))                     # accent colours of the poster (not the white page)
    rows.append([btn(sc("🎨 colour: ") + f"{themes.emoji(theme)} {themes.label(theme)}", "thm", raw=True, color=BLUE)])
    brand = opts.get("chname") or ""                                # the user's channel branding: on EVERY poster
    rows.append([btn(sc("🏷 branding: ") + (brand[:22] if brand else sc("not set")), "ch_brand", raw=True, color=BLUE),
                 btn("✏️ edit", "ch_brand", color=GREEN)])
    rows.append([btn("🔙 back", "ob", color=RED)])
    return kb(*rows)


def theme_menu_keyboard(current: str | None):
    """step 1 of the colour change: 🌑 dark colours / 🌸 soft colours  (+ back to the original colours)"""
    current = themes.normalize(current)
    cur_group = themes.group_of(current)
    rows = [[btn(("✅ " if cur_group == g else "") + f"{icon} {name}", f"thg:{g}", raw=True,
                 color=GREEN if cur_group == g else BLUE) for g, (icon, name) in themes.GROUPS.items()],
            [btn(("✅ " if current == themes.DEFAULT else "") + f"{themes.emoji(themes.DEFAULT)} {themes.label(themes.DEFAULT)}",
                 f"th:{themes.DEFAULT}", raw=True, color=GREEN if current == themes.DEFAULT else BLUE)],
            [btn("🔙 back", "op", color=RED)]]
    return kb(*rows)


def theme_keyboard(current: str | None, group: str):
    """step 2: the colours of one group as a grid, 3 per row; the active one carries a tick"""
    current = themes.normalize(current)
    keys = themes.keys_in(group)
    cells = [btn(("✅ " if k == current else "") + f"{themes.emoji(k)} {themes.label(k)}", f"th:{k}", raw=True,
                 color=GREEN if k == current else BLUE) for k in keys]
    rows = [cells[i:i + 3] for i in range(0, len(cells), 3)]
    rows.append([btn("🔙 back", "thm", color=RED), btn("⚙️ options", "op", color=BLUE)])
    return kb(*rows)


def cancel_keyboard():
    return kb([btn("🎨 styles", "sl0", color=BLUE), btn("✖️ cancel", "cx", color=RED)])


# ======================================================================
# state / message helpers
# ======================================================================
def _ud(user_id: int) -> dict:
    ud = STATE[user_id]
    ud.setdefault("opts", dict(button=BUTTON_PRESETS[0], pic=0))
    return ud


_KIND_CODE = {"anime": "a", "movie": "m", "tv": "t"}               # used inside the result buttons: pk:m:550
_CODE_KIND = {v: k for k, v in _KIND_CODE.items()}


def _kind(ud: dict) -> str:
    return ud.get("kind") or "anime"


def _word(ud: dict) -> str:
    """'anime' | 'movie' | 'tv show' - the kind of title the user is looking for right now"""
    return dataset.KIND_WORD[_kind(ud)]


def _caption(ud: dict, rendered) -> str:
    a = ud["anime"]
    cap = f"🎬 <b>{html.escape(a.title)}</b>\n" + sc(f"🎨 style {ud['style']} · {rendered.size[0]}×{rendered.size[1]}")
    if POSTERS[ud["style"]].character and a.characters:
        n, label = dataset.char_state(a, ud["opts"].get("char", 0))
        cap += "\n" + sc("🧍 art: ") + html.escape(label) + "\n" + sc("⚙ options → ◀ ▶ to change character")
        if dataset.kind_of(a) == "anime" and a.main_name and not label.startswith("main character"):
            cap += "\n" + sc("⭐ main character: ") + html.escape(a.main_name)
    elif not POSTERS[ud["style"]].character:
        kind = dataset.art_kind(ud["style"])
        _, label, _, _ = dataset.art_state(a, ud["style"], ud["opts"].get("pic"))
        cap += "\n" + sc(f"🖼 {kind}: ") + html.escape(label) + "\n" + sc(f"⚙ options → ◀ ▶ to change the {kind}")
    theme = themes.normalize(ud["opts"].get("theme"))
    if theme != themes.DEFAULT:
        cap += "\n" + sc("🎨 colour: ") + f"{themes.emoji(theme)} {themes.label(theme)}"
    if ud["opts"].get("chname"):
        cap += "\n" + sc("🏷 branding: ") + html.escape(ud["opts"]["chname"])
    return bq(cap)


async def _safe_delete(client, chat_id, message_id):
    try:
        await client.delete_messages(chat_id, message_id)
    except RPCError:
        pass


async def _edit(message, text: str, markup=None):
    """Edit a message - caption if it is a photo, text otherwise."""
    try:
        if message.photo:
            await message.edit_caption(text, reply_markup=markup)
        else:
            await message.edit_text(text, reply_markup=markup)
    except MessageNotModified:
        pass


def _bio(data: bytes, name: str) -> io.BytesIO:
    b = io.BytesIO(data)
    b.name = name
    return b


# ======================================================================
# rendering + showing the poster
# ======================================================================
async def _show_photo(client, ud, chat_id, rendered, caption, markup, edit_id=None, delete_id=None):
    """Edit the existing poster message in place when possible, else send a new photo."""
    if edit_id:
        try:
            await client.edit_message_media(
                chat_id, edit_id, InputMediaPhoto(_bio(rendered.jpg, "poster.jpg"), caption=caption, parse_mode=HTML),
                reply_markup=markup)
            ud["pmsg"] = edit_id
            return
        except MessageNotModified:
            ud["pmsg"] = edit_id
            return
        except RPCError as e:
            log.info("edit failed (%s) -> sending a new photo", e)
    m = await client.send_photo(chat_id, _bio(rendered.jpg, "poster.jpg"), caption=caption, reply_markup=markup)
    ud["pmsg"] = m.id
    if delete_id and delete_id != edit_id:
        await _safe_delete(client, chat_id, delete_id)
    if edit_id and edit_id != m.id:
        await _safe_delete(client, chat_id, edit_id)


async def generate(client, user_id: int, chat_id: int, *, panel: bool = False, source_msg=None):
    """Render with the current style/options and show it.
    source_msg = message the button was pressed on (photo -> edited, text -> replaced)."""
    ud = _ud(user_id)
    if ud.get("busy"):
        return
    ud["busy"] = True
    try:
        try:
            await client.send_chat_action(chat_id, enums.ChatAction.UPLOAD_PHOTO)
        except RPCError:
            pass
        try:                                                                 # the user's channel branding goes on every poster
            ud["opts"]["chname"] = await asyncio.wait_for(chstore.get_brand(user_id), timeout=8)
        except Exception:                                                    # database down / bad MONGO_URI must NOT block posters
            log.exception("branding lookup failed (is MONGO_URI correct?) -> making the poster without branding")
            ud["opts"]["chname"] = ""
        if "theme" not in ud["opts"]:                                        # first poster of this session: the colour used last time
            try:
                ud["opts"]["theme"] = themes.normalize(await asyncio.wait_for(chstore.get_theme(user_id), timeout=8))
            except Exception:
                ud["opts"]["theme"] = themes.DEFAULT
        t0 = time.monotonic()
        log.info("generate: start style=%s anime=%r", ud.get("style"), ud["anime"].title)
        try:
            rendered = await asyncio.wait_for(dataset.make_poster(ud["style"], ud["anime"], ud["opts"]),
                                              timeout=RENDER_TIMEOUT)
        except asyncio.TimeoutError:
            log.error("generate: render timed out after %s s (style %s)", RENDER_TIMEOUT, ud.get("style"))
            await client.send_message(
                chat_id, bq(sc("⏱ that took too long. try a lighter style (3, 6, 7, 10) or another anime.")),
                reply_markup=kb([btn("🎨 styles", "sl0", color=BLUE), btn("🔍 new title", "na", color=GREEN)]))
            return
        except dataset.NoArt as e:
            kind = e.args[0] if e.args else "banner"
            await client.send_message(
                chat_id, bq(sc(f"😕 no {kind} found for this title, and style {ud.get('style')} needs one. "
                               f"try another style.")),
                reply_markup=kb([btn("🎨 styles", "sl0", color=BLUE), btn("🔍 new title", "na", color=GREEN)]))
            return
        except Exception:
            log.exception("render failed (style %s)", ud.get("style"))
            await client.send_message(
                chat_id, bq(sc("😕 i couldn't render that poster. try another style or another anime.")),
                reply_markup=kb([btn("🎨 styles", "sl0", color=BLUE), btn("🔍 new title", "na", color=GREEN)]))
            return
        log.info("generate: rendered in %.1fs (jpg %d KB, png %d KB, %s)", time.monotonic() - t0,
                 len(rendered.jpg) // 1024, len(rendered.png) // 1024, rendered.size)
        ud["last"] = rendered
        markup = options_keyboard(ud["style"], ud["opts"], ud["anime"]) if panel else result_keyboard(ud["style"])

        edit_id = delete_id = None
        if source_msg is not None and source_msg.photo:
            edit_id = source_msg.id
        elif source_msg is not None:
            delete_id = source_msg.id
        elif ud.get("pmsg"):
            edit_id = ud["pmsg"]
        try:
            await _show_photo(client, ud, chat_id, rendered, _caption(ud, rendered), markup, edit_id, delete_id)
            log.info("generate: photo sent, total %.1fs", time.monotonic() - t0)
        except Exception:
            log.exception("generate: sending the poster failed")
            await client.send_message(
                chat_id, bq(sc("⚠️ the poster was made but telegram did not accept it. tap 🔄 redo or try another style.")),
                reply_markup=kb([btn("🎨 styles", "sl0", color=BLUE), btn("🔍 new title", "na", color=GREEN)]))
    finally:
        ud["busy"] = False


# ======================================================================
# style previews (/styles)
# ======================================================================
_thumb_lock = asyncio.Lock()
_sample_anime = None
_FILE_IDS: dict[int, str] = {}          # style -> telegram file_id (after the first upload)


def _placeholder_thumb(n: int) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1280, 720), (255, 235, 242))
    d = ImageDraw.Draw(img)
    d.ellipse((-300, 60, 540, 900), fill=(255, 112, 150))
    d.text((640, 340), f"STYLE {n}", fill=(14, 18, 58))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


async def _thumb(n: int) -> bytes:
    """Preview image of a style: samples/style_N.jpg, else rendered once from SAMPLE_ANIME_ID and cached."""
    global _sample_anime
    path = SAMPLES_DIR / f"style_{n}.jpg"
    if path.exists():
        return path.read_bytes()
    async with _thumb_lock:
        if path.exists():
            return path.read_bytes()
        try:
            if _sample_anime is None:
                _sample_anime = await dataset.load_anime(SAMPLE_ANIME_ID)
            r = await dataset.make_poster(n, _sample_anime, {})
            from PIL import Image
            im = Image.open(io.BytesIO(r.jpg)).convert("RGB")
            im.thumbnail((1280, 1280))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=88)
            SAMPLES_DIR.mkdir(exist_ok=True)
            path.write_bytes(out.getvalue())
            return out.getvalue()
        except Exception:
            log.exception("could not build the preview of style %s", n)
            return _placeholder_thumb(n)


async def _viewer_media(n: int):
    """file_id when we already uploaded this preview, else the bytes."""
    return _FILE_IDS.get(n) or _bio(await _thumb(n), f"style_{n}.jpg")


async def open_viewer(client, chat_id: int, n: int, message=None):
    """message=None -> send a new preview message, else edit the given one."""
    media = await _viewer_media(n)
    cap, markup = style_info(n), viewer_keyboard(n)
    if message is None:
        m = await client.send_photo(chat_id, media, caption=cap, reply_markup=markup)
    else:
        try:
            m = await message.edit_media(InputMediaPhoto(media, caption=cap, parse_mode=HTML), reply_markup=markup)
        except MessageNotModified:
            return
    if m is not None and getattr(m, "photo", None) and n not in _FILE_IDS:
        _FILE_IDS[n] = m.photo.file_id


# ======================================================================
# access control
# ======================================================================
async def guard(client, update):
    if not ALLOWED:
        return
    user = update.from_user
    if user and user.id in ALLOWED:
        return
    if hasattr(update, "data"):                           # callback query
        await update.answer(sc("not allowed."), show_alert=True)
    else:
        await update.reply_text(bq(sc("⛔ this bot is private.")))
    raise StopPropagation


# ======================================================================
# commands
# ======================================================================
async def _reply_with_image(message, text: str, markup):
    """Photo (random MENU_IMAGES) with the text as caption; plain text if the image can't be loaded.
    About / Help / Back buttons later edit this caption, so the picture stays the same."""
    try:
        await message.reply_photo(random.choice(MENU_IMAGES), caption=text, reply_markup=markup)
    except RPCError as e:
        log.warning("menu image failed (%s) -> text only", e)
        await message.reply_text(text, reply_markup=markup)


async def cmd_start(client, message):
    ud = _ud(message.from_user.id)
    ud["await"] = None
    ud["kind"] = "anime"
    ud.pop("pending", None)
    await _reply_with_image(message, start_text(message.from_user), start_keyboard())


async def cmd_help(client, message):
    await _reply_with_image(message, help_text(), help_keyboard())


async def cmd_styles(client, message):
    ud = _ud(message.from_user.id)
    await open_viewer(client, message.chat.id, ud.get("style") or 1)


async def _title_command(client, message, kind: str, example: str):
    """/poster  /movies  /tvshows : switch to that kind of title, then search the name (or ask for it)."""
    uid = message.from_user.id
    ud = _ud(uid)
    ud["kind"] = kind
    query = " ".join(message.command[1:]).strip()
    if not query:
        ud["await"] = "name"
        ud.pop("pending", None)
        await message.reply_text(bq(sc("usage: ") + f"<code>{example}</code>\n" +
                                    sc(f"or just send me the {_word(ud)} name now.")),
                                 reply_markup=cancel_keyboard())
        return
    await _handle_name(client, uid, message.chat.id, query[:100])


async def cmd_poster(client, message):
    await _title_command(client, message, "anime", "/poster anime name")


async def cmd_movies(client, message):
    await _title_command(client, message, "movie", "/movies movie name")


async def cmd_tvshows(client, message):
    await _title_command(client, message, "tv", "/tvshows tv show name")


# ======================================================================
# style chosen / name typed / search / pick
# ======================================================================
def _set_style(ud: dict, new_style: int) -> None:
    """switch style; the chosen poster / banner index only carries over between styles of the same kind"""
    old = ud.get("style")
    if old is None or dataset.art_kind(old) != dataset.art_kind(new_style):
        ud["opts"]["pic"] = 0
    ud["style"] = new_style


async def _choose_style(client, user_id: int, chat_id: int, style: int, message):
    """Select a style. With an anime loaded -> render at once, otherwise ask for the name."""
    ud = _ud(user_id)
    _set_style(ud, style)
    if ud.get("anime") is not None and not ud.get("pending"):
        await generate(client, user_id, chat_id, source_msg=message)
        return
    pending = ud.pop("pending", None)
    if pending:                                   # user typed a name before choosing a style
        await _run_search(client, user_id, chat_id, pending, source_msg=message)
        return
    ud["await"] = "name"
    await _edit(message, bq(f"✅ {sc('style')} <b>{style}</b> {sc('selected')} ➜ {sc(POSTERS[style].blurb)}\n\n"
                            + sc(f"✍️ now send me the {_word(ud)} name.")), cancel_keyboard())


async def _handle_name(client, user_id: int, chat_id: int, query: str):
    ud = _ud(user_id)
    if not ud.get("style"):                       # no style yet: remember the name, show the picker
        ud["pending"] = query
        await client.send_message(chat_id, pick_style_text(query), reply_markup=styles_keyboard())
        return
    await _run_search(client, user_id, chat_id, query)


async def _run_search(client, user_id: int, chat_id: int, query: str, source_msg=None):
    ud = _ud(user_id)
    ud["await"] = None
    q = html.escape(query)
    text = bq(sc("🔎 searching ") + f"“{q}”…")
    if source_msg is not None and not source_msg.photo:
        status = source_msg
        await status.edit_text(text)
    else:
        status = await client.send_message(chat_id, text)
    kind = _kind(ud)
    try:
        results = await dataset.search_title(kind, query)
    except Exception:
        log.exception("%s search failed", dataset.SOURCE_NAME[kind])
        await status.edit_text(bq(sc(f"⚠️ {dataset.SOURCE_NAME[kind].lower()} isn't answering right now. "
                                     f"try again in a moment.")),
                               reply_markup=kb([btn("🔁 try again", "na", color=GREEN)]))
        ud["await"] = "name"
        return
    if not results:
        ud["await"] = "name"
        hint = ("send another spelling or the japanese / romaji title." if kind == "anime"
                else "check the spelling or try the english title.")
        await status.edit_text(bq(sc("😕 nothing found for ") + f"“{q}”\n" + sc(hint)),
                               reply_markup=cancel_keyboard())
        return
    rows = []
    for r in results:
        label = " · ".join(str(x) for x in (r["title"], r["year"], r["format"]) if x)
        rows.append([btn(label[:60], f"pk:{_KIND_CODE[kind]}:{r['id']}", raw=True, color=BLUE)])
    rows.append([btn("✖️ cancel", "cx", color=RED)])
    await status.edit_text(bq(sc("🔎 results for ") + f"“{q}” " + sc("➜ pick one:")), reply_markup=kb(*rows))


async def on_text(client, message):
    """The listener: what the text means depends on what we asked for last."""
    user_id, chat_id = message.from_user.id, message.chat.id
    ud = _ud(user_id)
    text = (message.text or "").strip()
    if not text:
        return
    mode = ud.get("await")

    if mode == "button":
        ud["opts"]["button"] = text.upper()[:22]
        ud["await"] = None
        await _safe_delete(client, chat_id, message.id)
        await generate(client, user_id, chat_id, panel=True)
    else:                                                 # "name" or nothing: a name to search
        await _handle_name(client, user_id, chat_id, text[:100])


async def on_pick(client, cq):
    ud = _ud(cq.from_user.id)
    await cq.answer(sc("loading…"))
    if not ud.get("style"):
        await _edit(cq.message, bq(sc("session expired ➜ send /start again.")))
        return
    await _edit(cq.message, bq(sc("⏳ fetching data and artwork…")))
    parts = cq.data.split(":")                                       # pk:<id>  (old buttons)  |  pk:<a/m/t>:<id>
    kind, ident = (_CODE_KIND.get(parts[1], "anime"), int(parts[2])) if len(parts) == 3 else ("anime", int(parts[1]))
    try:
        ud["anime"] = await dataset.load_title(kind, ident)
        ud["kind"] = kind
    except Exception:
        log.exception("loading the %s failed", kind)
        await _edit(cq.message, bq(sc("⚠️ couldn't load that title. try another result.")),
                    kb([btn("🔍 new title", "na", color=GREEN)]))
        return
    ud["await"] = None
    ud["opts"]["char"] = 0                                          # new anime -> first character candidate
    ud["opts"]["pic"] = 0                                           # ... and first poster / banner
    ud.pop("pmsg", None)
    await generate(client, cq.from_user.id, cq.message.chat.id, source_msg=cq.message)


# ======================================================================
# inline-button router
# ======================================================================
async def on_button(client, cq):
    ud = _ud(cq.from_user.id)
    data = cq.data
    msg = cq.message
    cid = msg.chat.id
    uid = cq.from_user.id

    if data == "noop":
        await cq.answer()
        return

    # ---- start / about / help
    if data == "home":
        await cq.answer()
        await _edit(msg, start_text(cq.from_user), start_keyboard())
        return
    if data == "ab":
        await cq.answer()
        await _edit(msg, ABOUT_TEXT, about_keyboard())
        return
    if data == "hp":
        await cq.answer()
        await _edit(msg, help_text(), help_keyboard(back=True))
        return

    # ---- style previews
    if data.startswith("sv:"):
        await cq.answer()
        await open_viewer(client, cid, int(data[3:]), msg if msg.photo else None)
        return

    # ---- style grid
    if data.startswith("st:"):
        await cq.answer()
        await _choose_style(client, uid, cid, int(data[3:]), msg)
        return
    if data in ("sl", "sl0"):                                      # open the style picker
        await cq.answer()
        if msg.photo:                                              # under the poster: keep the picture
            await msg.edit_reply_markup(styles_keyboard(ud.get("style"), back=True))
        else:
            await _edit(msg, pick_style_text(), styles_keyboard(ud.get("style")))
        return
    if data == "bk":                                               # back from picker to poster buttons
        await cq.answer()
        await msg.edit_reply_markup(result_keyboard(ud.get("style", 1)))
        return
    if data == "cx":
        await cq.answer(sc("cancelled"))
        ud["await"] = None
        ud.pop("pending", None)
        await _edit(msg, pick_style_text(), styles_keyboard(ud.get("style")))
        return
    if data == "na":                                               # new anime -> name listener
        await cq.answer()
        ud["await"] = "name"
        text = bq(sc(f"🔍 send me the {_word(ud)} name."))
        if msg.photo:
            await client.send_message(cid, text, reply_markup=cancel_keyboard())
        else:
            await _edit(msg, text, cancel_keyboard())
        return
    if data.startswith("pk:"):
        await on_pick(client, cq)
        return

    # ---- everything below needs a loaded anime
    if ud.get("anime") is None or not ud.get("style"):
        await cq.answer(sc("session expired - send /start"), show_alert=True)
        return
    if ud.get("busy"):
        await cq.answer(sc("still rendering, one moment…"))
        return

    style = ud["style"]
    opts = ud["opts"]

    if data in ("nx", "pv"):
        await cq.answer(sc("rendering…"))
        _set_style(ud, style % N_STYLES + 1 if data == "nx" else (style - 2) % N_STYLES + 1)
        await generate(client, uid, cid, source_msg=msg)
    elif data == "rg":
        await cq.answer(sc("rendering…"))
        await generate(client, uid, cid, source_msg=msg)
    elif data == "fl":
        last = ud.get("last")
        if not last:
            await cq.answer(sc("nothing to send yet."), show_alert=True)
            return
        await cq.answer(sc("sending file…"))
        await client.send_chat_action(cid, enums.ChatAction.UPLOAD_DOCUMENT)
        slug = re.sub(r"[^a-z0-9]+", "_", ud["anime"].title.lower()).strip("_")[:40] or "poster"
        await client.send_document(cid, _bio(last.png, f"{slug}_style{style}.png"), file_name=f"{slug}_style{style}.png")
    elif data == "op":
        s = POSTERS[style]
        await cq.answer()
        await msg.edit_reply_markup(options_keyboard(style, opts, ud["anime"]))
    elif data == "ob":
        await cq.answer()
        await msg.edit_reply_markup(result_keyboard(style))
    elif data == "thm":                                             # 🎨 colour: 🌑 dark colours / 🌸 soft colours
        await cq.answer()
        await msg.edit_reply_markup(theme_menu_keyboard(opts.get("theme")))
    elif data.startswith("thg:"):                                   # one group: its 3 x 3 colour grid
        group = data[4:]
        if group not in themes.GROUPS:
            await cq.answer()
            return
        await cq.answer()
        await msg.edit_reply_markup(theme_keyboard(opts.get("theme"), group))
    elif data.startswith("th:"):                                    # a colour (or the original colours) was chosen
        key = themes.normalize(data[3:])
        if key == themes.normalize(opts.get("theme")):
            await cq.answer(sc("already using this colour"))
            await msg.edit_reply_markup(options_keyboard(style, opts, ud["anime"]))
            return
        await cq.answer(sc(f"{themes.label(key)} · rendering…"))
        opts["theme"] = key
        try:                                                        # remember it for next time (Mongo / json)
            await asyncio.wait_for(chstore.set_theme(uid, key), timeout=8)
        except Exception:
            log.warning("could not save the colour choice", exc_info=True)
        await generate(client, uid, cid, panel=True, source_msg=msg)
    elif data in ("ipp", "ipn"):                                    # poster / banner: back / next
        kind = dataset.art_kind(style)
        await dataset.list_art(ud["anime"], kind)                   # first tap: look up the other posters / banners
        n = dataset.art_count(ud["anime"], style)
        if n < 2:
            await cq.answer(sc(f"no other {kind} found for this anime."), show_alert=True)
            await msg.edit_reply_markup(options_keyboard(style, opts, ud["anime"]))
            return
        await cq.answer(sc("rendering…"))
        opts["pic"] = ((opts.get("pic") or 0) + (1 if data == "ipn" else -1)) % n
        await generate(client, uid, cid, panel=True, source_msg=msg)
    elif data == "obt":
        await cq.answer(sc("rendering…"))
        cur = opts["button"]
        i = BUTTON_PRESETS.index(cur) if cur in BUTTON_PRESETS else -1
        opts["button"] = BUTTON_PRESETS[(i + 1) % len(BUTTON_PRESETS)]
        await generate(client, uid, cid, panel=True, source_msg=msg)
    elif data in ("ocp", "ocn", "occ"):                              # choose the character art
        n = len(ud["anime"].characters)
        if n == 0:
            await cq.answer(sc("no character art found for this anime."), show_alert=True)
            return
        await cq.answer(sc("rendering…"))
        cur = opts.get("char", 0)                                   # -1 = cover card, 0..n-1 = art
        if data == "occ":
            opts["char"] = -1 if cur >= 0 else 0
        else:
            step = 1 if data == "ocn" else -1
            opts["char"] = (cur + 1 + step) % (n + 1) - 1           # cycles -1,0,1..n-1
        await generate(client, uid, cid, panel=True, source_msg=msg)
    elif data == "obc":
        await cq.answer()
        ud["await"] = "button"
        ud["pmsg"] = msg.id
        await client.send_message(cid, bq(sc("✏️ send the button text (max 22 characters).")),
                                  reply_markup=kb([btn("✖️ cancel", "cxo", color=RED)]))
    elif data == "cxo":                                            # cancel a text prompt from the options panel
        await cq.answer(sc("cancelled"))
        ud["await"] = None
        await _safe_delete(client, cid, msg.id)
    else:
        await cq.answer()


# ======================================================================
# registration
# ======================================================================
async def post_start(app):
    await app.set_bot_commands([
        BotCommand("start", "Wake me up"),
        BotCommand("help", "All commands + support"),
        BotCommand("poster", "Anime poster: /poster Naruto"),
        BotCommand("movies", "Movie poster: /movies Inception"),
        BotCommand("tvshows", "TV show poster: /tvshows Breaking Bad"),
        BotCommand("styles", "Preview every poster style"),
        BotCommand("addchnl", "Add your channel (I must be full admin)"),
        BotCommand("mychnls", "Your channels"),
        BotCommand("removechnl", "Remove a channel"),
        BotCommand("schedules", "Your scheduled posts"),
        BotCommand("template", "Your caption template"),
        BotCommand("timezone", "Set your time zone"),
    ])


def _not_command(_, __, m):
    return bool(m.text) and not m.text.startswith("/")


def register(app):
    # the guard runs first (group -1) and can block everybody else
    app.add_handler(MessageHandler(guard), group=-1)
    app.add_handler(CallbackQueryHandler(guard), group=-1)

    app.add_handler(MessageHandler(cmd_start, filters.command("start")))
    app.add_handler(MessageHandler(cmd_help, filters.command("help")))
    app.add_handler(MessageHandler(cmd_styles, filters.command("styles")))
    app.add_handler(MessageHandler(cmd_poster, filters.command("poster")))
    app.add_handler(MessageHandler(cmd_movies, filters.command(["movies", "movie"])))
    app.add_handler(MessageHandler(cmd_tvshows, filters.command(["tvshows", "tvshow", "tv"])))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(on_text, filters.private & filters.create(_not_command)))

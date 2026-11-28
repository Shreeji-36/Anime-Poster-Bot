"""settings.py - /settings : one panel for every type of poster (Movies, TV Shows, Anime)

  /settings
      📢 Main Channel                       (opens your channel)
      🎬 Movies        📺 TV Shows
      🌸 Anime         💬 Chat              (opens the support group)

  Movies / TV Shows / Anime each open their own panel, so every type keeps its OWN:
      🏷 watermark     the text printed on the poster (the "branding")
      📝 template      the caption that is attached to every new poster (+ used by 📣 post / ⏰ schedule)
      🎨 style         the poster style that is used at once (no style picker)
      🌈 colour        the accent colour of the poster
      🔘 button text   WATCH NOW / WATCH TRAILER / your own text

Everything is saved per user in the same record as the channels (chstore.py): MongoDB when MONGO_URI is set,
otherwise a json file (Render deletes that file on every restart - set MONGO_URI so the settings stay).

Free tier: only small text messages and one tiny database record, no image work, no new libraries.
A value that is not set for a type falls back to the old defaults (the global branding / colour / /template).
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import re

from pyrogram import filters
from pyrogram.errors import MessageNotModified, RPCError
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

import chname
import chstore
import cmds
import config
import dataset
import themes
from dataset import BUTTON_PRESETS, POSTERS
from ui import BLUE, GREEN, NO_PREVIEW, RED, bq, btn, kb, sc

log = logging.getLogger("settings")

MODES = ("se_wm", "se_bt")                      # the bot is waiting for a typed watermark / button text
CAPTION_MAX = 1024                              # telegram's limit for a photo caption
DB_TIMEOUT = 8

# order of the panels = order of the buttons.  key = the kind used everywhere else in the bot
KINDS: dict[str, tuple[str, str]] = {"movie": ("🎬", "Movies"), "tv": ("📺", "TV Shows"), "anime": ("🌸", "Anime")}


# ======================================================================
# small helpers
# ======================================================================
async def _kind_settings(uid: int, kind: str) -> dict:
    try:
        return await asyncio.wait_for(chstore.get_kind(uid, kind), DB_TIMEOUT)
    except Exception:
        log.exception("could not read the %s settings (is MONGO_URI correct?)", kind)
        return {}


async def _save(uid: int, kind: str, key: str, value) -> bool:
    try:
        await asyncio.wait_for(chstore.set_kind(uid, kind, key, value), DB_TIMEOUT)
        return True
    except Exception:
        log.exception("could not save the %s setting %s", kind, key)
        return False


def _label(kind: str) -> str:
    icon, name = KINDS[kind]
    return f"{icon} {name}"


def _plain_len(text: str) -> int:
    """length of a message the way telegram counts it (tags removed) - a photo caption may have 1024 of these"""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text or "")))


async def _edit(msg, text: str, markup=None) -> None:
    """edit the panel message. /settings is a photo with a caption, so the caption is edited;
    a screen that is longer than a caption (a long template) continues as a normal text message."""
    try:
        if not msg.photo:
            await msg.edit_text(text, reply_markup=markup, **NO_PREVIEW)
        elif _plain_len(text) <= CAPTION_MAX:
            await msg.edit_caption(text, reply_markup=markup)
        else:
            await msg.reply_text(text, reply_markup=markup, **NO_PREVIEW)
            try:
                await msg.delete()
            except RPCError:
                pass
    except MessageNotModified:
        pass


async def _send_panel(client, cid: int, text: str, markup=None) -> None:
    """a new panel: the photo of /start (one of MENU_IMAGES, picked at random) with the panel as its caption;
    plain text when the picture can't be sent or the text is longer than a caption"""
    if _plain_len(text) <= CAPTION_MAX and config.MENU_IMAGES:
        try:
            await client.send_photo(cid, random.choice(config.MENU_IMAGES), caption=text, reply_markup=markup)
            return
        except RPCError as e:
            log.warning("settings image failed (%s) -> text only", e)
    await client.send_message(cid, text, reply_markup=markup, **NO_PREVIEW)


def _back_row(kind: str):
    return [btn("🔙 back", f"se_k:{kind}", color=RED)]


# ======================================================================
# the main menu
# ======================================================================
def main_text() -> str:
    return bq(sc("⚙️ settings") + "\n" +
              sc("choose a type. movies, tv shows and anime each keep their own watermark, template, style, "
                 "colour and button text."))


def main_keyboard():
    return kb([btn("📢 main channel", url=config.MAIN_CHANNEL_URL, color=BLUE)],
              [btn(_label("movie"), "se_k:movie", color=GREEN), btn(_label("tv"), "se_k:tv", color=GREEN)],
              [btn(_label("anime"), "se_k:anime", color=GREEN), btn("💬 chat", url=config.SUPPORT_URL, color=BLUE)])


# ======================================================================
# the panel of one type
# ======================================================================
async def panel_view(uid: int, kind: str):
    s = await _kind_settings(uid, kind)
    try:
        glob_brand = await asyncio.wait_for(chstore.get_brand(uid), DB_TIMEOUT)
        glob_tpl = await asyncio.wait_for(chstore.get_template(uid), DB_TIMEOUT)
        glob_theme = await asyncio.wait_for(chstore.get_theme(uid), DB_TIMEOUT)
    except Exception:
        glob_brand, glob_tpl, glob_theme = "", None, ""

    if "brand" in s:
        wm = html.escape(s["brand"]) if s["brand"] else sc("none")
    else:
        wm = sc("default") + (f" ({html.escape(glob_brand)})" if glob_brand else "")
    if s.get("tpl"):
        tp = sc("✅ set")
    else:
        tp = sc("default (/template)") if glob_tpl else sc("not set")
    st_n = s.get("style")
    st = f"<b>{st_n}</b> · {sc(POSTERS[st_n].blurb)}" if st_n in POSTERS else sc("ask me every time")
    theme = themes.normalize(s["theme"] if "theme" in s else glob_theme)
    co = f"{themes.emoji(theme)} {sc(themes.label(theme))}"
    bt = html.escape(s.get("button") or BUTTON_PRESETS[0])

    text = bq(f"{_label(kind)} · " + sc("settings") + "\n\n" +
              sc("🏷 watermark ➜ ") + wm + "\n" +
              sc("📝 template ➜ ") + tp + "\n" +
              sc("🎨 style ➜ ") + st + "\n" +
              sc("🌈 colour ➜ ") + co + "\n" +
              sc("🔘 button ➜ ") + bt)
    markup = kb([btn("🏷 watermark", f"se_wm:{kind}", color=BLUE), btn("📝 template", f"se_tp:{kind}", color=BLUE)],
                [btn("🎨 style", f"se_sy:{kind}", color=BLUE), btn("🌈 colour", f"se_co:{kind}", color=BLUE)],
                [btn("🔘 button text", f"se_bt:{kind}", color=BLUE)],
                [btn("🔙 back", "se_home", color=RED)])
    return text, markup


# ---------------------------------------------------------------- 🏷 watermark
async def wm_view(uid: int, kind: str):
    s = await _kind_settings(uid, kind)
    if "brand" in s:
        now = html.escape(s["brand"]) if s["brand"] else sc("none")
    else:
        try:
            glob = await asyncio.wait_for(chstore.get_brand(uid), DB_TIMEOUT)
        except Exception:
            glob = ""
        now = sc("default") + (f" · <b>{html.escape(glob)}</b>" if glob else "")
    text = bq(f"{_label(kind)} · " + sc("🏷 watermark") + "\n\n" +
              sc("the text printed on every poster of this type, in the poster's own font and colours.\n"
                 f"up to {chname.LIMIT} characters, no emoji.") + "\n\n" + sc("now: ") + now)
    rows = [[btn("✏️ change", f"se_wmset:{kind}", color=GREEN), btn("🚫 none", f"se_wmnone:{kind}", color=RED)]]
    if "brand" in s:
        rows.append([btn("♻️ use the default", f"se_wmdef:{kind}", color=BLUE)])
    rows.append(_back_row(kind))
    return text, kb(*rows)


# ---------------------------------------------------------------- 📝 template
async def tp_view(uid: int, kind: str):
    import autopost                                   # (autopost imports cmds, so it is imported here, not on top)
    s = await _kind_settings(uid, kind)
    own = s.get("tpl")
    try:
        glob = await asyncio.wait_for(chstore.get_template(uid), DB_TIMEOUT)
    except Exception:
        glob = None
    head = (sc(f"{_label(kind)} · 📝 caption template\n") +
            sc("this caption is attached to every new poster of this type, and offered under 📣 post to channel and "
               "⏰ schedule. placeholders are filled in for every title:\n") + autopost._placeholder_help())
    if own:
        text = bq(head + "\n\n" + sc("your template:")) + "\n" + own["html"]
    elif glob:
        text = bq(head + "\n\n" + sc("no template for this type yet - your default template (/template) is used:")) + "\n" + glob["html"]
    else:
        text = bq(head + "\n\n" + sc("you have no template yet."))
    rows = [[btn("✏️ replace" if own else "✏️ create template", f"se_tpset:{kind}", color=GREEN)]]
    if own or glob:
        row = [btn("👁 preview", f"se_tpprev:{kind}", color=BLUE)]
        if own:
            row.append(btn("🗑 delete", f"se_tpdel:{kind}", color=RED))
        rows.append(row)
    rows.append(_back_row(kind))
    return text, kb(*rows)


# ---------------------------------------------------------------- 🎨 style
async def sy_view(uid: int, kind: str):
    s = await _kind_settings(uid, kind)
    cur = s.get("style")
    lines = "\n".join(f"<b>{p.id}</b> ➜ {sc(p.blurb)}" for p in POSTERS.values())
    text = (bq(f"{_label(kind)} · " + sc("🎨 poster style\n") +
               sc("this style is used at once for every new poster of this type (you can still change it under the poster).")) +
            "\n" + bq(lines, expandable=True))
    cells = [btn(f"{'✅ ' if sid == cur else ''}{sid}", f"se_sys:{kind}:{sid}", raw=True,
                 color=GREEN if sid == cur else BLUE) for sid in POSTERS]
    rows = [cells[i:i + 5] for i in range(0, len(cells), 5)]
    if cur in POSTERS:
        rows.append([btn("♻️ ask me every time", f"se_syc:{kind}", color=BLUE)])
    rows.append(_back_row(kind))
    return text, kb(*rows)


# ---------------------------------------------------------------- 🌈 colour
async def _current_theme(uid: int, kind: str) -> str:
    s = await _kind_settings(uid, kind)
    if "theme" in s:
        return themes.normalize(s["theme"])
    try:
        return themes.normalize(await asyncio.wait_for(chstore.get_theme(uid), DB_TIMEOUT))
    except Exception:
        return themes.DEFAULT


async def co_view(uid: int, kind: str):
    current = await _current_theme(uid, kind)
    cur_group = themes.group_of(current)
    rows = [[btn(("✅ " if cur_group == g else "") + f"{icon} {name}", f"se_cog:{kind}:{g}", raw=True,
                 color=GREEN if cur_group == g else BLUE) for g, (icon, name) in themes.GROUPS.items()],
            [btn(("✅ " if current == themes.DEFAULT else "") + f"{themes.emoji(themes.DEFAULT)} {themes.label(themes.DEFAULT)}",
                 f"se_cos:{kind}:{themes.DEFAULT}", raw=True, color=GREEN if current == themes.DEFAULT else BLUE)],
            _back_row(kind)]
    text = bq(f"{_label(kind)} · " + sc("🌈 poster colour\n") +
              sc("the accent colour of the poster - not the white page.\nnow: ") + f"{themes.emoji(current)} {themes.label(current)}")
    return text, kb(*rows)


async def cog_view(uid: int, kind: str, group: str):
    current = await _current_theme(uid, kind)
    cells = [btn(("✅ " if k == current else "") + f"{themes.emoji(k)} {themes.label(k)}", f"se_cos:{kind}:{k}", raw=True,
                 color=GREEN if k == current else BLUE) for k in themes.keys_in(group)]
    rows = [cells[i:i + 3] for i in range(0, len(cells), 3)]
    rows.append([btn("🔙 back", f"se_co:{kind}", color=RED), btn("⚙️ panel", f"se_k:{kind}", color=BLUE)])
    icon, name = themes.GROUPS[group]
    return bq(f"{_label(kind)} · {icon} " + sc(name)), kb(*rows)


# ---------------------------------------------------------------- 🔘 button text
async def bt_view(uid: int, kind: str):
    s = await _kind_settings(uid, kind)
    cur = s.get("button") or BUTTON_PRESETS[0]
    text = bq(f"{_label(kind)} · " + sc("🔘 button text\n") +
              sc("the text of the big button on the poster (styles that have one).\nnow: ") + f"<b>{html.escape(cur)}</b>")
    cells = [btn(("✅ " if p == cur else "") + p, f"se_bts:{kind}:{i}", raw=True, color=GREEN if p == cur else BLUE)
             for i, p in enumerate(BUTTON_PRESETS)]
    rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
    rows.append([btn("✏️ custom text", f"se_btc:{kind}", color=GREEN)])
    rows.append(_back_row(kind))
    return text, kb(*rows)


# ======================================================================
# used by the poster flow (cmds.py) - what the saved settings do
# ======================================================================
async def default_style(uid: int, kind: str) -> int | None:
    """the style saved for this type, None = ask every time"""
    n = (await _kind_settings(uid, kind)).get("style")
    return n if n in POSTERS else None


async def kind_defaults(uid: int, kind: str) -> dict:
    """the colour + button text of a type (only what is set - the rest stays as it is)"""
    out = {}
    s = await _kind_settings(uid, kind)
    if "theme" in s:
        out["theme"] = themes.normalize(s["theme"])
    if s.get("button"):
        out["button"] = s["button"]
    return out


async def template_caption(uid: int, a) -> tuple[str | None, bool]:
    """(caption html, too_long) for the poster of title `a`.
    (None, False) = no template, (None, True) = the template is longer than a caption can be even without the synopsis."""
    import autopost
    kind = dataset.kind_of(a)
    try:
        tpl = await asyncio.wait_for(chstore.get_template(uid, kind), DB_TIMEOUT)
    except Exception:
        log.exception("reading the caption template failed")
        return None, False
    if not tpl or not (tpl.get("html") or "").strip():
        return None, False
    values = autopost.fields(a)
    desc = (a.raw or {}).get("description")
    for limit in (350, 220, 120, 60, 0):                 # a long {synopsis} is shortened until the caption fits
        values["synopsis"] = dataset.clean_text(desc, limit) if limit else ""
        out = autopost._no_emoji(autopost.fill(tpl["html"], values))     # a bot can't send custom emoji
        if autopost._plain_len(out) <= CAPTION_MAX:
            return out, False
    return None, True


# ======================================================================
# /settings
# ======================================================================
async def cmd_settings(client, message):
    uid = message.from_user.id
    ud = cmds._ud(uid)
    ud["await"] = None
    ud.pop("tpl_kind", None)
    await cmds._reply_with_image(message, main_text(), main_keyboard())          # photo + text + buttons, like /start


# ======================================================================
# typed text (watermark / button text)
# ======================================================================
def _cancel_kb(kind: str):
    return kb([btn("✖️ cancel", f"se_cx:{kind}", color=RED)])


async def on_mode_text(client, message):
    uid, cid = message.from_user.id, message.chat.id
    ud = cmds._ud(uid)
    mode, kind = ud.get("await"), ud.get("se_kind")
    if kind not in KINDS:
        ud["await"] = None
        return
    raw = (message.text or "").strip()

    if mode == "se_wm":
        text = chname.clean(raw)
        if not text:
            await message.reply_text(bq(sc("😕 send some normal letters (emoji can't be drawn on a poster). "
                                           f"up to {chname.LIMIT} characters.")), reply_markup=_cancel_kb(kind))
            return
        if not await _save(uid, kind, "brand", text):
            await message.reply_text(bq(sc("⚠️ i couldn't save it (database problem?). try again in a moment.")),
                                     reply_markup=_cancel_kb(kind))
            return
    else:                                                 # se_bt
        text = raw.upper()[:22]
        if not text:
            return
        if not await _save(uid, kind, "button", text):
            await message.reply_text(bq(sc("⚠️ i couldn't save it (database problem?). try again in a moment.")),
                                     reply_markup=_cancel_kb(kind))
            return
        if ud.get("opts_kind") == kind:                   # the poster flow already running uses it from now on
            ud["opts"]["button"] = text

    ud["await"] = None
    for mid in (ud.pop("se_msg", None), message.id):
        if mid:
            await cmds._safe_delete(client, cid, mid)
    view, markup = await (wm_view if mode == "se_wm" else bt_view)(uid, kind)
    await _send_panel(client, cid, view, markup)


# ======================================================================
# inline buttons  (callback data "se_...")
# ======================================================================
async def on_cb(client, cq):
    uid, msg = cq.from_user.id, cq.message
    cid = msg.chat.id
    ud = cmds._ud(uid)
    parts = cq.data.split(":")
    cmd, kind = parts[0], (parts[1] if len(parts) > 1 else "")

    if cmd == "se_home":
        await cq.answer()
        ud["await"] = None
        await _edit(msg, main_text(), main_keyboard())
        return
    if kind not in KINDS:
        await cq.answer()
        return

    if cmd == "se_k":                                                    # panel of one type
        await cq.answer()
        ud["await"] = None
        text, markup = await panel_view(uid, kind)
        await _edit(msg, text, markup)

    elif cmd == "se_cx":                                                 # cancel a typed value -> back to the panel
        await cq.answer(sc("cancelled"))
        ud["await"] = None
        ud.pop("se_msg", None)
        text, markup = await panel_view(uid, kind)
        await _edit(msg, text, markup)

    # ---- 🏷 watermark
    elif cmd == "se_wm":
        await cq.answer()
        text, markup = await wm_view(uid, kind)
        await _edit(msg, text, markup)
    elif cmd == "se_wmset":
        await cq.answer()
        ud["await"], ud["se_kind"], ud["se_msg"] = "se_wm", kind, msg.id
        await _edit(msg, bq(f"{_label(kind)} · " + sc("🏷 watermark\n") +
                            sc(f"✍️ send the new text (up to {chname.LIMIT} characters, no emoji).")), _cancel_kb(kind))
    elif cmd in ("se_wmnone", "se_wmdef"):
        ok = await _save(uid, kind, "brand", "" if cmd == "se_wmnone" else None)
        await cq.answer(sc("saved") if ok else sc("could not save - try again"), show_alert=not ok)
        text, markup = await wm_view(uid, kind)
        await _edit(msg, text, markup)

    # ---- 📝 template
    elif cmd == "se_tp":
        await cq.answer()
        ud["await"] = None
        text, markup = await tp_view(uid, kind)
        await _edit(msg, text, markup)
    elif cmd == "se_tpset":                                              # the normal template editor, saving for this type
        import autopost
        await cq.answer()
        ud["tpl_kind"], ud["await"] = kind, "aptpl"
        await client.send_message(
            cid,
            bq(sc(f"✍️ send the caption template for {KINDS[kind][1]}. format it as you like (bold, italic, spoiler, links, "
                  "quotes) and put these placeholders where the data should go:\n") + autopost._placeholder_help() + "\n\n" +
               sc("example:\n") + "<code>🎬 {title}\n⭐ {score}/10 · {year}\n🎭 {genres}\n\n{synopsis}\n\n{hashtags}</code>"),
            reply_markup=autopost._cancel_kb())
    elif cmd == "se_tpprev":
        import autopost
        try:
            tpl = await asyncio.wait_for(chstore.get_template(uid, kind), DB_TIMEOUT)
        except Exception:
            tpl = None
        if not tpl:
            await cq.answer(sc("no template"), show_alert=True)
            return
        await cq.answer()
        await autopost._preview_template(client, uid, cid, tpl)
    elif cmd == "se_tpdel":
        ok = await _save(uid, kind, "tpl", None)
        await cq.answer(sc("🗑 template deleted") if ok else sc("could not save - try again"), show_alert=not ok)
        text, markup = await tp_view(uid, kind)
        await _edit(msg, text, markup)

    # ---- 🎨 style
    elif cmd == "se_sy":
        await cq.answer()
        text, markup = await sy_view(uid, kind)
        await _edit(msg, text, markup)
    elif cmd in ("se_sys", "se_syc"):
        try:
            n = int(parts[2]) if cmd == "se_sys" else None
        except (IndexError, ValueError):
            await cq.answer()
            return
        if n is not None and n not in POSTERS:
            await cq.answer()
            return
        ok = await _save(uid, kind, "style", n)
        await cq.answer(sc(f"style {n} saved") if (ok and n) else sc("saved") if ok else sc("could not save - try again"),
                        show_alert=not ok)
        text, markup = await panel_view(uid, kind)
        await _edit(msg, text, markup)

    # ---- 🌈 colour
    elif cmd == "se_co":
        await cq.answer()
        text, markup = await co_view(uid, kind)
        await _edit(msg, text, markup)
    elif cmd == "se_cog":
        group = parts[2] if len(parts) > 2 else ""
        if group not in themes.GROUPS:
            await cq.answer()
            return
        await cq.answer()
        text, markup = await cog_view(uid, kind, group)
        await _edit(msg, text, markup)
    elif cmd == "se_cos":
        key = themes.normalize(parts[2] if len(parts) > 2 else "")
        ok = await _save(uid, kind, "theme", key)
        if ok and ud.get("opts_kind") == kind:                           # the poster flow already running uses it now
            ud["opts"]["theme"] = key
        await cq.answer(sc(f"{themes.label(key)} saved") if ok else sc("could not save - try again"), show_alert=not ok)
        text, markup = await panel_view(uid, kind)
        await _edit(msg, text, markup)

    # ---- 🔘 button text
    elif cmd == "se_bt":
        await cq.answer()
        ud["await"] = None
        text, markup = await bt_view(uid, kind)
        await _edit(msg, text, markup)
    elif cmd == "se_bts":
        try:
            text_ = BUTTON_PRESETS[int(parts[2])]
        except (IndexError, ValueError):
            await cq.answer()
            return
        ok = await _save(uid, kind, "button", text_)
        if ok and ud.get("opts_kind") == kind:
            ud["opts"]["button"] = text_
        await cq.answer(sc("saved") if ok else sc("could not save - try again"), show_alert=not ok)
        text, markup = await bt_view(uid, kind)
        await _edit(msg, text, markup)
    elif cmd == "se_btc":
        await cq.answer()
        ud["await"], ud["se_kind"], ud["se_msg"] = "se_bt", kind, msg.id
        await _edit(msg, bq(f"{_label(kind)} · " + sc("🔘 button text\n") + sc("✍️ send the button text (max 22 characters).")),
                    _cancel_kb(kind))
    else:
        await cq.answer()


# ======================================================================
# wiring
# ======================================================================
def _mode_filter(*modes):
    return filters.create(lambda _, __, m: bool(m.from_user) and cmds.STATE[m.from_user.id].get("await") in modes)


def register(app):
    """call BEFORE cmds.register(): cmds has a catch-all button handler and a catch-all text handler"""
    app.add_handler(MessageHandler(cmd_settings, filters.command("settings") & filters.private))
    app.add_handler(MessageHandler(on_mode_text, filters.private & filters.create(cmds._not_command) & _mode_filter(*MODES)))
    app.add_handler(CallbackQueryHandler(on_cb, filters.regex(r"^se_")))

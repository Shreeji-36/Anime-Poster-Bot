"""autopost.py - scheduled posts (⏰) + caption templates (/template) + time zone (/timezone)

  ⏰ schedule   button under every poster:  channel -> time -> caption -> preview -> saved.
                The poster picture you see is stored with the job, so the post is exactly what you previewed and the
                free Render CPU does no rendering at posting time.
  /schedules    your pending posts (delete any of them)
  /template     ONE caption template per user.  Placeholders like {title} {score} {genres} are filled in for every
                anime / movie / tv show.  Used by ⏰ schedule and by the 📝 button of 📣 post to channel.
  /timezone     the time zone your times are read in  (Asia/Kolkata, Europe/London, UTC+5:30 ...)

Free tier notes
  * jobs live in MongoDB (MONGO_URI). Without it they are kept in data/jobs.json, which Render deletes on every restart.
  * a small background loop checks for due jobs every 20 s: no extra RAM, no extra libraries (tzdata only).
  * Render's free service sleeps without web traffic -> a keep-alive ping to our own URL runs every 10 min.
  * a job that is late because the service was asleep is still posted (up to SCHEDULE_GRACE_HOURS late).
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, available_timezones

from pyrogram import enums, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.handlers import CallbackQueryHandler, MessageHandler

import chnl
import chstore
import cmds
import config
import dataset
from ui import BLUE, GREEN, NO_PREVIEW, RED, bq, btn, kb, sc

log = logging.getLogger("autopost")

HTML = enums.ParseMode.HTML
MODES = ("aptime", "aptext", "apbtns", "aptpl", "aptplb", "aptz")
TICK = 20                                   # seconds between two looks for due posts
STALE = 20 * 60                             # a job 'running' this long (bot killed while sending) is tried again
MAX_TRIES = 3
KEEPALIVE_EVERY = 600
TPL_MAX = 2000                              # characters of a template (with its formatting tags)

_tasks: list[asyncio.Task] = []


# ======================================================================
# time zone + time parsing
# ======================================================================
_ALL_TZ: dict[str, str] | None = None


def get_tz(name: str | None) -> tzinfo | None:
    """'Asia/Kolkata' | 'asia/kolkata' | 'UTC+5:30' | '+05:30' | 'UTC'  ->  tzinfo (None = not understood)"""
    global _ALL_TZ
    name = (name or "").strip()
    if not name:
        return None
    m = re.fullmatch(r"(?i)(?:utc|gmt)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?", name)
    if m:
        h, mi = int(m.group(2)), int(m.group(3) or 0)
        if h > 14 or mi > 59:
            return None
        return timezone((1 if m.group(1) == "+" else -1) * timedelta(hours=h, minutes=mi))
    if name.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        pass
    try:
        if _ALL_TZ is None:
            _ALL_TZ = {z.lower(): z for z in available_timezones()}
        real = _ALL_TZ.get(name.lower().replace(" ", "_"))
        return ZoneInfo(real) if real else None
    except Exception:
        return None


def tz_name(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if key:
        return key
    off = datetime.now(tz).utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    return f"UTC{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


def fmt_time(ts: float, tz: tzinfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%d %b %Y, %H:%M")


_WHEN = re.compile(
    r"^(?:(?P<kw>today|tomorrow)\s+|(?P<y>\d{4})-(?P<mo>\d{1,2})-(?P<d>\d{1,2})\s+|(?P<d2>\d{1,2})/(?P<mo2>\d{1,2})\s+)?"
    r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)?$")


def parse_when(text: str, tz: tzinfo, now: datetime | None = None) -> tuple[float | None, str | None]:
    """'in 2h30m' | '21:30' | '9pm' | 'tomorrow 8:00' | '2026-12-01 18:30' | '25/12 09:00'  ->  (unix time, None) or (None, error)"""
    now = now or datetime.now(tz)
    t = " ".join((text or "").lower().replace(",", " ").split())
    if not t:
        return None, "send a time"
    t = re.sub(r"(\d)\s*(?:minutes?|mins?)\b", r"\1m", t)                  # "in 5 minutes" -> "in 5m"
    t = re.sub(r"(\d)\s*(?:hours?|hrs?)\b", r"\1h", t)
    t = re.sub(r"(\d)\s*days?\b", r"\1d", t)

    dur = re.fullmatch(r"(?:in\s+)?((?:\d+\s*[dhm]\s*)+)", t)
    if dur:
        mins = sum(int(n) * {"d": 1440, "h": 60, "m": 1}[u] for n, u in re.findall(r"(\d+)\s*([dhm])", dur.group(1)))
        target = now + timedelta(minutes=mins)
    else:
        m = _WHEN.match(t)
        if not m or (m.group("mi") is None and not m.group("ap")):
            return None, "i couldn't read that time"
        h, mi, ap = int(m.group("h")), int(m.group("mi") or 0), m.group("ap")
        if ap:
            if not 1 <= h <= 12:
                return None, "hours with am / pm are 1 to 12"
            h = h % 12 + (12 if ap == "pm" else 0)
        if h > 23 or mi > 59:
            return None, "that is not a valid clock time"
        try:
            if m.group("y"):
                day = datetime(int(m.group("y")), int(m.group("mo")), int(m.group("d")), tzinfo=tz)
            elif m.group("d2"):
                day = datetime(now.year, int(m.group("mo2")), int(m.group("d2")), tzinfo=tz)
            else:
                day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if m.group("kw") == "tomorrow":
                    day += timedelta(days=1)
            target = day.replace(hour=h, minute=mi)
        except ValueError:
            return None, "that date does not exist"
        if target <= now + timedelta(seconds=60) and not (m.group("y") or m.group("d2") or m.group("kw")):
            target += timedelta(days=1)                                   # "21:30" said after 21:30 -> tomorrow
    if target <= now + timedelta(seconds=60):
        return None, "that time is in the past (it must be at least 1 minute from now)"
    if target > now + timedelta(days=60):
        return None, "i can only schedule up to 60 days ahead"
    return target.timestamp(), None


async def _tz_of(uid: int) -> tzinfo:
    try:
        name = await asyncio.wait_for(chstore.get_tz(uid), timeout=8)
    except Exception:
        name = ""
    return get_tz(name) or get_tz(config.TIMEZONE) or timezone.utc


# ======================================================================
# caption templates:  {title} {score} ...
# ======================================================================
PLACEHOLDERS = ["title", "native", "romaji", "score", "year", "season", "episodes", "duration", "studio", "genres",
                "hashtags", "tags", "status", "format", "synopsis"]

SAMPLE = dict(title="Sample Anime", native="サンプル", romaji="Sanpuru Anime", score="8.4", year="2024", season="Fall 2024",
              episodes="12", duration="24 min", studio="MAPPA", genres="Action, Drama, Fantasy",
              hashtags="#Action #Drama #Fantasy", tags="Shounen, Magic", status="Finished", format="TV",
              synopsis="A short, spoiler free summary of the story goes here.")


def fields(a) -> dict[str, str]:
    """the values of every placeholder for one loaded anime / movie / tv show"""
    r = a.raw or {}
    t = r.get("title") if isinstance(r.get("title"), dict) else {}
    score = dataset._score(r)
    eps, dur = r.get("episodes"), r.get("duration")
    genres = list(r.get("genres") or [])
    tags = [x["name"] for x in (r.get("tags") or []) if not x.get("isMediaSpoiler")][:5]
    season = " ".join(x for x in ((r.get("season") or "").title(), str(dataset._year(r) or "")) if x)
    return dict(
        title=a.title, native=t.get("native") or "", romaji=t.get("romaji") or "",
        score=f"{score / 10:.1f}" if score else "N/A", year=str(dataset._year(r) or ""), season=season,
        episodes=str(eps) if eps else "", duration=f"{dur} min" if dur else "",
        studio=dataset._studio(r) or "", genres=", ".join(genres),
        hashtags=" ".join("#" + re.sub(r"\W+", "", g) for g in genres if re.sub(r"\W+", "", g)),
        tags=", ".join(tags), status=(r.get("status") or "").replace("_", " ").title(),
        format=(r.get("format") or "").replace("_", " "), synopsis=dataset.clean_text(r.get("description"), 350))


def fill(template_html: str, values: dict[str, str]) -> str:
    """{title} -> the title (escaped for telegram html). Unknown {words} stay as they are."""
    return re.sub(r"\{(\w+)\}", lambda m: html.escape(values[m.group(1)]) if m.group(1) in values else m.group(0),
                  template_html or "")


def _html_of(message) -> str:
    """the message text WITH its formatting, as telegram html"""
    try:
        return message.text.html
    except Exception:
        return html.escape(message.text or "")


def _plain_len(h: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", h or "")))


def _no_emoji(h: str) -> str:
    return re.sub(r"</?emoji[^>]*>", "", h)


def _placeholder_help() -> str:
    return " ".join(f"<code>{{{k}}}</code>" for k in PLACEHOLDERS)


# ======================================================================
# sending (photo + html caption + url buttons)
# ======================================================================
async def send_html(client, chat_id: int, jpg: bytes, text_html: str, markup) -> list:
    async def go(t: str):
        if t and _plain_len(t) > chnl.CAPTION_MAX:             # too long for a caption: photo first, text after it
            return [await client.send_photo(chat_id, cmds._bio(jpg, "poster.jpg")),
                    await client.send_message(chat_id, t, parse_mode=HTML, reply_markup=markup, **NO_PREVIEW)]
        return [await client.send_photo(chat_id, cmds._bio(jpg, "poster.jpg"), caption=t or None, parse_mode=HTML,
                                        reply_markup=markup)]
    try:
        return await go(text_html)
    except RPCError:
        if "<emoji" in (text_html or ""):                       # bots can't always use custom emoji
            return await go(_no_emoji(text_html))
        raise


async def _html_to_entities(client, cid: int, h: str):
    """html -> (text, entities) the way chnl.py keeps them: a temporary message is sent to the user and read back"""
    try:
        m = await client.send_message(cid, h, parse_mode=HTML, **NO_PREVIEW)
    except RPCError:
        m = await client.send_message(cid, _no_emoji(h), parse_mode=HTML, **NO_PREVIEW)
    text, ents = m.text or "", (list(m.entities) if m.entities else None)
    await cmds._safe_delete(client, cid, m.id)
    return text, ents


# ======================================================================
# keyboards / small helpers
# ======================================================================
def _ud(uid: int) -> dict:
    return cmds._ud(uid)


def _cancel_kb(*rows):
    return kb(*rows, [btn("✖️ cancel", "ap_cancel", color=RED)])


def _head(sch: dict) -> str:
    return f"🎬 <b>{html.escape(sch['title'])}</b> · " + sc(f"style {sch.get('style')}") + (
        "\n" + sc("📣 ") + f"<b>{html.escape(sch['chat_title'])}</b>" if sch.get("chat_title") else "")


async def _cleanup(client, cid: int, sch: dict | None) -> None:
    for mid in (sch or {}).get("msgs", []):
        await cmds._safe_delete(client, cid, mid)


# ======================================================================
# ⏰ schedule flow
# ======================================================================
async def start_schedule(client, uid: int, cid: int) -> None:
    ud = _ud(uid)
    last = ud.get("last")
    if last is None or ud.get("anime") is None:
        await client.send_message(cid, bq(sc("🖼 make a poster first: send an anime name (or /poster naruto), pick a style, "
                                             "then press ⏰ schedule.")))
        return
    chans = await chstore.channels(uid)
    if not chans:
        await client.send_message(cid, bq(sc("📣 add a channel first with /addchnl - i need to be a full admin there.")),
                                  reply_markup=kb([btn("➕ add channel", "ch_add", color=GREEN)]))
        return
    if await chstore.count_jobs(uid) >= config.MAX_SCHEDULES:
        await client.send_message(cid, bq(sc(f"⏰ you already have {config.MAX_SCHEDULES} scheduled posts. "
                                             "delete one in /schedules first.")),
                                  reply_markup=kb([btn("📋 my schedules", "ap_list", color=BLUE)]))
        return
    ud["sch"] = dict(jpg=last.jpg, title=ud["anime"].title, style=ud.get("style"), anime=ud["anime"], msgs=[])
    ud["await"] = None
    if len(chans) == 1:
        ud["sch"].update(chat_id=chans[0]["id"], chat_title=chans[0]["title"])
        await _ask_time(client, uid, cid)
        return
    rows = [[btn(c["title"][:40], f"ap_ch:{c['id']}", raw=True, color=BLUE)] for c in chans]
    await client.send_message(cid, bq(_head(ud["sch"]) + "\n" + sc("⏰ schedule it for which channel?")),
                              reply_markup=_cancel_kb(*rows))


async def _ask_time(client, uid: int, cid: int) -> None:
    ud = _ud(uid)
    sch = ud["sch"]
    tz = await _tz_of(uid)
    now = datetime.now(tz)
    ud["await"] = "aptime"
    examples = ("<code>in 2h</code>  <code>in 45m</code>  <code>21:30</code>  <code>9pm</code>\n"
                "<code>tomorrow 8:00</code>  <code>25/12 09:00</code>  <code>2026-12-01 18:30</code>")
    await client.send_message(
        cid,
        bq(_head(sch) + "\n\n" + sc("⏰ when should i post it?\nyour time now: ") +
           f"<b>{now.strftime('%d %b, %H:%M')}</b> ({html.escape(tz_name(tz))})\n\n" +
           sc("send a time, for example:\n") + examples),
        reply_markup=_cancel_kb(
            [btn("in 1 hour", "ap_q:60", color=BLUE), btn("in 3 hours", "ap_q:180", color=BLUE),
             btn("in 6 hours", "ap_q:360", color=BLUE)],
            [btn("🌍 change time zone", "ap_tz", color=GREEN)]))


async def _ask_caption(client, uid: int, cid: int) -> None:
    ud = _ud(uid)
    sch = ud["sch"]
    tz = await _tz_of(uid)
    ud["await"] = None
    rows = []
    try:
        has_tpl = bool(await chstore.get_template(uid))
    except Exception:
        has_tpl = False
    if has_tpl:
        rows.append([btn("📝 use my template", "ap_cap:tpl", color=GREEN)])
    rows.append([btn("✍️ write a caption", "ap_cap:txt", color=BLUE), btn("⏭ no caption", "ap_cap:none", color=BLUE)])
    await client.send_message(
        cid,
        bq(_head(sch) + "\n" + sc("🕒 ") + f"<b>{html.escape(fmt_time(sch['run_at'], tz))}</b> ({html.escape(tz_name(tz))})\n\n" +
           sc("📝 which caption should the post get?") +
           ("" if has_tpl else "\n" + sc("tip: save a template once with /template and it is one tap here."))),
        reply_markup=_cancel_kb(*rows))


async def _ask_buttons(client, uid: int, cid: int, mode: str, skip_cb: str) -> None:
    _ud(uid)["await"] = mode
    example = "Watch now - https://example.com/anime\nJoin - t.me/yourchannel | Support - t.me/yourgroup"
    await client.send_message(
        cid,
        bq(sc("🔘 send the inline buttons, one row per line:\n") + f"<code>{html.escape(example)}</code>\n" +
           sc("format: text - link   ·   two buttons in one row: separate them with  |\n"
              f"up to {chnl.MAX_ROWS} rows, {chnl.MAX_PER_ROW} buttons per row.")),
        reply_markup=_cancel_kb([btn("⏭ no buttons", skip_cb, color=BLUE)]))


async def _confirm(client, uid: int, cid: int) -> None:
    ud = _ud(uid)
    sch = ud["sch"]
    tz = await _tz_of(uid)
    ud["await"] = None
    await _cleanup(client, cid, sch)
    sch["msgs"] = []
    try:
        sent = await send_html(client, cid, sch["jpg"], sch["html"], chnl.to_markup(sch["rows"]))
    except RPCError as e:
        log.warning("schedule preview failed: %s", e)
        await client.send_message(cid, bq(sc("⚠️ telegram refused that caption / buttons: ") + html.escape(str(e)[:150]) +
                                          "\n" + sc("choose the caption again.")))
        await _ask_caption(client, uid, cid)
        return
    sch["msgs"] = [m.id for m in sent]
    note = ""
    if not config.MONGO_URI:
        note = "\n\n" + sc("⚠️ no database (MONGO_URI) is set: scheduled posts are lost when the server restarts.")
    ctl = await client.send_message(
        cid,
        bq(sc("👆 this is exactly how the post will look.\n") + sc("post to ") + f"<b>{html.escape(sch['chat_title'])}</b>" +
           sc(" on ") + f"<b>{html.escape(fmt_time(sch['run_at'], tz))}</b> ({html.escape(tz_name(tz))})?" + note),
        reply_markup=kb([btn("✅ schedule it", "ap_ok", color=GREEN)],
                        [btn("🕒 change time", "ap_time", color=BLUE), btn("📝 change caption", "ap_caption", color=BLUE)],
                        [btn("✖️ cancel", "ap_cancel", color=RED)]))
    sch["ctl"] = ctl.id


async def _save_job(client, uid: int, cid: int, ctl_msg) -> None:
    ud = _ud(uid)
    sch = ud.get("sch") or {}
    if not sch.get("run_at") or not sch.get("chat_id"):
        await cmds._edit(ctl_msg, bq(sc("session expired. press ⏰ schedule under a poster again.")))
        return
    if sch.get("saving"):
        return
    sch["saving"] = True
    try:
        tz = await _tz_of(uid)
        if sch["run_at"] <= time.time() + 30:
            await cmds._edit(ctl_msg, bq(sc("⌛ that time has just passed. pick a new one.")))
            await _ask_time(client, uid, cid)
            return
        await cmds._edit(ctl_msg, bq(sc("🔎 checking my rights…")))
        info, err = await chnl.verify(client, uid, sch["chat_id"])
        if err:
            await cmds._edit(ctl_msg, err, _cancel_kb())
            return
        job_id = uuid.uuid4().hex[:8]
        await chstore.add_job(dict(
            _id=job_id, uid=uid, chat_id=info["id"], chat_title=info["title"], run_at=float(sch["run_at"]),
            jpg=sch["jpg"], html=sch["html"], rows=[[list(b) for b in row] for row in sch["rows"]],
            title=sch["title"], style=sch.get("style")))
        await _cleanup(client, cid, sch)
        await cmds._edit(
            ctl_msg,
            bq(sc("✅ scheduled!\n") + f"🎬 <b>{html.escape(sch['title'])}</b>\n" + sc("📣 ") +
               f"<b>{html.escape(info['title'])}</b>\n" + sc("🕒 ") + f"<b>{html.escape(fmt_time(sch['run_at'], tz))}</b> "
               f"({html.escape(tz_name(tz))})\n\n" + sc("i will message you when it is posted.")),
            kb([btn("📋 my schedules", "ap_list", color=BLUE)]))
        ud.pop("sch", None)
        ud["await"] = None
    except Exception:
        log.exception("saving the scheduled post failed")
        await cmds._edit(ctl_msg, bq(sc("⚠️ i couldn't save it (database problem?). try again in a moment.")),
                         kb([btn("🔁 try again", "ap_ok", color=GREEN), btn("✖️ cancel", "ap_cancel", color=RED)]))
    finally:
        sch["saving"] = False


# ======================================================================
# /schedules
# ======================================================================
async def _list_view(uid: int):
    jobs = await chstore.list_jobs(uid)
    tz = await _tz_of(uid)
    if not jobs:
        return (bq(sc("⏰ you have no scheduled posts.\nmake a poster and press ⏰ schedule under it.")),
                kb([btn("✖️ close", "ap_close", color=RED)]))
    lines, rows = [], []
    for i, j in enumerate(jobs, 1):
        state = sc(" · sending…") if j.get("status") == "running" else ""
        lines.append(f"{i}. <b>{html.escape(j['title'])}</b> ➜ {html.escape(j['chat_title'])}\n    🕒 "
                     f"{html.escape(fmt_time(j['run_at'], tz))}{state}")
        rows.append(btn(f"🗑 {i}", f"ap_del:{j['_id']}", raw=True, color=RED))
    keyboard = [rows[k:k + 5] for k in range(0, len(rows), 5)]
    keyboard.append([btn("✖️ close", "ap_close", color=BLUE)])
    return (bq(sc("⏰ your scheduled posts ") + f"({html.escape(tz_name(tz))})\n\n" + "\n".join(lines) + "\n\n" +
               sc("tap 🗑 + number to delete one.")), kb(*keyboard))


async def cmd_schedules(client, message):
    uid, cid = message.from_user.id, message.chat.id
    _ud(uid)["await"] = None
    try:
        text, markup = await _list_view(uid)
    except Exception:
        log.exception("listing scheduled posts failed")
        await message.reply_text(bq(sc("⚠️ i couldn't read your schedules (database problem?).")))
        return
    await message.reply_text(text, reply_markup=markup)


# ======================================================================
# /template
# ======================================================================
async def _tpl_view(uid: int):
    tpl = await chstore.get_template(uid)
    head = sc("📝 caption template\n") + sc("one caption with placeholders - they are filled in for every title:\n")
    help_ = _placeholder_help()
    if not tpl:
        return (bq(head + help_ + "\n\n" + sc("you have no template yet.")),
                kb([btn("✏️ create template", "ap_tset", color=GREEN)], [btn("✖️ close", "ap_close", color=RED)]))
    return (bq(head + help_ + "\n\n" + sc("your template:")) + "\n" + tpl["html"],
            kb([btn("👁 preview", "ap_tprev", color=BLUE), btn("✏️ replace", "ap_tset", color=GREEN)],
               [btn("🗑 delete", "ap_tdel", color=RED), btn("✖️ close", "ap_close", color=BLUE)]))


async def cmd_template(client, message):
    uid = message.from_user.id
    _ud(uid)["await"] = None
    try:
        text, markup = await _tpl_view(uid)
    except Exception:
        log.exception("reading the template failed")
        await message.reply_text(bq(sc("⚠️ i couldn't read your template (database problem?).")))
        return
    await message.reply_text(text, reply_markup=markup, **NO_PREVIEW)


async def _preview_template(client, uid: int, cid: int, tpl: dict) -> bool:
    """show the template filled with the current title (or a sample). False = telegram refused it"""
    a = _ud(uid).get("anime")
    values = fields(a) if a is not None else SAMPLE
    try:
        await client.send_message(cid, fill(tpl["html"], values), parse_mode=HTML, **NO_PREVIEW,
                                  reply_markup=chnl.to_markup(tpl.get("rows")))
    except RPCError as e:
        await client.send_message(cid, bq(sc("⚠️ telegram refused that text / buttons: ") + html.escape(str(e)[:150])))
        return False
    return True


async def _finish_template(client, uid: int, cid: int, rows) -> None:
    ud = _ud(uid)
    tpl = dict(html=ud.pop("tpl_tmp", ""), rows=[[list(b) for b in row] for row in rows])
    ud["await"] = None
    if not await _preview_template(client, uid, cid, tpl):
        ud["tpl_tmp"] = tpl["html"]
        await _ask_buttons(client, uid, cid, "aptplb", "ap_tskip")
        return
    await chstore.set_template(uid, tpl)
    await client.send_message(cid, bq(sc("✅ template saved. it is offered under ⏰ schedule and 📣 post to channel.")),
                              reply_markup=kb([btn("📝 open /template", "ap_tview", color=BLUE)]))


# ======================================================================
# /timezone
# ======================================================================
async def _ask_tz(client, uid: int, cid: int) -> None:
    tz = await _tz_of(uid)
    _ud(uid)["await"] = "aptz"
    await client.send_message(
        cid,
        bq(sc("🌍 send your time zone.\nnow: ") + f"<b>{html.escape(tz_name(tz))}</b> · " +
           f"{datetime.now(tz).strftime('%H:%M')}\n\n" + sc("examples:\n") +
           "<code>Asia/Kolkata</code>  <code>Europe/London</code>  <code>America/New_York</code>\n"
           "<code>UTC+5:30</code>  <code>UTC-4</code>"),
        reply_markup=_cancel_kb())


async def _set_tz(client, uid: int, cid: int, text: str) -> bool:
    tz = get_tz(text)
    if tz is None:
        await client.send_message(cid, bq(sc("😕 i don't know that time zone. try Asia/Kolkata, Europe/London or UTC+5:30.")),
                                  reply_markup=_cancel_kb())
        return False
    name = tz_name(tz)
    await chstore.set_tz(uid, name)
    ud = _ud(uid)
    ud["await"] = None
    await client.send_message(cid, bq(sc("✅ time zone: ") + f"<b>{html.escape(name)}</b> · " +
                                      sc("it is ") + f"<b>{datetime.now(tz).strftime('%H:%M')}</b> " + sc("there now.")))
    sch = ud.get("sch")
    if sch and sch.get("chat_id") and not sch.get("run_at"):      # in the middle of ⏰ schedule: ask the time again
        await _ask_time(client, uid, cid)
    return True


async def cmd_timezone(client, message):
    uid, cid = message.from_user.id, message.chat.id
    _ud(uid)["await"] = None
    args = message.command[1:] if getattr(message, "command", None) else []
    if args:
        await _set_tz(client, uid, cid, " ".join(args))
    else:
        await _ask_tz(client, uid, cid)


# ======================================================================
# text typed by the user
# ======================================================================
async def on_mode_text(client, message):
    uid, cid = message.from_user.id, message.chat.id
    ud = _ud(uid)
    mode = ud.get("await")
    sch = ud.get("sch")

    if mode == "aptz":
        await _set_tz(client, uid, cid, message.text or "")

    elif mode == "aptime":
        if not sch:
            ud["await"] = None
            return
        tz = await _tz_of(uid)
        ts, err = parse_when(message.text or "", tz)
        if err:
            await message.reply_text(bq(sc("⚠️ " + err + ".\ntry: ") + "<code>in 2h</code>  <code>21:30</code>  "
                                        "<code>tomorrow 8:00</code>"), reply_markup=_cancel_kb())
            return
        sch["run_at"] = ts
        if sch.get("html") is not None:                           # changing the time after the preview
            await _confirm(client, uid, cid)
        else:
            await _ask_caption(client, uid, cid)

    elif mode == "aptext":
        if not sch:
            ud["await"] = None
            return
        sch["html"] = fill(_html_of(message), fields(sch["anime"]))
        await _ask_buttons(client, uid, cid, "apbtns", "ap_skipbtn")

    elif mode == "apbtns":
        if not sch:
            ud["await"] = None
            return
        rows, errors = chnl.parse_buttons(message.text or "")
        if errors or not rows:
            bad = "\n".join("• " + html.escape(e) for e in errors[:5]) or sc("no button found")
            await message.reply_text(bq(sc("⚠️ i couldn't use these lines:\n") + bad + "\n\n" +
                                        sc("one per line:  text - link   (two in a row: text - link | text - link)")),
                                     reply_markup=_cancel_kb([btn("⏭ no buttons", "ap_skipbtn", color=BLUE)]))
            return
        sch["rows"] = rows
        await _confirm(client, uid, cid)

    elif mode == "aptpl":
        h = _html_of(message).strip()
        if not h:
            return
        if len(h) > TPL_MAX:
            await message.reply_text(bq(sc(f"😕 that template is too long (max {TPL_MAX} characters with formatting).")),
                                     reply_markup=_cancel_kb())
            return
        ud["tpl_tmp"] = h
        await _ask_buttons(client, uid, cid, "aptplb", "ap_tskip")

    elif mode == "aptplb":
        rows, errors = chnl.parse_buttons(message.text or "")
        if errors or not rows:
            bad = "\n".join("• " + html.escape(e) for e in errors[:5]) or sc("no button found")
            await message.reply_text(bq(sc("⚠️ i couldn't use these lines:\n") + bad + "\n\n" +
                                        sc("one per line:  text - link   (two in a row: text - link | text - link)")),
                                     reply_markup=_cancel_kb([btn("⏭ no buttons", "ap_tskip", color=BLUE)]))
            return
        await _finish_template(client, uid, cid, rows)


# ======================================================================
# inline buttons  (callback data "ap_...")
# ======================================================================
async def on_cb(client, cq):
    uid, msg = cq.from_user.id, cq.message
    cid = msg.chat.id
    ud = _ud(uid)
    data = cq.data
    cmd, _, arg = data.partition(":")
    sch = ud.get("sch")

    if cmd == "ap_new":
        await cq.answer()
        await start_schedule(client, uid, cid)

    elif cmd == "ap_cancel":
        await cq.answer()
        ud["await"] = None
        ud.pop("tpl_tmp", None)
        await _cleanup(client, cid, ud.pop("sch", None))
        await cmds._edit(msg, bq(sc("✖️ cancelled.")))

    elif cmd == "ap_close":
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)

    elif cmd == "ap_ch":
        if not sch:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        c = await chstore.get_channel(uid, int(arg))
        if not c:
            await cq.answer(sc("channel not found"), show_alert=True)
            return
        await cq.answer()
        sch.update(chat_id=c["id"], chat_title=c["title"])
        await cmds._safe_delete(client, cid, msg.id)
        await _ask_time(client, uid, cid)

    elif cmd == "ap_q":                                            # quick time buttons: in N minutes
        if not sch:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        sch["run_at"] = time.time() + int(arg) * 60
        await cmds._safe_delete(client, cid, msg.id)
        if sch.get("html") is not None:
            await _confirm(client, uid, cid)
        else:
            await _ask_caption(client, uid, cid)

    elif cmd == "ap_tz":
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)
        await _ask_tz(client, uid, cid)

    elif cmd == "ap_time":                                         # from the preview: pick another time
        if not sch:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)
        sch.pop("run_at", None)                                    # (the time zone button re-asks the time when it is empty)
        await _ask_time(client, uid, cid)

    elif cmd == "ap_caption":                                      # from the preview: pick another caption
        if not sch:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)
        sch.pop("html", None)
        await _cleanup(client, cid, sch)
        sch["msgs"] = []
        await _ask_caption(client, uid, cid)

    elif cmd == "ap_cap":
        if not sch:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)
        if arg == "tpl":
            tpl = await chstore.get_template(uid)
            if not tpl:
                await _ask_caption(client, uid, cid)
                return
            sch["html"] = fill(tpl["html"], fields(sch["anime"]))
            sch["rows"] = [[tuple(b) for b in row] for row in (tpl.get("rows") or [])]
            await _confirm(client, uid, cid)
        elif arg == "none":
            sch["html"], sch["rows"] = "", []
            await _confirm(client, uid, cid)
        else:
            ud["await"] = "aptext"
            await client.send_message(
                cid,
                bq(sc("✍️ send the caption. bold, italic, spoiler, links, quotes - your formatting stays.\n"
                      "placeholders are filled in too:\n") + _placeholder_help()),
                reply_markup=_cancel_kb())

    elif cmd == "ap_skipbtn":
        if not sch:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        sch["rows"] = []
        await cmds._safe_delete(client, cid, msg.id)
        await _confirm(client, uid, cid)

    elif cmd == "ap_ok":
        await cq.answer(sc("saving…"))
        await _save_job(client, uid, cid, msg)

    # ---- /schedules
    elif cmd == "ap_list":
        await cq.answer()
        text, markup = await _list_view(uid)
        await cmds._edit(msg, text, markup)

    elif cmd == "ap_del":
        ok = await chstore.remove_job(uid, arg)
        await cq.answer(sc("🗑 deleted") if ok else sc("not found (maybe it is being posted right now)"), show_alert=not ok)
        text, markup = await _list_view(uid)
        await cmds._edit(msg, text, markup)

    # ---- /template
    elif cmd == "ap_tview":
        await cq.answer()
        text, markup = await _tpl_view(uid)
        await client.send_message(cid, text, reply_markup=markup, **NO_PREVIEW)

    elif cmd == "ap_tset":
        await cq.answer()
        ud["await"] = "aptpl"
        await client.send_message(
            cid,
            bq(sc("✍️ send your caption template. format it as you like (bold, italic, spoiler, links, quotes) and put "
                  "these placeholders where the data should go:\n") + _placeholder_help() + "\n\n" + sc("example:\n") +
               "<code>🎬 {title}\n⭐ {score}/10 · {year}\n🎭 {genres}\n\n{synopsis}\n\n{hashtags}</code>"),
            reply_markup=_cancel_kb())

    elif cmd == "ap_tskip":
        if not ud.get("tpl_tmp"):
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)
        await _finish_template(client, uid, cid, [])

    elif cmd == "ap_tprev":
        tpl = await chstore.get_template(uid)
        if not tpl:
            await cq.answer(sc("no template"), show_alert=True)
            return
        await cq.answer()
        await _preview_template(client, uid, cid, tpl)

    elif cmd == "ap_tdel":
        await chstore.set_template(uid, None)
        await cq.answer(sc("🗑 template deleted"))
        text, markup = await _tpl_view(uid)
        await cmds._edit(msg, text, markup)

    elif cmd == "ap_usetpl":                                       # 📝 button of the 📣 post to channel flow
        pf = ud.get("pf")
        a = ud.get("anime")
        tpl = await chstore.get_template(uid)
        if not pf or a is None or not tpl:
            await cq.answer(sc("session expired"), show_alert=True)
            return
        await cq.answer()
        await cmds._safe_delete(client, cid, msg.id)
        pf["text"], pf["ents"] = await _html_to_entities(client, cid, fill(tpl["html"], fields(a)))
        rows = [[tuple(b) for b in row] for row in (tpl.get("rows") or [])]
        if rows:
            pf["rows"] = rows
        if pf.get("review") or rows:
            await chnl._show_preview(client, uid, cid)
        else:
            await chnl._ask_buttons(client, uid, cid)

    else:
        await cq.answer()


# ======================================================================
# the background loop
# ======================================================================
async def _dm(client, uid: int, text: str, markup=None) -> None:
    try:
        await client.send_message(uid, text, reply_markup=markup)
    except Exception:
        log.info("could not message user %s", uid)


async def _run_job(client, job: dict) -> None:
    uid, jid = job["uid"], job["_id"]
    tz = await _tz_of(uid)
    title = html.escape(job["title"])
    channel = html.escape(job["chat_title"])
    if time.time() - job["run_at"] > config.SCHEDULE_GRACE_HOURS * 3600:
        await chstore.finish_job(jid)
        await _dm(client, uid, bq(sc("⌛ a scheduled post was skipped because the bot was offline for too long:\n") +
                                  f"🎬 <b>{title}</b> ➜ <b>{channel}</b>\n" + sc("🕒 ") + html.escape(fmt_time(job["run_at"], tz))))
        return
    try:
        info, err = await chnl.verify(client, uid, job["chat_id"])
        if err:                                                     # lost rights / removed from the channel: no retry
            await chstore.finish_job(jid)
            await _dm(client, uid, bq(sc("⚠️ i could not post your scheduled post:\n") + f"🎬 <b>{title}</b> ➜ <b>{channel}</b>") +
                      "\n" + err)
            return
        rows = [[tuple(b) for b in row] for row in (job.get("rows") or [])]
        sent = await send_html(client, info["id"], job["jpg"], job.get("html") or "", chnl.to_markup(rows))
    except FloodWait as e:
        wait = int(getattr(e, "value", 30)) + 5
        await chstore.retry_job(jid, time.time() + wait, job.get("tries", 0))
        log.warning("scheduled post %s: flood wait %ss", jid, wait)
        return
    except Exception as e:                                          # network hiccup / telegram error: a few more tries
        tries = job.get("tries", 0) + 1
        log.warning("scheduled post %s failed (try %s/%s): %s", jid, tries, MAX_TRIES, e)
        if tries < MAX_TRIES:
            await chstore.retry_job(jid, time.time() + 300, tries)
            return
        await chstore.finish_job(jid)
        await _dm(client, uid, bq(sc("⚠️ a scheduled post failed after 3 tries:\n") + f"🎬 <b>{title}</b> ➜ <b>{channel}</b>\n" +
                                  html.escape(str(e)[:150])))
        return
    await chstore.finish_job(jid)
    link = chnl.post_link(info["id"], info["username"], sent[-1].id)
    await _dm(client, uid, bq(sc("✅ scheduled post sent\n") + f"🎬 <b>{title}</b> ➜ <b>{html.escape(info['title'])}</b>"),
              kb([btn("🔗 open post", url=link, color=BLUE)]))
    log.info("scheduled post %s sent to %s", jid, info["id"])


async def _scheduler(client) -> None:
    await asyncio.sleep(5)
    log.info("scheduler running (every %ss)", TICK)
    while True:
        try:
            await chstore.release_stale(time.time(), STALE)
            while True:
                job = await chstore.claim_due_job(time.time())
                if not job:
                    break
                try:
                    await _run_job(client, job)
                finally:
                    del job                                         # let the picture's memory go
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("scheduler step failed (will try again)")
        await asyncio.sleep(TICK)


async def _keepalive() -> None:
    """Render's free service sleeps after 15 min without web traffic: ask our own public URL every 10 min"""
    url = config.RENDER_EXTERNAL_URL
    if not (config.KEEPALIVE and url):
        return
    import httpx
    await asyncio.sleep(120)
    log.info("keep-alive ping to %s every %ss", url, KEEPALIVE_EVERY)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        while True:
            try:
                await http.get(url)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("keep-alive failed: %s", e)
            await asyncio.sleep(KEEPALIVE_EVERY)


def start(app) -> None:
    _tasks.append(asyncio.create_task(_scheduler(app)))
    _tasks.append(asyncio.create_task(_keepalive()))


async def stop() -> None:
    for t in _tasks:
        t.cancel()
    await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks.clear()


# ======================================================================
# wiring
# ======================================================================
def _mode_filter(*modes):
    return filters.create(lambda _, __, m: bool(m.from_user) and cmds.STATE[m.from_user.id].get("await") in modes)


def _safe(func):
    """a command that crashes is written to the log WITH the reason and the user gets a short message (no silence)"""
    async def wrapper(client, message):
        try:
            await func(client, message)
        except Exception as e:
            log.exception("/%s failed", func.__name__.removeprefix("cmd_"))
            try:
                await message.reply_text(bq(sc("⚠️ something went wrong: ") + html.escape(f"{type(e).__name__}: {str(e)[:120]}")))
            except Exception:
                pass
    return wrapper


def register(app):
    """call BEFORE cmds.register() (same reason as chnl.register)"""
    app.add_handler(MessageHandler(_safe(cmd_schedules), filters.command("schedules") & filters.private))
    app.add_handler(MessageHandler(_safe(cmd_template), filters.command("template") & filters.private))
    app.add_handler(MessageHandler(_safe(cmd_timezone), filters.command("timezone") & filters.private))
    app.add_handler(MessageHandler(on_mode_text, filters.private & filters.create(cmds._not_command) & _mode_filter(*MODES)))
    app.add_handler(CallbackQueryHandler(on_cb, filters.regex(r"^ap_")))

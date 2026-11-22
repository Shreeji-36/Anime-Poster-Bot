from __future__ import annotations

import inspect
import re

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from pyrogram.enums import ButtonStyle
except Exception:                                   # old build without button styles
    ButtonStyle = None

_STYLE_OK = ButtonStyle is not None and "style" in inspect.signature(InlineKeyboardButton.__init__).parameters

_SC = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ")
_TAGS = re.compile(r"(<[^>]+>|&[a-zA-Z0-9#]+;)")


def sc(text: str) -> str:
    """'Hello <b>World</b>' -> 'ʜᴇʟʟᴏ <b>ᴡᴏʀʟᴅ</b>'  (tags and &entities untouched)."""
    return "".join(p if i % 2 else p.lower().translate(_SC) for i, p in enumerate(_TAGS.split(text)))


def _no_preview() -> dict:
    """Keyword arguments that switch the link preview off.

    Kurigram 2.2 REMOVED disable_web_page_preview (calling it raises TypeError, so the command silently does nothing)
    and uses link_preview_options instead; older builds only know the old name. Ask the installed library."""
    try:
        from pyrogram import Client
        params = inspect.signature(Client.send_message).parameters
        if "link_preview_options" in params:
            from pyrogram.types import LinkPreviewOptions
            return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
        if "disable_web_page_preview" in params:
            return {"disable_web_page_preview": True}
    except Exception:
        pass
    return {}


NO_PREVIEW = _no_preview()          # use:  await client.send_message(chat, text, **NO_PREVIEW)


def bq(text: str, expandable: bool = False) -> str:
    return f"<blockquote{' expandable' if expandable else ''}>{text}</blockquote>"


# colours
BLUE, GREEN, RED = "primary", "success", "danger"


def btn(text: str, data: str | None = None, *, url: str | None = None, color: str | None = None,
        raw: bool = False) -> InlineKeyboardButton:
    """raw=True keeps the label exactly as given (anime titles, user text)."""
    label = text if raw else sc(text)
    kw = {}
    if color and _STYLE_OK:
        kw["style"] = getattr(ButtonStyle, color.upper())
    if url:
        return InlineKeyboardButton(label, url=url, **kw)
    return InlineKeyboardButton(label, callback_data=data, **kw)


def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([list(r) for r in rows])

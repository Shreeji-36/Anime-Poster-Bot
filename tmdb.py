"""Movies + TV shows from TMDB, converted into the SAME record shape the poster code already uses for AniList.

Why: every poster style reads one dict (title, genres, description, score, cover, banner ...). If a movie / tv show
is delivered in that exact shape, the existing styles draw it without any change.

The record keeps AniList's words on purpose (format "MOVIE" / "TV", status "FINISHED" ...) because the templates
were written for them.  Two extra keys are added:  "_kind" ("movie" | "tv")  and  "_tmdb" (ids needed later).

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

import config

log = logging.getLogger("tmdb")

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
HEADERS = {"User-Agent": "anime-poster-bot/1.0"}
KINDS = ("movie", "tv")


class TMDBError(Exception):
    pass


# ----------------------------------------------------------------------
# low level
# ----------------------------------------------------------------------
def _key() -> str:
    k = (config.TMDB_API_KEY or "").strip()
    if not k:
        raise TMDBError("TMDB_API_KEY is not set")
    return k


async def _get(path: str, **params) -> dict:
    """GET one TMDB endpoint. Accepts the normal v3 api key and the long v4 'read access token'."""
    key = _key()
    headers = dict(HEADERS)
    if key.startswith("eyJ"):                                   # v4 token
        headers["Authorization"] = f"Bearer {key}"
    else:                                                       # v3 api key
        params["api_key"] = key
    last: Exception | None = None
    async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
        for attempt in range(3):
            try:
                r = await client.get(f"{API}{path}", params=params)
                if r.status_code == 404:
                    raise TMDBError("not found")
                if r.status_code == 401:
                    raise TMDBError("TMDB rejected the api key (401) - check TMDB_API_KEY")
                if r.status_code == 429:                        # too many requests: wait and retry
                    try:
                        wait = min(10.0, float(r.headers.get("Retry-After", "2")))
                    except ValueError:
                        wait = 2.0
                    await asyncio.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except TMDBError:
                raise
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(1 + attempt)
    raise TMDBError(f"TMDB is not answering: {last}")


def _img(path: str | None, size: str) -> str | None:
    return f"{IMG}{size}{path}" if path else None


def _latin(s: str) -> bool:
    """True when the text only uses latin letters (the poster fonts cannot draw hangul / arabic / thai ...)."""
    return bool(s) and all(ord(c) < 0x250 for c in s)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# search  ->  same shape as dataset.search_anime():  [{id, title, year, format}]
# ----------------------------------------------------------------------
async def search(kind: str, query: str, limit: int = 6) -> list[dict]:
    if kind not in KINDS:
        raise ValueError(kind)
    data = await _get(f"/search/{kind}", query=query, include_adult="false", language="en-US", page=1)
    out = []
    for m in (data.get("results") or [])[:limit]:
        title = (m.get("title") or m.get("name") or m.get("original_title") or m.get("original_name") or "Unknown")
        date = m.get("release_date") or m.get("first_air_date") or ""
        out.append(dict(id=m["id"], title=title, year=_int(date[:4]),
                        format="Movie" if kind == "movie" else "TV"))
    return out


# ----------------------------------------------------------------------
# one title  ->  AniList-shaped record
# ----------------------------------------------------------------------
_STATUS = {"Released": "FINISHED", "Ended": "FINISHED", "Returning Series": "RELEASING",
           "Canceled": "CANCELLED", "In Production": "NOT_YET_RELEASED", "Post Production": "NOT_YET_RELEASED",
           "Planned": "NOT_YET_RELEASED", "Rumored": "NOT_YET_RELEASED", "Pilot": "NOT_YET_RELEASED"}


async def load_raw(kind: str, tmdb_id: int) -> dict:
    if kind not in KINDS:
        raise ValueError(kind)
    d = await _get(f"/{kind}/{int(tmdb_id)}", append_to_response="credits,keywords,external_ids", language="en-US")
    return to_raw(kind, d)


def to_raw(kind: str, d: dict) -> dict:
    title = d.get("title") or d.get("name") or d.get("original_title") or d.get("original_name") or "Unknown"
    original = d.get("original_title") or d.get("original_name") or ""
    lang = d.get("original_language") or ""

    date = d.get("release_date") or d.get("first_air_date") or ""
    y, m, dd = (date.split("-") + ["", "", ""])[:3]
    year = _int(y)

    votes, avg = d.get("vote_count") or 0, d.get("vote_average") or 0
    score = round(float(avg) * 10) if votes and avg else None          # 0-10  ->  0-100 like AniList

    if kind == "movie":
        runtime, episodes = d.get("runtime") or None, None
    else:
        runtime = ((d.get("episode_run_time") or [None])[0]
                   or (d.get("last_episode_to_air") or {}).get("runtime") or None)
        episodes = d.get("number_of_episodes") or None

    companies = [c["name"] for c in (d.get("production_companies") or []) if c.get("name")]
    if not companies and kind == "tv":
        companies = [n["name"] for n in (d.get("networks") or []) if n.get("name")]

    kw = d.get("keywords") or {}
    kw_items = (kw.get("keywords") if kind == "movie" else kw.get("results")) or []
    keywords = [k["name"].title() for k in kw_items if k.get("name") and len(k["name"]) <= 18][:8]

    cast = ((d.get("credits") or {}).get("cast") or [])[:4]
    edges = [dict(role="LEAD" if i == 0 else "SUPPORTING",
                  node=dict(name=dict(full=c.get("name") or "?"), image=dict(large=_img(c.get("profile_path"), "w185"))))
             for i, c in enumerate(cast)]

    ext = d.get("external_ids") or {}
    return dict(
        id=int(d["id"]), idMal=None,
        title=dict(english=title,
                   romaji=original if original and original != title and _latin(original) else "",
                   native=original if lang in ("ja", "zh", "cn") and original else ""),
        description=d.get("overview") or d.get("tagline") or "",
        genres=[g["name"] for g in (d.get("genres") or []) if g.get("name")],
        tags=[dict(name=k, isMediaSpoiler=False, rank=90 - i) for i, k in enumerate(keywords)],
        averageScore=score, meanScore=score, popularity=int(d.get("popularity") or 0),
        seasonYear=year, season=None,
        startDate=dict(year=year, month=_int(m), day=_int(dd)),
        format="MOVIE" if kind == "movie" else "TV",
        episodes=episodes, duration=runtime,
        status=_STATUS.get(d.get("status") or "", "FINISHED"),
        isAdult=bool(d.get("adult")),
        coverImage=dict(extraLarge=_img(d.get("poster_path"), "w780"), large=_img(d.get("poster_path"), "w500"),
                        color=None),
        bannerImage=_img(d.get("backdrop_path"), "w1280"),
        studios=dict(nodes=[dict(name=companies[0])] if companies else []),
        characters=dict(edges=edges),
        _kind=kind,
        _tmdb=dict(kind=kind, id=int(d["id"]), tvdb_id=_int(ext.get("tvdb_id")), imdb_id=ext.get("imdb_id"),
                   lang=lang),
    )


# ----------------------------------------------------------------------
# more posters / banners for the next / back buttons
# ----------------------------------------------------------------------
async def list_images(kind: str, tmdb_id: int, what: str, lang: str | None = None) -> list[tuple[str, str]]:
    """what = 'poster' | 'banner'  ->  [(label, url)], best rated first."""
    langs = "en,null" + (f",{lang}" if lang and lang != "en" else "")
    data = await _get(f"/{kind}/{int(tmdb_id)}/images", include_image_language=langs)
    key, size = ("posters", "w780") if what == "poster" else ("backdrops", "w1280")
    items = sorted(data.get(key) or [],
                   key=lambda i: (float(i.get("vote_average") or 0), int(i.get("vote_count") or 0)), reverse=True)
    out = []
    for n, it in enumerate(items, 1):
        url = _img(it.get("file_path"), size)
        if not url:
            continue
        tag = (it.get("iso_639_1") or "no text").upper()
        out.append((f"TMDB {what} #{n} · {tag}", url))
    return out


# ----------------------------------------------------------------------
# fanart.tv (transparent art): tv shows by TVDB id, movies by TMDB id
# ----------------------------------------------------------------------
async def fanart_record(client: httpx.AsyncClient, raw: dict) -> dict:
    key = (config.FANART_API_KEY or "").strip()
    t = raw.get("_tmdb") or {}
    if not key or not t:
        return {}
    if t.get("kind") == "movie":
        url = f"https://webservice.fanart.tv/v3/movies/{t['id']}"
    else:
        if not t.get("tvdb_id"):
            return {}
        url = f"https://webservice.fanart.tv/v3/tv/{t['tvdb_id']}"
    r = await client.get(url, params={"api_key": key})
    return r.json() if r.status_code == 200 else {}
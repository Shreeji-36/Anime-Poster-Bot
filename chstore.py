from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile

import config

log = logging.getLogger("chstore")

_users: dict[int, dict] = {}                 # cache (the json backend keeps everything here)
_lock = asyncio.Lock()
_coll = None                                 # mongo collection (users)
_jobs = None                                 # mongo collection (scheduled posts)
_job_mem: dict[str, dict] = {}               # scheduled posts when there is no MongoDB (json file)
_ready = False


def _blank() -> dict:
    # "kinds" = the /settings of each type: {"movie": {"brand", "tpl", "style", "theme", "button"}, "tv": {...}, "anime": {...}}
    # a key that is missing in a type means "use the default" (the global brand / theme / template of the old commands)
    return {"channels": [], "brand": "", "theme": "", "tpl": None, "tz": "", "kinds": {}}


async def _init() -> None:
    global _ready, _coll, _jobs
    if _ready:
        return
    _ready = True
    if config.MONGO_URI:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            db = AsyncIOMotorClient(config.MONGO_URI)[config.MONGO_DB]
            _coll = db["poster_users"]
            _jobs = db["poster_jobs"]
            try:
                await _jobs.create_index([("status", 1), ("run_at", 1)])
                await _jobs.create_index("uid")
            except Exception as e:                       # an index is only a speed-up
                log.warning("could not create the jobs index: %s", e)
            log.info("channels + scheduled posts are stored in MongoDB (%s)", config.MONGO_DB)
            return
        except Exception as e:
            log.warning("MONGO_URI is set but MongoDB can't be used (%s) -> json file instead", e)
    path = config.DATA_DIR / "users.json"
    try:
        if path.exists():
            _users.update({int(k): v for k, v in json.loads(path.read_text("utf-8")).items()})
    except Exception as e:
        log.warning("could not read %s: %s", path, e)
    log.info("channels are stored in %s", path)
    jpath = config.DATA_DIR / "jobs.json"
    try:
        if jpath.exists():
            for k, v in json.loads(jpath.read_text("utf-8")).items():
                v["jpg"] = base64.b64decode(v.get("jpg") or "")
                _job_mem[k] = v
    except Exception as e:
        log.warning("could not read %s: %s", jpath, e)


def _write_json() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config.DATA_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in _users.items()}, f, ensure_ascii=False)
    os.replace(tmp, config.DATA_DIR / "users.json")


async def _get(uid: int) -> dict:
    await _init()
    if uid not in _users:
        doc = await _coll.find_one({"_id": uid}) if _coll is not None else None
        if doc:
            doc.pop("_id", None)
        _users[uid] = {**_blank(), **(doc or {})}
    return _users[uid]


async def _save(uid: int) -> None:
    async with _lock:
        if _coll is not None:
            await _coll.replace_one({"_id": uid}, {"_id": uid, **_users[uid]}, upsert=True)
        else:
            await asyncio.to_thread(_write_json)


# ---------------------------------------------------------------- reading
async def channels(uid: int) -> list[dict]:
    return list((await _get(uid))["channels"])


async def get_channel(uid: int, chat_id: int) -> dict | None:
    return next((c for c in await channels(uid) if c["id"] == chat_id), None)


def _kd(d: dict, kind: str | None) -> dict:
    return (d.get("kinds") or {}).get(kind) or {} if kind else {}


async def get_kind(uid: int, kind: str) -> dict:
    """the /settings of one type ('movie' | 'tv' | 'anime') - a copy, {} when nothing was set"""
    return dict(_kd(await _get(uid), kind))


async def set_kind(uid: int, kind: str, key: str, value) -> None:
    """save one /settings value of a type; value None = forget it (the default is used again)"""
    d = await _get(uid)
    kinds = d.setdefault("kinds", {})
    kd = kinds.setdefault(kind, {})
    if value is None:
        kd.pop(key, None)
    else:
        kd[key] = value
    await _save(uid)


async def get_brand(uid: int, kind: str | None = None) -> str:
    """the watermark text of a poster. With a kind: that type's own text if it has one ('' = none on purpose),
    otherwise the default one"""
    d = await _get(uid)
    kd = _kd(d, kind)
    if "brand" in kd:
        return kd["brand"] or ""
    return d.get("brand") or ""


async def get_theme(uid: int, kind: str | None = None) -> str:
    """the poster colour ('' = original colours); a type's own colour first, then the one used last time"""
    d = await _get(uid)
    kd = _kd(d, kind)
    if "theme" in kd:
        return kd["theme"] or ""
    return d.get("theme") or ""


# ---------------------------------------------------------------- writing
async def add_channel(uid: int, ch: dict) -> None:
    d = await _get(uid)
    d["channels"] = [c for c in d["channels"] if c["id"] != ch["id"]]
    d["channels"].append({"id": ch["id"], "title": ch["title"], "username": ch.get("username")})
    await _save(uid)


async def remove_channel(uid: int, chat_id: int) -> bool:
    d = await _get(uid)
    before = len(d["channels"])
    d["channels"] = [c for c in d["channels"] if c["id"] != chat_id]
    if len(d["channels"]) == before:
        return False
    await _save(uid)
    return True


async def set_brand(uid: int, text: str, kind: str | None = None) -> None:
    if kind:
        await set_kind(uid, kind, "brand", text)
        return
    (await _get(uid))["brand"] = text
    await _save(uid)


async def set_theme(uid: int, key: str, kind: str | None = None) -> None:
    if kind:
        await set_kind(uid, kind, "theme", key)
        return
    (await _get(uid))["theme"] = key
    await _save(uid)



# ---------------------------------------------------------------- caption template + time zone (autopost.py)
async def get_template(uid: int, kind: str | None = None) -> dict | None:
    """{"html": caption with {placeholders}, "rows": [[label, url], ...]} or None.
    With a kind: the template of that type (/settings), else the default one of /template."""
    d = await _get(uid)
    return _kd(d, kind).get("tpl") or d.get("tpl") or None


async def set_template(uid: int, tpl: dict | None, kind: str | None = None) -> None:
    if kind:
        await set_kind(uid, kind, "tpl", tpl)
        return
    (await _get(uid))["tpl"] = tpl
    await _save(uid)


async def get_tz(uid: int) -> str:
    return (await _get(uid)).get("tz") or ""


async def set_tz(uid: int, name: str) -> None:
    (await _get(uid))["tz"] = name
    await _save(uid)


# ---------------------------------------------------------------- scheduled posts
# one job = {"_id", "uid", "chat_id", "chat_title", "run_at" (unix seconds), "jpg" (bytes), "html", "rows", "title",
#            "style", "status": "pending" | "running", "tries", "claimed"}
def _write_jobs() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config.DATA_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({k: {**v, "jpg": base64.b64encode(v.get("jpg") or b"").decode()} for k, v in _job_mem.items()}, f)
    os.replace(tmp, config.DATA_DIR / "jobs.json")


async def _save_jobs() -> None:
    async with _lock:
        await asyncio.to_thread(_write_jobs)


def _light(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "jpg"}


async def add_job(job: dict) -> None:
    await _init()
    job = {**job, "status": "pending", "tries": 0}
    if _jobs is not None:
        await _jobs.insert_one(job)
    else:
        _job_mem[job["_id"]] = job
        await _save_jobs()


async def count_jobs(uid: int) -> int:
    await _init()
    if _jobs is not None:
        return await _jobs.count_documents({"uid": uid})
    return sum(1 for j in _job_mem.values() if j["uid"] == uid)


async def list_jobs(uid: int) -> list[dict]:
    """the user's scheduled posts, soonest first (without the picture)"""
    await _init()
    if _jobs is not None:
        return await _jobs.find({"uid": uid}, {"jpg": 0}).sort("run_at", 1).to_list(200)
    return sorted((_light(j) for j in _job_mem.values() if j["uid"] == uid), key=lambda j: j["run_at"])


async def remove_job(uid: int, job_id: str) -> bool:
    await _init()
    if _jobs is not None:
        return (await _jobs.delete_one({"_id": job_id, "uid": uid, "status": "pending"})).deleted_count > 0
    j = _job_mem.get(job_id)
    if not j or j["uid"] != uid or j["status"] != "pending":
        return False
    del _job_mem[job_id]
    await _save_jobs()
    return True


async def claim_due_job(now: float) -> dict | None:
    """take ONE job that is due (marked 'running' in the same step, so a second instance can never post it twice)"""
    await _init()
    if _jobs is not None:
        from pymongo import ReturnDocument
        return await _jobs.find_one_and_update(
            {"status": "pending", "run_at": {"$lte": now}}, {"$set": {"status": "running", "claimed": now}},
            sort=[("run_at", 1)], return_document=ReturnDocument.AFTER)
    due = [j for j in _job_mem.values() if j["status"] == "pending" and j["run_at"] <= now]
    if not due:
        return None
    job = min(due, key=lambda j: j["run_at"])
    job["status"], job["claimed"] = "running", now
    await _save_jobs()
    return dict(job)


async def finish_job(job_id: str) -> None:
    await _init()
    if _jobs is not None:
        await _jobs.delete_one({"_id": job_id})
    elif _job_mem.pop(job_id, None) is not None:
        await _save_jobs()


async def retry_job(job_id: str, run_at: float, tries: int) -> None:
    """a send failed for a temporary reason: try again later"""
    await _init()
    if _jobs is not None:
        await _jobs.update_one({"_id": job_id}, {"$set": {"status": "pending", "run_at": run_at, "tries": tries}})
    elif job_id in _job_mem:
        _job_mem[job_id].update(status="pending", run_at=run_at, tries=tries)
        await _save_jobs()


async def release_stale(now: float, older_than: float) -> None:
    """a job stuck in 'running' (the bot was killed while sending) goes back to 'pending'"""
    await _init()
    if _jobs is not None:
        await _jobs.update_many({"status": "running", "claimed": {"$lt": now - older_than}}, {"$set": {"status": "pending"}})
        return
    changed = False
    for j in _job_mem.values():
        if j["status"] == "running" and j.get("claimed", 0) < now - older_than:
            j["status"], changed = "pending", True
    if changed:
        await _save_jobs()

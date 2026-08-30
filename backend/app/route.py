"""Callsign -> scheduled origin/destination.

Stage 1: free community routeset at adsbdb.com (no key), for every callsign.
Stage 2: an optional keyed schedule provider (see schedule.py), tried ONLY for
callsigns adsbdb couldn't resolve, quota-guarded, and cached to disk so restarts
don't re-spend the quota.

`get(callsign)` -> route dict or None.  `meta(callsign)` -> schedule times dict or None.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import time
from pathlib import Path

import httpx

from .config import settings
from .schedule import build_provider

log = logging.getLogger("route")

_ADSBDB = "https://api.adsbdb.com/v0/callsign/{cs}"
_HEADERS = {"User-Agent": "india-domestic-flight-tracker/0.1"}

_routes: dict[str, dict] = {}       # callsign -> {dep, arr, dep_city, arr_city, dep_country, arr_country}
_meta: dict[str, dict] = {}         # callsign -> {sched_dep, sched_arr, est_arr, sched_status, gate, terminal}
_noroute: dict[str, float] = {}     # callsign -> ts of last unsuccessful adsbdb lookup
_sched_tried: dict[str, float] = {} # callsign -> ts of last schedule-API attempt

_queued: set[str] = set()
_q: asyncio.Queue[str] = asyncio.Queue(maxsize=4000)
_sched_queued: set[str] = set()
_sched_q: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=4000)

_ADSBDB_RETRY = 1800.0    # retry an adsbdb miss after 30 min
_SCHED_RETRY = 21600.0    # retry a schedule-API miss after 6 h
_ADSBDB_SPACING = 0.4

_sched_hits: list[float] = []   # timestamps of schedule-API calls, for the quota window
_CACHE = Path(settings.route_cache_path)
_dirty = False


def _norm(cs: str | None) -> str:
    return (cs or "").strip().upper()


def get(callsign: str | None) -> dict | None:
    return _routes.get(_norm(callsign))


def meta(callsign: str | None) -> dict | None:
    return _meta.get(_norm(callsign))


def schedule_summary(callsign: str | None) -> dict | None:
    """Schedule times for a callsign, with a computed arrival delay, or None."""
    m = _meta.get(_norm(callsign))
    if not m:
        return None
    delay = None
    if m.get("sched_arr") and m.get("est_arr"):
        try:
            delay = round(
                (_dt.datetime.fromisoformat(m["est_arr"]) - _dt.datetime.fromisoformat(m["sched_arr"])).total_seconds()
                / 60
            )
        except ValueError:
            delay = None
    return {
        "sched_dep": m.get("sched_dep"),
        "sched_arr": m.get("sched_arr"),
        "est_arr": m.get("est_arr"),
        "delay_min": delay,
        "sched_status": m.get("sched_status"),
        "gate": m.get("gate"),
        "terminal": m.get("terminal"),
    }


def enqueue(callsign: str | None) -> None:
    cs = _norm(callsign)
    if not cs or cs in _routes or cs in _queued:
        return
    last = _noroute.get(cs)
    if last is not None and (time.time() - last) < _ADSBDB_RETRY:
        return
    _queued.add(cs)
    try:
        _q.put_nowait(cs)
    except asyncio.QueueFull:
        _queued.discard(cs)


def enqueue_schedule(callsign: str | None, flight_no: str | None) -> None:
    """Stage 2 -- only call for callsigns still unresolved after classify()."""
    cs = _norm(callsign)
    if not cs or not flight_no or cs in _routes or cs in _sched_queued:
        return
    last = _sched_tried.get(cs)
    if last is not None and (time.time() - last) < _SCHED_RETRY:
        return
    _sched_queued.add(cs)
    try:
        _sched_q.put_nowait((cs, flight_no))
    except asyncio.QueueFull:
        _sched_queued.discard(cs)


def _quota_ok() -> bool:
    now = time.time()
    _sched_hits[:] = [t for t in _sched_hits if now - t < 86400]
    per_hour = sum(1 for t in _sched_hits if now - t < 3600)
    return per_hour < settings.schedule_max_per_hour and len(_sched_hits) < settings.schedule_max_per_day


# ---------- disk cache ----------

def _load_cache() -> None:
    global _dirty
    try:
        raw = json.loads(_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    _routes.update(raw.get("routes", {}))
    _meta.update(raw.get("meta", {}))
    log.info("loaded %d cached routes from %s", len(_routes), _CACHE)


def _save_cache() -> None:
    global _dirty
    if not _dirty:
        return
    try:
        _CACHE.write_text(json.dumps({"routes": _routes, "meta": _meta}), encoding="utf-8")
        _dirty = False
    except OSError as e:
        log.debug("route cache save failed: %s", e)


_load_cache()


# ---------- stage 1: adsbdb ----------

def _parse_adsbdb(payload: dict) -> dict | None:
    fr = (payload.get("response") or {}).get("flightroute")
    if not isinstance(fr, dict):
        return None
    o = fr.get("origin") or {}
    d = fr.get("destination") or {}
    dep = o.get("iata_code") or o.get("icao_code")
    arr = d.get("iata_code") or d.get("icao_code")
    if not dep or not arr:
        return None
    return {
        "dep": dep,
        "arr": arr,
        "dep_city": o.get("municipality"),
        "arr_city": d.get("municipality"),
        "dep_country": o.get("country_iso_name"),
        "arr_country": d.get("country_iso_name"),
    }


async def resolver_loop() -> None:
    global _dirty
    client = httpx.AsyncClient(timeout=12.0, headers=_HEADERS)
    log.info("route resolver started (adsbdb)")
    last_save = time.time()
    try:
        while True:
            cs = await _q.get()
            _queued.discard(cs)
            if cs in _routes:
                continue
            try:
                r = await client.get(_ADSBDB.format(cs=cs))
                if r.status_code == 200:
                    parsed = _parse_adsbdb(r.json())
                    if parsed:
                        _routes[cs] = parsed
                        _dirty = True
                    else:
                        _noroute[cs] = time.time()
                elif r.status_code == 429:
                    await asyncio.sleep(5.0)
                    enqueue(cs)
                    continue
                else:
                    _noroute[cs] = time.time()
            except httpx.HTTPError:
                pass
            if time.time() - last_save > 90:
                _save_cache()
                last_save = time.time()
            await asyncio.sleep(_ADSBDB_SPACING)
    except asyncio.CancelledError:
        await client.aclose()
        _save_cache()
        raise


# ---------- stage 2: keyed schedule provider ----------

async def schedule_loop() -> None:
    global _dirty
    provider = build_provider()
    if provider is None:
        log.info("no schedule provider configured -- '•••' flights stay unresolved")
        return
    log.info("schedule loop started (%s)", provider.name)
    last_save = time.time()
    try:
        while True:
            cs, flight_no = await _sched_q.get()
            _sched_queued.discard(cs)
            if cs in _routes:
                continue
            if not _quota_ok():
                _sched_tried[cs] = time.time()  # try again after the retry window
                await asyncio.sleep(10.0)
                continue
            _sched_hits.append(time.time())
            _sched_tried[cs] = time.time()
            try:
                res = await provider.route(flight_no)
            except Exception as e:  # noqa: BLE001
                log.debug("schedule provider error for %s: %s", flight_no, e)
                res = None
            if res == "ratelimited":
                await asyncio.sleep(30.0)
            elif res:
                _routes[cs] = {
                    k: res.get(k)
                    for k in ("dep", "arr", "dep_city", "arr_city", "dep_country", "arr_country")
                }
                _meta[cs] = {
                    k: res.get(k)
                    for k in ("sched_dep", "sched_arr", "est_arr", "sched_status", "gate", "terminal")
                }
                _dirty = True
                log.info("schedule: %s -> %s-%s", flight_no, res.get("dep"), res.get("arr"))
            if time.time() - last_save > 60:
                _save_cache()
                last_save = time.time()
            await asyncio.sleep(settings.schedule_spacing)
    except asyncio.CancelledError:
        try:
            await provider.aclose()
        except Exception:
            pass
        _save_cache()
        raise


def stats() -> dict:
    now = time.time()
    return {
        "resolved": len(_routes),
        "unknown": len(_noroute),
        "queued": _q.qsize(),
        "sched_queued": _sched_q.qsize(),
        "sched_calls_1h": sum(1 for t in _sched_hits if now - t < 3600),
        "sched_calls_24h": len(_sched_hits),
    }

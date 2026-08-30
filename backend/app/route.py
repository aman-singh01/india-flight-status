"""Callsign -> scheduled origin/destination, from the free community API adsbdb.com.

A background loop drains a queue of callsigns seen by the ingest layer, ~2-3 req/s,
and caches the result in memory. `get()` is a plain dict lookup for the rest of the
app; unknown callsigns simply return None until the resolver catches up.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

log = logging.getLogger("route")

_API = "https://api.adsbdb.com/v0/callsign/{cs}"
_HEADERS = {"User-Agent": "india-domestic-flight-tracker/0.1"}

_routes: dict[str, dict] = {}       # callsign -> route dict
_noroute: dict[str, float] = {}     # callsign -> ts of last unsuccessful lookup
_queued: set[str] = set()
_q: asyncio.Queue[str] = asyncio.Queue(maxsize=4000)

_RETRY_AFTER = 1800.0   # re-try a callsign adsbdb didn't know after 30 min
_SPACING = 0.4          # seconds between adsbdb requests


def _norm(cs: str | None) -> str:
    return (cs or "").strip().upper()


def get(callsign: str | None) -> dict | None:
    return _routes.get(_norm(callsign))


def enqueue(callsign: str | None) -> None:
    cs = _norm(callsign)
    if not cs or cs in _routes or cs in _queued:
        return
    last = _noroute.get(cs)
    if last is not None and (time.time() - last) < _RETRY_AFTER:
        return
    _queued.add(cs)
    try:
        _q.put_nowait(cs)
    except asyncio.QueueFull:
        _queued.discard(cs)


def _parse(payload: dict) -> dict | None:
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
    client = httpx.AsyncClient(timeout=12.0, headers=_HEADERS)
    log.info("route resolver started")
    try:
        while True:
            cs = await _q.get()
            _queued.discard(cs)
            if cs in _routes:
                continue
            try:
                r = await client.get(_API.format(cs=cs))
                if r.status_code == 200:
                    parsed = _parse(r.json())
                    if parsed:
                        _routes[cs] = parsed
                    else:
                        _noroute[cs] = time.time()
                elif r.status_code == 429:
                    await asyncio.sleep(5.0)
                    enqueue(cs)
                    continue
                else:  # 404 / 5xx
                    _noroute[cs] = time.time()
            except httpx.HTTPError:
                pass  # transient; ingest will re-enqueue next cycle
            await asyncio.sleep(_SPACING)
    except asyncio.CancelledError:
        await client.aclose()
        raise


def stats() -> dict:
    return {"resolved": len(_routes), "unknown": len(_noroute), "queued": _q.qsize()}

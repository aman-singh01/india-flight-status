"""Schedule board: scheduled flights from airport FIDS, merged with the ADS-B feed.

`refresh_loop()` polls the configured FIDS scrapers every `fids_refresh` seconds.
`rows()` returns the current in-window scheduled flights (a time window around now,
so the board isn't cluttered with flights half a day away). `find(query)` looks one
up by flight number for `/api/status`.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .config import settings
from .fids import build_fids

log = logging.getLogger("board")

_rows: dict[str, dict] = {}  # hex ("fids-6E2361") -> normalized board row
_by_flightno: dict[str, dict] = {}  # "6E2361" -> row
_last_ok = 0.0


def rows() -> list[dict]:
    """Scheduled flights whose time is within the display window of now."""
    now = time.time()
    lo = now - settings.fids_window_behind * 3600
    hi = now + settings.fids_window_ahead * 3600
    out = []
    for r in _rows.values():
        ts = r.get("_sched_ts") or 0.0
        if ts and not (lo <= ts <= hi):
            continue
        out.append({k: v for k, v in r.items() if not k.startswith("_")})
    return out


def find(query: str) -> dict | None:
    q = query.upper().replace(" ", "").replace("-", "")
    r = _by_flightno.get(q)
    return {k: v for k, v in r.items() if not k.startswith("_")} if r else None


def healthy() -> bool:
    return bool(_rows) and (time.time() - _last_ok) < max(600.0, settings.fids_refresh * 3)


def stats() -> dict:
    return {"rows": len(_rows), "in_window": len(rows()), "last_ok_age_s": round(time.time() - _last_ok)}


async def refresh_loop() -> None:
    global _last_ok
    scrapers = build_fids(settings.fids_sources)
    if not scrapers:
        log.info("no FIDS sources configured")
        return
    log.info("FIDS board started (%s)", ", ".join(s.name for s in scrapers))
    try:
        while True:
            fresh: dict[str, dict] = {}
            for sc in scrapers:
                try:
                    got = await sc.fetch()
                except Exception as e:  # noqa: BLE001 -- one bad scraper mustn't kill the loop
                    log.warning("fids %s failed: %s", sc.name, e)
                    continue
                for row in got:
                    fresh[row["hex"]] = row
            if fresh:
                _rows.clear()
                _rows.update(fresh)
                _by_flightno.clear()
                _by_flightno.update({r["flight_no"]: r for r in fresh.values()})
                _last_ok = time.time()
                log.info("FIDS board: %d scheduled flights (%d in window)", len(_rows), len(rows()))
            await asyncio.sleep(settings.fids_refresh)
    except asyncio.CancelledError:
        for sc in scrapers:
            try:
                await sc._client.aclose()
            except Exception:
                pass
        raise


def merge(adsb_rows: list[dict]) -> list[dict]:
    """Union the live ADS-B domestic list with the scheduled board.

    A scheduled flight whose flight number matches a tracked aircraft enriches that
    row (route / gate / scheduled status) and keeps its live position; the rest of
    the board is appended as position-less rows.
    """
    by_no: dict[str, dict] = {}
    for f in adsb_rows:
        # a FIDS row is Delhi-only, so only match a live flight that touches Delhi
        # (or whose route we don't know yet) -- guards against a reused flight number
        # on an unrelated sector.
        if f.get("flight_no") and (not f.get("dep") or not f.get("arr") or "DEL" in (f["dep"], f["arr"])):
            by_no.setdefault(f["flight_no"].upper(), f)

    out = list(adsb_rows)
    for sched in rows():
        live = by_no.get((sched["flight_no"] or "").upper())
        if live is not None:
            _enrich(live, sched)
        else:
            out.append(sched)
    return out


def _enrich(live: dict, sched: dict) -> None:
    for k in ("dep", "arr", "dep_city", "arr_city"):
        if not live.get(k) and sched.get(k):
            live[k] = sched[k]
    if not live.get("schedule") and sched.get("schedule"):
        live["schedule"] = sched["schedule"]
    live.setdefault("route_src", sched.get("route_src"))
    live["scheduled_status"] = (sched.get("schedule") or {}).get("sched_status")

"""Schedule board: scheduled flights from airport FIDS, merged with the ADS-B feed.

Rows come from two kinds of source, each identified by a string id:
  - "fids:del"   the built-in Delhi scraper (`refresh_loop()`), self-refreshing
  - "push:BOM"   pushed to `POST /ingest/fids` by a scraper on a residential IP
                 (the big airports block datacenter IPs), expires after
                 `fids_push_ttl` seconds without a fresh push.

`rows()` flattens every source, drops stale ones, and keeps only flights within a
time window of now. `merge()` unions the result with the live ADS-B list.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .config import settings
from .fids import build_fids

log = logging.getLogger("board")

# source id -> {"rows": {hex: row}, "at": epoch}
_sources: dict[str, dict] = {}


def _put(source_id: str, rows_list: list[dict]) -> None:
    _sources[source_id] = {
        "rows": {r["hex"]: r for r in rows_list if r.get("hex")},
        "at": time.time(),
    }


def ingest(source_id: str, rows_list: list[dict]) -> int:
    """Replace one source's rows (used by the push endpoint). Returns the count."""
    _put(source_id, rows_list)
    return len(_sources[source_id]["rows"])


def _live_sources() -> list[dict]:
    now = time.time()
    ttl = max(settings.fids_push_ttl, settings.fids_refresh * 3)
    return [s for s in _sources.values() if now - s["at"] < ttl]


def _all_rows() -> list[dict]:
    out = []
    for s in _live_sources():
        out.extend(s["rows"].values())
    return out


def rows() -> list[dict]:
    """Scheduled flights whose time is within the display window of now."""
    now = time.time()
    lo = now - settings.fids_window_behind * 3600
    hi = now + settings.fids_window_ahead * 3600
    out = []
    for r in _all_rows():
        ts = r.get("_sched_ts") or 0.0
        if ts and not (lo <= ts <= hi):
            continue
        out.append({k: v for k, v in r.items() if not k.startswith("_")})
    return out


def find(query: str) -> dict | None:
    q = query.upper().replace(" ", "").replace("-", "")
    for r in _all_rows():
        if (r.get("flight_no") or "").upper() == q:
            return {k: v for k, v in r.items() if not k.startswith("_")}
    return None


def healthy() -> bool:
    return bool(_live_sources())


def stats() -> dict:
    now = time.time()
    return {
        "rows": len(_all_rows()),
        "in_window": len(rows()),
        "sources": {
            sid: {"rows": len(s["rows"]), "age_s": round(now - s["at"])}
            for sid, s in sorted(_sources.items())
        },
    }


async def refresh_loop() -> None:
    scrapers = build_fids(settings.fids_sources)
    if not scrapers:
        log.info("no built-in FIDS scrapers configured (push ingest may still be used)")
        return
    log.info("FIDS board started (%s)", ", ".join(s.name for s in scrapers))
    try:
        while True:
            for sc in scrapers:
                try:
                    got = await sc.fetch()
                except Exception as e:  # noqa: BLE001 -- one bad scraper mustn't kill the loop
                    log.warning("fids %s failed: %s", sc.name, e)
                    continue
                if got:
                    _put(sc.name, got)
                    log.info("FIDS %s: %d scheduled flights", sc.name, len(got))
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

    A scheduled flight whose number matches a tracked aircraft *and shares an
    endpoint with it* enriches that row (route / gate / status) and keeps its live
    position; unmatched schedule rows are appended position-less.
    """
    by_no: dict[str, dict] = {}
    for f in adsb_rows:
        if f.get("flight_no"):
            by_no.setdefault(f["flight_no"].upper(), f)

    out = list(adsb_rows)
    for sched in rows():
        live = by_no.get((sched["flight_no"] or "").upper())
        if live is not None and _endpoints_ok(live, sched):
            _enrich(live, sched)
        else:
            out.append(sched)
    return out


def _endpoints_ok(live: dict, sched: dict) -> bool:
    ld, la = live.get("dep"), live.get("arr")
    if not ld or not la:
        return True  # live route unknown -> trust the schedule
    # same city pair only: a shared flight number on a different leg of a multi-leg
    # rotation (DEL-BLR vs BLR-CCU) must not cross-enrich.
    return {ld, la} == {sched.get("dep"), sched.get("arr")}


def _enrich(live: dict, sched: dict) -> None:
    for k in ("dep", "arr", "dep_city", "arr_city"):
        if not live.get(k) and sched.get(k):
            live[k] = sched[k]
    if not live.get("schedule") and sched.get("schedule"):
        live["schedule"] = sched["schedule"]
    live.setdefault("route_src", sched.get("route_src"))
    live["scheduled_status"] = (sched.get("schedule") or {}).get("sched_status")

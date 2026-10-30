from __future__ import annotations

import asyncio
import logging
import time

from . import db, metrics
from . import route as route_db
from .config import settings
from .domestic import airline_from_callsign, classify
from .sources import build_sources
from .store import store

log = logging.getLogger("ingest")

_feeds: dict[str, dict] = {}  # source name -> {aircraft, at, note}: what each feed gave last cycle


def record_feed(name: str, aircraft: int, note: str = "") -> None:
    _feeds[name] = {"aircraft": aircraft, "at": time.time(), "note": note}


def feed_stats() -> dict:
    """Per-source health for /api/health -- so a feed that silently returns nothing
    (a quota, a block) is visible instead of guessed at."""
    now = time.time()
    return {
        n: {"aircraft": f["aircraft"], "age_s": round(now - f["at"]), "note": f["note"]}
        for n, f in sorted(_feeds.items())
    }


async def _cycle(sources) -> None:
    merged: dict[str, dict] = {}
    for src in sources:
        try:
            items = await src.fetch()
        except Exception as e:  # noqa: BLE001 - one bad source shouldn't kill ingest
            log.warning("source %s failed: %s", src.name, e)
            record_feed(src.name, 0, f"error: {e.__class__.__name__}: {e}"[:160])
            continue
        record_feed(src.name, len(items), getattr(src, "note", ""))
        metrics.source_aircraft.labels(source=src.name).set(len(items))
        for it in items:
            merged[it["hex"]] = it  # later source in SOURCES wins on conflict

    store.upsert(list(merged.values()))
    store.prune()

    domestic = 0
    for t in store.all():
        if airline_from_callsign(t.callsign):  # only resolve routes for Indian carriers
            route_db.enqueue(t.callsign)
        t.klass = classify(
            {
                "callsign": t.callsign,
                "registration": t.registration,
                "lat": t.lat,
                "lon": t.lon,
                "alt_ft": t.alt_ft,
                "vs_fpm": t.vs_fpm,
                "track_deg": t.track_deg,
            },
            list(t.track),
        )
        if t.klass:
            domestic += 1
            # enqueue_schedule decides internally: route-gap fill, or (schedule_all)
            # every flight for scheduled times / gate / delay
            route_db.enqueue_schedule(t.callsign, t.klass.get("flight_no"))

    if settings.persist:
        await db.write_positions(store.all())

    rs = route_db.stats()
    metrics.ingest_cycles.inc()
    metrics.aircraft_tracked.set(len(store.all()))
    metrics.flights_domestic.set(domestic)
    metrics.routes_resolved.set(rs["resolved"])
    metrics.schedule_calls_24h.set(rs["sched_calls_24h"])
    log.info(
        "ingest: %d tracked, %d domestic  (routes %d, adsbdb-q %d, sched-q %d, sched calls %d/1h)",
        len(store.all()),
        domestic,
        rs["resolved"],
        rs["queued"],
        rs["sched_queued"],
        rs["sched_calls_1h"],
    )


async def run_ingest() -> None:
    sources = build_sources(settings.sources)
    log.info("ingest sources: %s", [s.name for s in sources])
    try:
        while True:
            try:
                await _cycle(sources)
            except Exception:
                log.exception("ingest cycle error")
            await asyncio.sleep(settings.poll_interval)
    except asyncio.CancelledError:
        for s in sources:
            try:
                await s.aclose()
            except Exception:
                pass
        raise

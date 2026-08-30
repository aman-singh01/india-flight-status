from __future__ import annotations

import asyncio
import logging

from . import db
from . import route as route_db
from .config import settings
from .domestic import classify
from .sources import build_sources
from .store import store

log = logging.getLogger("ingest")


async def _cycle(sources) -> None:
    merged: dict[str, dict] = {}
    for src in sources:
        try:
            items = await src.fetch()
        except Exception as e:  # noqa: BLE001 - one bad source shouldn't kill ingest
            log.warning("source %s failed: %s", src.name, e)
            continue
        for it in items:
            merged[it["hex"]] = it  # later source in SOURCES wins on conflict

    store.upsert(list(merged.values()))
    store.prune()

    domestic = 0
    for t in store.all():
        route_db.enqueue(t.callsign)
        t.klass = classify(
            {
                "callsign": t.callsign,
                "registration": t.registration,
                "lat": t.lat,
                "lon": t.lon,
                "alt_ft": t.alt_ft,
                "vs_fpm": t.vs_fpm,
            },
            list(t.track),
        )
        if t.klass:
            domestic += 1

    if settings.persist:
        await db.write_positions(store.all())

    rs = route_db.stats()
    log.info(
        "ingest: %d tracked, %d domestic  (routes %d/%d, %d queued)",
        len(store.all()), domestic, rs["resolved"], rs["resolved"] + rs["unknown"], rs["queued"],
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

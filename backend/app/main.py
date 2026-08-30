from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db, metrics, schemas
from . import route as route_db
from .config import settings
from .domestic import _airlines, _airports
from .ingest import run_ingest
from .models import flight_dict
from .status import lookup as status_lookup
from .store import store
from .ws import manager, push_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # don't log every grid request
log = logging.getLogger("app")


def domestic_flights() -> list[dict]:
    # only surface flights we can name a full route for; the rest (odd ferry
    # callsigns no provider has a route for) are hidden from the board / feed but
    # still reachable via /api/status/{flight_no}.
    out = (flight_dict(t) for t in store.all())
    return [f for f in out if f and f["dep"] and f["arr"]]


def payload() -> dict:
    return {"type": "flights", "ts": time.time(), "flights": domestic_flights()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    tasks = [
        asyncio.create_task(run_ingest()),
        asyncio.create_task(push_loop(max(2.0, settings.poll_interval), payload)),
        asyncio.create_task(route_db.resolver_loop()),
        asyncio.create_task(route_db.schedule_loop()),
    ]
    log.info("started (sources=%s, poll=%ss)", settings.sources, settings.poll_interval)
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db.close()


app = FastAPI(
    title="India Flight Status",
    version="0.1.0",
    summary="Live status of domestic flights over India, from public ADS-B data.",
    description=(
        "Every response is served straight from an in-memory store fed by a pluggable "
        "set of ADS-B sources. Routes come from the free adsbdb routeset, or a keyed "
        "schedule API when configured. See the repo README for architecture."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "flights", "description": "The live domestic-flight feed."},
        {"name": "status", "description": "Look up one flight by number / registration / hex."},
        {"name": "reference", "description": "Bundled airport & airline data, aggregate stats."},
        {"name": "ops", "description": "Health and ingest/resolver stats."},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=schemas.Health, tags=["ops"])
async def health():
    """Service health plus tracked/domestic counts and route-resolver progress."""
    return {
        "ok": True,
        "tracked": len(store.all()),
        "domestic": len(domestic_flights()),
        "sources": settings.sources,
        "routes": route_db.stats(),
    }


@app.get("/api/flights", response_model=schemas.FlightsResponse, tags=["flights"])
async def flights():
    """Every domestic flight currently in coverage with a resolvable origin & destination."""
    fl = domestic_flights()
    return {"count": len(fl), "flights": fl}


@app.get("/api/status/{query}", response_model=schemas.FlightStatus, tags=["status"])
async def flight_status(query: str):
    """Live status for a flight number (`6E203` / `AI2984` / `IGO203`), registration or ICAO hex.

    Works even for flights hidden from `/api/flights` (no resolvable route). Returns
    `found: false` with a reason when nothing in coverage matches.
    """
    return status_lookup(query)


@app.get("/api/flights/{hexid}", response_model=schemas.FlightDetail, tags=["flights"])
async def flight_detail(hexid: str):
    """One flight by ICAO hex, with its recent position trail."""
    t = store.get(hexid.lower())
    if t is None or not t.klass:
        raise HTTPException(status_code=404, detail="not a tracked domestic flight")
    d = flight_dict(t)
    assert d is not None
    d["track"] = [[round(ts), round(la, 5), round(lo, 5), al] for (ts, la, lo, al) in t.track]
    if settings.persist and len(d["track"]) < 5:
        d["track"] = await db.track_for(hexid.lower())
    return d


@app.get("/api/airports", response_model=schemas.AirportsResponse, tags=["reference"])
async def airports():
    """The bundled Indian airport list used for route inference and place names."""
    return {"count": len(_airports), "airports": _airports}


@app.get("/api/airlines", response_model=dict[str, schemas.AirlineInfo], tags=["reference"])
async def airlines():
    """Indian scheduled operators, keyed by ICAO callsign prefix."""
    return _airlines


@app.get("/api/stats", response_model=schemas.Stats, tags=["reference"])
async def stats():
    """Totals and a by-airline breakdown of the current domestic feed."""
    fl = domestic_flights()
    by_airline: dict[str, int] = {}
    airborne = 0
    for f in fl:
        by_airline[f["airline"]] = by_airline.get(f["airline"], 0) + 1
        if (f["alt_ft"] or 0) > 1000:
            airborne += 1
    return {
        "total": len(fl),
        "airborne": airborne,
        "by_airline": dict(sorted(by_airline.items(), key=lambda kv: -kv[1])),
    }


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await websocket.send_json(payload())
        while True:
            await websocket.receive_text()  # client pings; content ignored
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    body, content_type = metrics.render()
    return Response(body, media_type=content_type)


_frontend = Path(__file__).resolve().parent.parent.parent / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")

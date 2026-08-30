from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db
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


app = FastAPI(title="India Domestic Flight Tracker", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "tracked": len(store.all()),
        "domestic": len(domestic_flights()),
        "sources": settings.sources,
        "routes": route_db.stats(),
    }


@app.get("/api/flights")
async def flights() -> dict:
    fl = domestic_flights()
    return {"count": len(fl), "flights": fl}


@app.get("/api/status/{query}")
async def flight_status(query: str) -> dict:
    """Live status for a flight number (6E203 / AI2984 / IGO203), registration or hex."""
    return status_lookup(query)


@app.get("/api/flights/{hexid}")
async def flight_detail(hexid: str) -> dict:
    t = store.get(hexid.lower())
    if t is None or not t.klass:
        raise HTTPException(status_code=404, detail="not a tracked domestic flight")
    d = flight_dict(t)
    assert d is not None
    d["track"] = [[round(ts), round(la, 5), round(lo, 5), al] for (ts, la, lo, al) in t.track]
    if settings.persist and len(d["track"]) < 5:
        d["track"] = await db.track_for(hexid.lower())
    return d


@app.get("/api/airports")
async def airports() -> dict:
    return {"count": len(_airports), "airports": _airports}


@app.get("/api/airlines")
async def airlines() -> dict:
    return _airlines


@app.get("/api/stats")
async def stats() -> dict:
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


_frontend = Path(__file__).resolve().parent.parent.parent / "frontend"
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")

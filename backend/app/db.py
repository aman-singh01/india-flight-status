from __future__ import annotations

import time

import aiosqlite

from .config import settings

_db: aiosqlite.Connection | None = None
_last_write: dict[str, float] = {}


async def init() -> None:
    global _db
    if not settings.persist:
        return
    _db = await aiosqlite.connect(settings.db_path)
    await _db.execute(
        """CREATE TABLE IF NOT EXISTS positions(
               hex TEXT, callsign TEXT, ts REAL,
               lat REAL, lon REAL, alt_ft INTEGER, status TEXT)"""
    )
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_pos_hex_ts ON positions(hex, ts)")
    await _db.commit()


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


async def write_positions(tracked) -> None:
    if _db is None:
        return
    now = time.time()
    rows = []
    for t in tracked:
        if not t.klass:
            continue
        if now - _last_write.get(t.hex, 0.0) < 20.0:
            continue
        _last_write[t.hex] = now
        rows.append((t.hex, t.callsign, now, t.lat, t.lon, t.alt_ft, t.klass["status"]))
    if rows:
        await _db.executemany(
            "INSERT INTO positions(hex,callsign,ts,lat,lon,alt_ft,status) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        await _db.commit()


async def track_for(hexid: str, limit: int = 1500) -> list[list]:
    if _db is None:
        return []
    cur = await _db.execute(
        "SELECT ts,lat,lon,alt_ft FROM positions WHERE hex=? ORDER BY ts DESC LIMIT ?",
        (hexid, limit),
    )
    rows = await cur.fetchall()
    return [[round(r[0]), round(r[1], 5), round(r[2], 5), r[3]] for r in reversed(rows)]

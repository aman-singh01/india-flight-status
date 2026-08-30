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
    await _db.execute("""CREATE TABLE IF NOT EXISTS positions(
               hex TEXT, callsign TEXT, ts REAL,
               lat REAL, lon REAL, alt_ft INTEGER, track_deg REAL, status TEXT)""")
    try:  # migrate older DBs
        await _db.execute("ALTER TABLE positions ADD COLUMN track_deg REAL")
    except Exception:
        pass
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_pos_hex_ts ON positions(hex, ts)")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_pos_ts ON positions(ts)")
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
        rows.append((t.hex, t.callsign, now, t.lat, t.lon, t.alt_ft, t.track_deg, t.klass["status"]))
    if rows:
        await _db.executemany(
            "INSERT INTO positions(hex,callsign,ts,lat,lon,alt_ft,track_deg,status) "
            "VALUES (?,?,?,?,?,?,?,?)",
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


async def span() -> dict:
    """Oldest / newest position timestamp and row count in the history table."""
    if _db is None:
        return {"from": None, "to": None, "rows": 0}
    cur = await _db.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM positions")
    lo, hi, n = await cur.fetchone()
    return {"from": round(lo) if lo else None, "to": round(hi) if hi else None, "rows": n or 0}


async def snapshot(at: float, window: float = 240.0) -> list[dict]:
    """Each aircraft's most recent stored position at or before `at` (within `window` s).

    SQLite quirk relied on: with a bare column + MAX() and GROUP BY, the other
    columns come from the row holding the max.
    """
    if _db is None:
        return []
    cur = await _db.execute(
        """SELECT hex, callsign, MAX(ts) AS ts, lat, lon, alt_ft, track_deg
           FROM positions WHERE ts <= ? AND ts >= ?
           GROUP BY hex""",
        (at, at - window),
    )
    rows = await cur.fetchall()
    return [
        {
            "hex": h,
            "callsign": (c or "").strip() or None,
            "ts": round(t),
            "lat": round(la, 5),
            "lon": round(lo, 5),
            "alt_ft": al,
            "track_deg": round(td) if td is not None else None,
        }
        for (h, c, t, la, lo, al, td) in rows
    ]

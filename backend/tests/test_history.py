import time

import pytest

from app import db


@pytest.fixture()
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, "persist", True)
    monkeypatch.setattr(db.settings, "db_path", str(tmp_path / "t.db"))
    db._db = None
    db._last_write.clear()
    await db.init()
    yield
    await db.close()


async def _insert(hexid, ts, lat, lon, alt, trk=None):
    await db._db.execute(
        "INSERT INTO positions(hex,callsign,ts,lat,lon,alt_ft,track_deg,status) " "VALUES (?,?,?,?,?,?,?,?)",
        (hexid, hexid.upper(), ts, lat, lon, alt, trk, "domestic"),
    )
    await db._db.commit()


async def test_span_empty(temp_db):
    s = await db.span()
    assert s == {"from": None, "to": None, "rows": 0}


async def test_span_and_snapshot(temp_db):
    t0 = time.time()
    await _insert("a1", t0 - 300, 20.0, 78.0, 30000)
    await _insert("a1", t0 - 60, 20.5, 78.5, 34000, trk=200)  # newer for a1
    await _insert("a2", t0 - 120, 12.0, 77.0, 5000)
    await _insert("a3", t0 - 5000, 28.0, 77.0, 36000)  # far in the past

    s = await db.span()
    assert s["rows"] == 4
    assert s["from"] == round(t0 - 5000) and s["to"] == round(t0 - 60)

    snap = await db.snapshot(t0, window=240)
    by = {a["hex"]: a for a in snap}
    assert set(by) == {"a1", "a2"}  # a3 is outside the 240s window
    assert by["a1"]["alt_ft"] == 34000  # the newer a1 row
    assert by["a1"]["track_deg"] == 200
    assert by["a2"]["track_deg"] is None


async def test_snapshot_no_db(monkeypatch):
    monkeypatch.setattr(db, "_db", None)
    assert await db.snapshot(time.time()) == []
    assert (await db.span())["rows"] == 0

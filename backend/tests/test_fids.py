import time

from app import board
from app.fids.base import icao_callsign, ist_to_utc_iso, normalize_pushed, phase_for, resolve_airport
from app.fids.delhi import _normalize


def _row(**over):
    base = {
        "FLIGHTNUMBER": "6E 2361",
        "airline_name": "INDIGO AIRLINES",
        "AIRPORT_DESCRIPTION": "Varanasi",
        "SCHEDULED_TIME": "15:24",
        "FLIGHTDATE": "2026-08-31",
        "estimated_date_display": "2026-08-31",
        "FLIGHT_STATUS_DESCRIPTION": "On Time",
        "GATE_BELT": "T21",
        "terminal": "T2",
    }
    base.update(over)
    return base


def test_resolve_airport():
    assert resolve_airport("Varanasi")[0] == "VNS"
    assert resolve_airport("Bengaluru Intl Airport")[0] == "BLR"
    assert resolve_airport("Chennai International Airport")[0] == "MAA"
    iata, name = resolve_airport("Nowhere-ville")
    assert iata is None and name == "Nowhere-ville"
    assert resolve_airport(None) == (None, None)


def test_ist_to_utc_iso():
    assert ist_to_utc_iso("2026-08-31", "15:24") == "2026-08-31T09:54:00+00:00"
    assert ist_to_utc_iso("bad", "x") is None


def test_icao_callsign():
    assert icao_callsign("6E2361") == "IGO2361"
    assert icao_callsign("AI2412") == "AIC2412"
    assert icao_callsign("ZZ99") is None  # unknown airline prefix


def test_normalize_departure():
    r = _normalize(_row(), arriving=False)
    assert r["flight_no"] == "6E2361"
    assert r["callsign"] == "IGO2361"
    assert r["dep"] == "DEL" and r["arr"] == "VNS"
    assert r["phase"] == "Scheduled"
    assert r["position"] is False and r["lat"] is None
    assert r["schedule"]["sched_dep"] == "2026-08-31T09:54:00+00:00"
    assert r["schedule"]["gate"] == "T21"
    assert r["source"] == "fids:del"


def test_normalize_arrival_phase():
    assert _normalize(_row(FLIGHT_STATUS_DESCRIPTION="On Time"), arriving=True)["phase"] == "En route"
    assert _normalize(_row(FLIGHT_STATUS_DESCRIPTION="Arrived"), arriving=True)["phase"] == "Landed"
    a = _normalize(_row(AIRPORT_DESCRIPTION="Pune"), arriving=True)
    assert a["dep"] == "PNQ" and a["arr"] == "DEL"


def test_normalize_status_mapping():
    assert _normalize(_row(FLIGHT_STATUS_DESCRIPTION="Cancelled"), arriving=False)["phase"] == "Cancelled"
    assert _normalize(_row(FLIGHT_STATUS_DESCRIPTION="Now Boarding"), arriving=False)["phase"] == "Boarding"
    assert _normalize(_row(FLIGHT_STATUS_DESCRIPTION="Departed"), arriving=False)["phase"] == "Departed"
    assert _normalize(_row(FLIGHT_STATUS_DESCRIPTION="Not Operating"), arriving=False) is None
    assert _normalize(_row(FLIGHTNUMBER=""), arriving=False) is None


def _seed_board(rows):
    board._sources.clear()
    board.ingest("test", rows)


def test_board_rows_window(monkeypatch):
    now = time.time()
    monkeypatch.setattr(board.settings, "fids_window_behind", 2.0)
    monkeypatch.setattr(board.settings, "fids_window_ahead", 6.0)
    _seed_board(
        [
            {"hex": "fids-A", "flight_no": "6E1", "_sched_ts": now + 3600},  # in
            {"hex": "fids-B", "flight_no": "6E2", "_sched_ts": now + 50000},  # too far ahead
            {"hex": "fids-C", "flight_no": "6E3", "_sched_ts": now - 10000},  # too far behind
        ]
    )
    got = {r["flight_no"] for r in board.rows()}
    assert got == {"6E1"}
    assert all(not k.startswith("_") for r in board.rows() for k in r)  # private keys stripped
    board._sources.clear()


def test_board_merge_enriches_and_appends(monkeypatch):
    monkeypatch.setattr(board.settings, "fids_window_behind", 999.0)
    monkeypatch.setattr(board.settings, "fids_window_ahead", 999.0)
    now = time.time()
    _seed_board(
        [
            {
                "hex": "fids-6E100",
                "flight_no": "6E100",
                "dep": "DEL",
                "arr": "BOM",
                "dep_city": "Delhi",
                "arr_city": "Mumbai",
                "schedule": {"gate": "T21"},
                "route_src": "fids",
                "phase": "Scheduled",
                "phase_detail": "x",
                "_sched_ts": now,
            },
            {
                "hex": "fids-6E200",
                "flight_no": "6E200",
                "dep": "DEL",
                "arr": "BLR",
                "phase": "Boarding",
                "phase_detail": "y",
                "position": False,
                "_sched_ts": now,
            },
        ]
    )
    # 6E100 has no known route -> matches; 6E200 is a live BLR-CCU flight sharing the
    # number of a DEL board row but NOT sharing an endpoint -> must NOT be enriched.
    adsb = [
        {"flight_no": "6E100", "hex": "abc123", "dep": None, "arr": None, "lat": 19.0},
        {"flight_no": "6E200", "hex": "def456", "dep": "BLR", "arr": "CCU", "lat": 15.0},
    ]
    out = board.merge(adsb)
    live = next(f for f in out if f["hex"] == "abc123")
    assert live["dep"] == "DEL" and live["arr"] == "BOM"  # enriched from the board
    unrelated = next(f for f in out if f["hex"] == "def456")
    assert unrelated["dep"] == "BLR" and "scheduled_status" not in unrelated  # left alone
    assert any(f["hex"] == "fids-6E200" for f in out)  # the DEL 6E200 board row still appended
    assert len(out) == 3


def test_phase_for_is_place_aware():
    assert phase_for("Boarding", False, "Mumbai") == ("Boarding", "boarding at Mumbai")
    assert phase_for("Arrived", True, "Bengaluru") == ("Landed", "landed at Bengaluru")
    assert phase_for("On Time", True, "Chennai") == ("En route", "inbound to Chennai")
    assert phase_for("Not Operating", False, "Delhi")[0] is None


def test_normalize_pushed():
    row = normalize_pushed(
        "BOM",
        {
            "flight_no": "6E 5301",
            "airline": "6E",
            "direction": "departure",
            "other": "Delhi",
            "sched_time": "14:30",
            "sched_date": "2026-09-02",
            "status": "Boarding",
            "gate": "45",
            "terminal": "T2",
        },
    )
    assert row["flight_no"] == "6E5301"
    assert row["callsign"] == "IGO5301"
    assert row["airline_icao"] == "IGO"
    assert row["dep"] == "BOM" and row["arr"] == "DEL"
    assert row["dep_city"] == "Mumbai" and row["arr_city"] == "Delhi"
    assert row["phase"] == "Boarding"
    assert "boarding at Mumbai" in row["phase_detail"] and "gate 45" in row["phase_detail"]
    assert row["schedule"]["sched_dep"] == "2026-09-02T09:00:00+00:00"  # 14:30 IST -> 09:00 UTC
    assert row["position"] is False
    assert row["source"] == "push:BOM"


def test_normalize_pushed_arrival_and_drops():
    a = normalize_pushed(
        "BLR",
        {
            "flight_no": "AI505",
            "direction": "arrival",
            "other": "DEL",
            "sched_time": "08:00",
            "status": "En Route",
        },
    )
    assert a["dep"] == "DEL" and a["arr"] == "BLR" and a["phase"] == "En route"
    assert normalize_pushed("BOM", {"flight_no": "", "direction": "departure", "sched_time": "1"}) is None
    assert (
        normalize_pushed(
            "BOM",
            {"flight_no": "6E1", "direction": "departure", "sched_time": "1", "status": "Not Operating"},
        )
        is None
    )


async def test_ingest_fids_endpoint(monkeypatch):
    from app import main
    from app.schemas import FidsIngest

    monkeypatch.setattr(main.settings, "fids_ingest_token", "secret")
    payload = FidsIngest(
        airport="BOM",
        flights=[
            {
                "flight_no": "6E777",
                "direction": "departure",
                "other": "Delhi",
                "sched_time": "10:00",
                "status": "On Time",
                "gate": "12",
            }
        ],
    )
    r = await main.ingest_fids(payload, authorization="Bearer secret")
    assert r == {"airport": "BOM", "accepted": 1, "dropped": 0}
    assert any(x["flight_no"] == "6E777" for x in board.rows())

    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):  # wrong token
        await main.ingest_fids(payload, authorization="Bearer nope")
    monkeypatch.setattr(main.settings, "fids_ingest_token", "")
    with pytest.raises(HTTPException):  # disabled
        await main.ingest_fids(payload, authorization="Bearer secret")

import time

from app import board
from app.fids.base import icao_callsign, ist_to_utc_iso, resolve_airport
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


def test_board_rows_window(monkeypatch):
    now = time.time()
    monkeypatch.setattr(board.settings, "fids_window_behind", 2.0)
    monkeypatch.setattr(board.settings, "fids_window_ahead", 6.0)
    monkeypatch.setattr(
        board,
        "_rows",
        {
            "fids-A": {"hex": "fids-A", "flight_no": "6E1", "_sched_ts": now + 3600},  # in
            "fids-B": {"hex": "fids-B", "flight_no": "6E2", "_sched_ts": now + 50000},  # too far ahead
            "fids-C": {"hex": "fids-C", "flight_no": "6E3", "_sched_ts": now - 10000},  # too far behind
        },
    )
    got = {r["flight_no"] for r in board.rows()}
    assert got == {"6E1"}
    assert all(not k.startswith("_") for r in board.rows() for k in r)  # private keys stripped


def test_board_merge_enriches_and_appends(monkeypatch):
    monkeypatch.setattr(board.settings, "fids_window_behind", 999.0)
    monkeypatch.setattr(board.settings, "fids_window_ahead", 999.0)
    now = time.time()
    sched_match = {
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
    }
    sched_extra = {
        "hex": "fids-6E200",
        "flight_no": "6E200",
        "dep": "DEL",
        "arr": "BLR",
        "phase": "Boarding",
        "phase_detail": "y",
        "position": False,
        "_sched_ts": now,
    }
    monkeypatch.setattr(board, "_rows", {"fids-6E100": sched_match, "fids-6E200": sched_extra})
    # 6E100 has no known route -> matches; 6E200 is a live BLR-CCU flight sharing the
    # number of a DEL board row but NOT touching Delhi -> must NOT be enriched/matched.
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

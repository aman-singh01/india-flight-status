import pytest

from app import domestic
from app.domestic import (
    _airport_ahead,
    _bearing_deg,
    airline_from_callsign,
    classify,
    flight_number,
    in_bbox,
    infer_route,
    nearest_foreign,
    nearest_indian_airport,
)


@pytest.fixture(autouse=True)
def _no_route_db(monkeypatch):
    """classify() consults route_db; default it to 'unknown' unless a test sets it."""
    monkeypatch.setattr(domestic.route_db, "get", lambda cs: None)


# ---- small helpers ----


def test_airline_from_callsign():
    assert airline_from_callsign("IGO2416")["name"] == "IndiGo"
    assert airline_from_callsign("AIC101")["iata"] == "AI"
    assert airline_from_callsign("UAE504") is None  # not an Indian carrier
    assert airline_from_callsign("") is None
    assert airline_from_callsign(None) is None


def test_flight_number():
    igo = airline_from_callsign("IGO0203")
    assert flight_number("IGO0203", igo) == "6E203"  # ICAO -> IATA, leading zero stripped
    assert flight_number("AIC2984", airline_from_callsign("AIC2984")) == "AI2984"
    assert flight_number("ABC123", None) == "ABC123"
    # trailing 1-2 letters are an ATC operational tag, not the marketed number
    assert flight_number("IGO674P", airline_from_callsign("IGO674P")) == "6E674"
    assert flight_number("IGO154H", airline_from_callsign("IGO154H")) == "6E154"
    assert flight_number("AIC12A", airline_from_callsign("AIC12A")) == "AI12"
    # a callsign with no clean numeric core is returned raw, not "6E2WJ"
    assert flight_number("IGO2WJ", airline_from_callsign("IGO2WJ")) == "IGO2WJ"


def test_in_bbox():
    assert in_bbox(28.5, 77.1)  # Delhi
    assert in_bbox(11.6, 92.7)  # Port Blair
    assert not in_bbox(51.5, 0.1)  # London
    assert not in_bbox(25.0, 55.0)  # Dubai


def test_nearest_indian_airport():
    iata, city = nearest_indian_airport(28.556, 77.100)  # on top of DEL
    assert iata == "DEL"
    assert nearest_indian_airport(20.0, 85.0, max_nm=5)[0] is None  # nothing that close


def test_nearest_foreign():
    assert nearest_foreign(27.70, 85.36) == "KTM"  # Kathmandu
    assert nearest_foreign(22.5, 79.0) is None  # central India


def test_bearing_deg():
    assert abs(_bearing_deg(0, 0, 1, 0) - 0) < 1  # due north
    assert abs(_bearing_deg(0, 0, 0, 1) - 90) < 1  # due east


def test_airport_ahead_points_at_metro():
    # near Nashik heading SSE -> should land on a southern metro
    assert _airport_ahead(20.0, 74.0, 155) in {"BLR", "HYD", "MAA", "GOI", "GOX", "PNQ"}
    # heading with no metro in a 14 deg cone -> None
    assert _airport_ahead(22.0, 79.0, 300) in {None, "DEL", "AMD", "JAI", "BOM", "IXC"}


# ---- infer_route ----


def _trk(*points):  # (lat, lon, alt) -> (ts, lat, lon, alt)
    return [(i, la, lo, al) for i, (la, lo, al) in enumerate(points)]


def test_infer_route_climbout_then_descent():
    track = _trk((19.09, 72.87, 2000), (22, 74, 20000), (25, 76, 35000), (27, 77, 20000), (28.3, 77.0, 6000))
    dep, arr = infer_route(track, 28.4, 77.05, 5000, -1500, 10)
    assert dep == "BOM" and arr == "DEL"


def test_infer_route_approach_only_has_no_origin():
    track = _trk((18.0, 79.0, 9000), (17.6, 78.7, 6000), (17.4, 78.5, 3000))
    dep, arr = infer_route(track, 17.3, 78.45, 2500, -900, 200)
    assert dep is None and arr == "HYD"


def test_infer_route_dep_equals_arr_is_dropped():
    # climbed then came back down to the same airport (go-around)
    track = _trk((28.55, 77.10, 3000), (28.9, 77.4, 12000), (28.6, 77.15, 4000))
    dep, arr = infer_route(track, 28.556, 77.10, 3500, -1200, 180)
    assert not (dep and dep == arr)


# ---- classify ----

BASE_AC = {
    "callsign": "IGO2416",
    "registration": "VT-IPZ",
    "lat": 21.0,
    "lon": 78.0,
    "alt_ft": 36000,
    "vs_fpm": 0,
    "track_deg": 200,
}


def test_classify_rejects_non_indian_carrier():
    ac = {**BASE_AC, "callsign": "UAE504"}
    assert classify(ac, []) is None


def test_classify_rejects_outside_bbox():
    ac = {**BASE_AC, "lat": 25.0, "lon": 55.0}
    assert classify(ac, []) is None


def test_classify_uses_route_db_when_known(monkeypatch):
    monkeypatch.setattr(
        domestic.route_db,
        "get",
        lambda cs: {
            "dep": "DEL",
            "arr": "BOM",
            "dep_city": "New Delhi",
            "arr_city": "Mumbai",
            "dep_country": "IN",
            "arr_country": "IN",
        },
    )
    k = classify(BASE_AC, [])
    assert k["dep"] == "DEL" and k["arr"] == "BOM"
    assert k["route_src"] == "schedule"
    assert k["flight_no"] == "6E2416"


def test_classify_drops_international_indian_carrier_flight(monkeypatch):
    monkeypatch.setattr(
        domestic.route_db,
        "get",
        lambda cs: {
            "dep": "BOM",
            "arr": "HND",
            "dep_country": "IN",
            "arr_country": "JP",
        },
    )
    assert classify(BASE_AC, []) is None


def test_classify_falls_back_to_inference(monkeypatch):
    monkeypatch.setattr(domestic.route_db, "get", lambda cs: None)
    track = _trk((19.09, 72.87, 2000), (22, 74, 20000), (25, 76, 35000), (27, 77, 20000), (28.3, 77.0, 6000))
    k = classify({**BASE_AC, "lat": 28.4, "lon": 77.05, "alt_ft": 5000, "vs_fpm": -1500}, track)
    assert k["dep"] == "BOM" and k["arr"] == "DEL"
    assert k["route_src"] == "inferred"

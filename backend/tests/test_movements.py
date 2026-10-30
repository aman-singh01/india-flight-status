import pytest

from app import main, schemas
from app.domestic import _airports
from app.movements import COOLDOWN_S, MovementLog
from app.store import Tracked

_AP = {a["iata"]: (a["lat"], a["lon"]) for a in _airports}
_KLASS = {
    "status": "domestic",
    "airline": "IndiGo",
    "airline_icao": "IGO",
    "flight_no": "6E2416",
    "dep": "BLR",
    "arr": "DEL",
    "dep_city": "Bengaluru",
    "arr_city": "Delhi",
    "route_src": "schedule",
}


def _ac(hexid="abc123", at="BLR", dlat=0.0, alt=None, gs=0.0, vs=0, t=1000.0, klass=_KLASS):
    """An aircraft report `dlat` degrees north of airport `at` (1 deg = 60 nm)."""
    lat, lon = _AP[at]
    return Tracked(
        hex=hexid,
        callsign="IGO2416",
        registration="VT-ABC",
        type="A320",
        lat=lat + dlat,
        lon=lon,
        alt_ft=alt,
        gs_kt=gs,
        vs_fpm=vs,
        last_seen=t,
        klass=klass,
    )


def test_takeoff_is_logged_against_the_airport_it_left():
    log = MovementLog()
    assert log.observe([_ac(alt=None, gs=5, t=1000)]) == []  # parked
    (ev,) = log.observe([_ac(dlat=0.03, alt=1200, gs=160, vs=2200, t=1060)])
    assert (ev["airport"], ev["kind"], ev["basis"], ev["confidence"]) == (
        "BLR",
        "departure",
        "takeoff",
        "observed",
    )
    assert ev["ts"] == 1060
    assert ev["flight_no"] == "6E2416" and ev["other"] == "DEL" and ev["other_city"] == "Delhi"


def test_touchdown_is_logged_as_an_arrival():
    log = MovementLog()
    assert log.observe([_ac(at="HYD", dlat=0.08, alt=900, gs=140, vs=-700, t=1000)]) == []  # on final
    (ev,) = log.observe([_ac(at="HYD", alt=None, gs=60, t=1030)])
    assert (ev["airport"], ev["kind"], ev["basis"], ev["confidence"]) == (
        "HYD",
        "arrival",
        "touchdown",
        "observed",
    )
    assert ev["other"] == "BLR"  # an arrival's other end is where it came from


def test_a_parked_jet_reading_a_pressure_altitude_offset_is_not_a_touchdown():
    # Found by replaying real Kolkata data: on a low-QNH day parked jets read ~350 ft with
    # no ground flag, then the flag switched on -- which looked like a landing.
    log = MovementLog()
    log.observe([_ac(at="CCU", alt=350, gs=3, t=1000)])
    log.observe([_ac(at="CCU", alt=350, gs=6, t=1060)])
    assert log.observe([_ac(at="CCU", alt=None, gs=8, t=1120)]) == []
    assert log.recent("CCU", now=1200) == []


def test_a_takeoff_counts_even_if_the_first_airborne_report_is_already_high():
    log = MovementLog()
    log.observe([_ac(alt=None, gs=10, t=1000)])
    (ev,) = log.observe([_ac(dlat=0.15, alt=6500, gs=230, vs=2800, t=1120)])  # two minutes on
    assert (ev["airport"], ev["basis"]) == ("BLR", "takeoff")


def test_a_ground_report_too_old_does_not_make_a_takeoff():
    log = MovementLog()
    log.observe([_ac(alt=None, gs=10, t=1000)])
    assert log.observe([_ac(dlat=0.15, alt=6500, gs=230, vs=2800, t=1000 + 900)]) == []


def test_first_seen_climbing_out_is_a_departure_marked_inferred():
    log = MovementLog()
    (ev,) = log.observe([_ac(at="BOM", dlat=0.1, alt=2200, gs=190, vs=2500, t=1000)])
    assert (ev["airport"], ev["kind"], ev["basis"], ev["confidence"]) == (
        "BOM",
        "departure",
        "climb-out",
        "inferred",
    )


def test_lost_on_final_approach_is_an_arrival_marked_inferred_at_the_last_report_time():
    log = MovementLog()
    assert log.observe([_ac(at="HYD", dlat=0.1, alt=1400, gs=145, vs=-800, t=1000)]) == []
    # the store pruned it (coverage ended): the arrival is dated to the last report
    (ev,) = log.observe([], now=1300)
    assert (ev["airport"], ev["kind"], ev["basis"], ev["confidence"]) == (
        "HYD",
        "arrival",
        "lost-on-approach",
        "inferred",
    )
    assert ev["ts"] == 1000


def test_cruising_past_an_airport_logs_nothing():
    log = MovementLog()
    assert log.observe([_ac(at="DEL", dlat=0.05, alt=36000, gs=450, vs=0, t=1000)]) == []
    assert log.observe([_ac(at="DEL", dlat=0.2, alt=36000, gs=450, vs=0, t=1060)]) == []
    assert log.observe([], now=1500) == []  # and leaving coverage at cruise isn't a landing


def test_a_departure_that_leaves_coverage_while_climbing_is_not_an_arrival():
    log = MovementLog()
    log.observe([_ac(at="BLR", dlat=0.05, alt=2500, gs=200, vs=2000, t=1000)], now=1000)  # climb-out event
    assert [e["kind"] for e in log.recent("BLR", now=1100)] == ["departure"]
    assert log.observe([], now=1300) == []  # gone while climbing: no arrival invented


def test_the_same_aircraft_is_not_logged_twice_inside_the_cooldown():
    log = MovementLog()
    takeoff = {"dlat": 0.03, "alt": 1200, "gs": 160, "vs": 2200}
    log.observe([_ac(alt=None, gs=5, t=1000)], now=1000)
    assert len(log.observe([_ac(**takeoff, t=1060)], now=1060)) == 1
    log.observe([_ac(alt=None, gs=5, t=1100)], now=1100)  # touch-and-go / pattern work
    assert log.observe([_ac(**takeoff, t=1160)], now=1160) == []
    later = 1060 + COOLDOWN_S + 10
    log.observe([_ac(alt=None, gs=5, t=later)], now=later)
    assert len(log.observe([_ac(**takeoff, t=later + 60)], now=later + 60)) == 1


def test_a_report_that_has_not_changed_is_not_replayed():
    log = MovementLog()
    log.observe([_ac(alt=None, gs=5, t=1000)])
    airborne = _ac(dlat=0.03, alt=1200, gs=160, vs=2200, t=1060)
    assert len(log.observe([airborne])) == 1
    # a rate-limited sweep leaves the aircraft in the store with the same last_seen
    assert log.observe([airborne]) == []


def test_only_domestic_flights_are_logged():
    log = MovementLog()
    log.observe([_ac(alt=None, gs=5, t=1000, klass=None)])
    assert log.observe([_ac(dlat=0.03, alt=1200, gs=160, vs=2200, t=1060, klass=None)]) == []
    assert log.stats()["watching"] == 0


def test_an_aircraft_reclassified_as_not_domestic_is_not_treated_as_lost():
    log = MovementLog()
    log.observe([_ac(at="HYD", dlat=0.1, alt=1400, gs=145, vs=-800, t=1000)])
    # route resolved to an international one: still in the store, no longer domestic
    assert log.observe([_ac(at="HYD", dlat=0.1, alt=1400, gs=145, vs=-800, t=1060, klass=None)]) == []
    assert log.observe([], now=1400) == []


def test_recent_filters_by_airport_and_window_and_sorts_newest_first():
    log = MovementLog()
    now = 100_000.0
    for hexid, ap, t in [
        ("a", "BLR", now - 600),
        ("b", "BLR", now - 7 * 3600),
        ("c", "BOM", now - 60),
        ("d", "BLR", now - 60),
    ]:
        log.observe([_ac(hexid, at=ap, dlat=0.1, alt=2200, gs=190, vs=2500, t=t)], now=t)
    got = log.recent("BLR", hours=6, now=now)
    assert [e["hex"] for e in got] == ["d", "a"]  # BOM's excluded, the 7-hour-old one is out of window
    assert [e["hex"] for e in log.recent("BLR", hours=6, limit=1, now=now)] == ["d"]


def test_endpoint_returns_the_log_and_validates_against_its_schema():
    log = main.movement_log
    log.__init__()  # isolate from other tests
    log.observe([_ac(alt=None, gs=5, t=1000)])
    log.observe([_ac(dlat=0.03, alt=1200, gs=160, vs=2200, t=1060)])
    import time

    # the endpoint looks back from the wall clock; re-date the event into that window
    log._by_airport["BLR"][0]["ts"] = round(time.time()) - 120
    out = pytest.importorskip("asyncio").run(main.airport_movements("blr", hours=6.0, limit=100))
    assert out["count"] == 1 and out["airport"]["iata"] == "BLR"
    schemas.AirportMovements(**out)  # payload matches the declared response model


def test_endpoint_404s_for_an_unknown_airport():
    import asyncio

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        asyncio.run(main.airport_movements("ZZZ", hours=6.0, limit=100))
    assert e.value.status_code == 404

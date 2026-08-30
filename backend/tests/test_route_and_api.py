import time

from app import main, route
from app.models import flight_dict
from app.store import Tracked

# ---- route.schedule_summary ----


def test_schedule_summary_computes_delay(monkeypatch):
    monkeypatch.setitem(
        route._meta,
        "IGOTEST",
        {
            "sched_dep": "2026-08-30T08:30:00+00:00",
            "sched_arr": "2026-08-30T10:35:00+00:00",
            "est_arr": "2026-08-30T10:53:00+00:00",
            "sched_status": "EnRoute",
            "gate": "22",
            "terminal": "2",
        },
    )
    s = route.schedule_summary("IGOTEST")
    assert s["delay_min"] == 18
    assert s["gate"] == "22"
    assert route.schedule_summary("NOPE") is None


def test_enqueue_schedule_skips_when_route_known(monkeypatch):
    monkeypatch.setattr(route.settings, "schedule_all", False)
    monkeypatch.setitem(route._routes, "IGOKNOWN", {"dep": "DEL", "arr": "BOM"})
    route._sched_queued.discard("IGOKNOWN")
    route.enqueue_schedule("IGOKNOWN", "6E1")
    assert "IGOKNOWN" not in route._sched_queued


def test_quota_guard(monkeypatch):
    monkeypatch.setattr(route.settings, "schedule_max_per_hour", 2)
    monkeypatch.setattr(route.settings, "schedule_max_per_day", 5)
    route._sched_hits.clear()
    assert route._quota_ok()
    route._sched_hits.extend([time.time()] * 2)
    assert not route._quota_ok()  # hourly cap hit


# ---- models.flight_dict ----


def _tracked(**kw):
    t = Tracked(hex=kw.get("hex", "800abc"))
    t.callsign = kw.get("callsign", "IGO2416")
    t.type = kw.get("type", "A20N")
    t.lat, t.lon = kw.get("lat", 21.0), kw.get("lon", 78.0)
    t.alt_ft = kw.get("alt_ft", 36000)
    t.gs_kt = kw.get("gs_kt", 460.0)
    t.vs_fpm = kw.get("vs_fpm", 0)
    t.track_deg = kw.get("track_deg", 200.0)
    t.klass = kw.get("klass")
    return t


def test_flight_dict_none_when_not_domestic():
    assert flight_dict(_tracked(klass=None)) is None


def test_flight_dict_shape():
    t = _tracked(
        klass={
            "status": "domestic",
            "airline": "IndiGo",
            "airline_icao": "IGO",
            "flight_no": "6E2416",
            "dep": "DEL",
            "arr": "BOM",
            "dep_city": "New Delhi",
            "arr_city": "Mumbai",
            "route_src": "schedule",
        }
    )
    d = flight_dict(t)
    assert d["flight_no"] == "6E2416"
    assert d["dep"] == "DEL" and d["arr"] == "BOM"
    assert d["phase"] == "En route"
    assert "cruising" in d["phase_detail"]
    assert d["near"] is not None  # "N km DIR of <city>"


# ---- API handlers (called directly, no lifespan / network) ----


def test_domestic_flights_requires_full_route(monkeypatch):
    full = _tracked(
        hex="a1",
        klass={
            "status": "domestic",
            "airline": "IndiGo",
            "airline_icao": "IGO",
            "flight_no": "6E1",
            "dep": "DEL",
            "arr": "BOM",
        },
    )
    half = _tracked(
        hex="a2",
        klass={
            "status": "domestic",
            "airline": "IndiGo",
            "airline_icao": "IGO",
            "flight_no": "6E2",
            "dep": None,
            "arr": "BOM",
        },
    )
    monkeypatch.setattr(main.store, "_ac", {"a1": full, "a2": half}, raising=False)
    out = main.domestic_flights()
    nos = {f["flight_no"] for f in out}
    assert "6E1" in nos and "6E2" not in nos


async def test_health_handler(monkeypatch):
    monkeypatch.setattr(main.store, "_ac", {}, raising=False)
    h = await main.health()
    assert h["ok"] is True and "routes" in h


async def test_airports_and_airlines_handlers():
    ap = await main.airports()
    assert ap["count"] > 50 and any(a["iata"] == "DEL" for a in ap["airports"])
    al = await main.airlines()
    assert "IGO" in al and al["IGO"]["name"] == "IndiGo"


# ---- response schemas match the actual payloads ----


def test_schemas_validate_real_payloads():
    from app import schemas

    t = _tracked(
        klass={
            "status": "domestic",
            "airline": "IndiGo",
            "airline_icao": "IGO",
            "flight_no": "6E2416",
            "dep": "DEL",
            "arr": "BOM",
            "dep_city": "New Delhi",
            "arr_city": "Mumbai",
            "route_src": "schedule",
        }
    )
    d = flight_dict(t)
    schemas.Flight(**d)  # /api/flights row
    schemas.FlightDetail(**{**d, "track": [[1.0, 21.0, 78.0, 36000.0]]})
    schemas.FlightsResponse(count=1, flights=[d])
    schemas.Health(ok=True, tracked=1, domestic=1, sources="demo", routes=route.stats())
    schemas.AirportsResponse(
        count=1,
        airports=[{"iata": "DEL", "icao": "VIDP", "name": "x", "city": "Delhi", "lat": 1.0, "lon": 2.0}],
    )
    schemas.FlightStatus(query="6E1", found=False, reason="none")

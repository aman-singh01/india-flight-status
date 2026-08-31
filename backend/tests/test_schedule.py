from app import schedule
from app.schedule import AeroDataBoxProvider, FlightAwareProvider, _iso, _parse_dt, build_provider


class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _provider(cls, payload, status=200):
    p = cls.__new__(cls)  # skip __init__ (no real http client)

    async def _get(*a, **k):
        return _FakeResp(status, payload)

    p._client = type("C", (), {"get": staticmethod(_get)})()
    if hasattr(cls, "_warned"):
        p._warned = set()
    return p


def test_parse_dt():
    assert _parse_dt({"utc": "2026-08-30 08:30Z"}) == "2026-08-30T08:30:00+00:00"
    assert _parse_dt(None) is None
    assert _parse_dt({}) is None


def test_iso_normalizes_z():
    assert _iso("2026-08-30T20:53:00Z") == "2026-08-30T20:53:00+00:00"
    assert _iso(None) is None
    assert _iso("not-a-date") is None


def test_build_provider_off_without_key(monkeypatch):
    monkeypatch.setattr(schedule.settings, "schedule_provider", "aerodatabox")
    monkeypatch.setattr(schedule.settings, "schedule_api_key", "")
    assert build_provider() is None
    monkeypatch.setattr(schedule.settings, "schedule_provider", "")
    assert build_provider() is None


async def test_aerodatabox_parses_leg():
    payload = [
        {
            "number": "AI 2984",
            "status": "EnRoute",
            "airline": {"iata": "AI"},
            "departure": {
                "airport": {"iata": "DEL", "municipalityName": "New Delhi", "countryCode": "IN"},
                "scheduledTime": {"utc": "2026-08-30 08:30Z"},
                "gate": "22",
            },
            "arrival": {
                "airport": {"iata": "BOM", "municipalityName": "Mumbai", "countryCode": "IN"},
                "scheduledTime": {"utc": "2026-08-30 10:35Z"},
                "revisedTime": {"utc": "2026-08-30 10:53Z"},
                "terminal": "2",
            },
        }
    ]
    res = await _provider(AeroDataBoxProvider, payload).route("AI2984")
    assert res["dep"] == "DEL" and res["arr"] == "BOM"
    assert res["dep_country"] == "IN"
    assert res["est_arr"] == "2026-08-30T10:53:00+00:00"
    assert res["gate"] == "22" and res["terminal"] == "2"
    assert res["flight_iata"] == "AI2984"  # from "number", space stripped


async def test_aerodatabox_204_is_none():
    assert await _provider(AeroDataBoxProvider, None, status=204).route("6E999") is None


async def test_aerodatabox_filters_by_airline():
    payload = [
        {
            "airline": {"iata": "WN"},
            "departure": {"airport": {"iata": "MLB"}, "scheduledTime": {"utc": "2026-08-30 05:00Z"}},
            "arrival": {"airport": {"iata": "DAB"}},
        },
        {
            "airline": {"iata": "S5"},
            "departure": {"airport": {"iata": "BLR"}, "scheduledTime": {"utc": "2026-08-30 09:00Z"}},
            "arrival": {"airport": {"iata": "HYD"}},
        },
    ]
    res = await _provider(AeroDataBoxProvider, payload).route("S5623")
    assert res["dep"] == "BLR" and res["arr"] == "HYD"  # picked the S5 leg, not the WN one


async def test_flightaware_parses_and_derives_country():
    payload = {
        "flights": [
            {
                "status": "En Route",
                "ident_iata": "AI865",
                "origin": {"code_iata": "DEL", "code_icao": "VIDP", "city": "New Delhi"},
                "destination": {"code_iata": "BOM", "code_icao": "VABB", "city": "Mumbai"},
                "scheduled_out": "2026-08-30T18:30:00Z",
                "scheduled_in": "2026-08-30T20:35:00Z",
                "estimated_in": "2026-08-30T20:53:00Z",
                "gate_destination": "A12",
                "terminal_destination": "2",
            }
        ]
    }
    res = await _provider(FlightAwareProvider, payload).route("6E2984", "IGO2984")
    assert res["dep"] == "DEL" and res["arr"] == "BOM"
    assert res["dep_country"] == "IN" and res["arr_country"] == "IN"  # from VA/VI ICAO prefix
    assert res["est_arr"] == "2026-08-30T20:53:00+00:00"
    assert res["gate"] == "A12"
    assert res["flight_iata"] == "AI865"  # marketed number from ident_iata, not the callsign


async def test_flightaware_auth_failure_returns_none():
    assert await _provider(FlightAwareProvider, {"error": "bad key"}, status=403).route("X", "X") is None

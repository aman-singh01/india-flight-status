from app.sources import build_sources
from app.sources.base import GRID, norm_alt, normalize_reapi
from app.sources.opensky import _normalize as opensky_normalize


def test_norm_alt():
    assert norm_alt(35000) == 35000
    assert norm_alt("35000") == 35000
    assert norm_alt("ground") is None
    assert norm_alt(None) is None
    assert norm_alt("junk") is None


def test_normalize_reapi_typical():
    ac = {
        "hex": "800ABC",
        "flight": "IGO2416 ",
        "r": "VT-IPZ",
        "t": "A20N",
        "alt_baro": 36000,
        "gs": 465.4,
        "track": 210.2,
        "baro_rate": -64,
        "lat": 19.1,
        "lon": 75.2,
    }
    n = normalize_reapi(ac, "adsblol")
    assert n["hex"] == "800abc"  # lowercased
    assert n["callsign"] == "IGO2416"  # trailing space stripped
    assert n["registration"] == "VT-IPZ"
    assert n["alt_ft"] == 36000
    assert n["vs_fpm"] == -64
    assert n["source"] == "adsblol"


def test_normalize_reapi_rejects_non_icao_and_empty():
    assert normalize_reapi({"hex": "~ABC123", "lat": 1, "lon": 2}, "x") is None
    assert normalize_reapi({"hex": "", "lat": 1, "lon": 2}, "x") is None


def test_normalize_reapi_ground_and_missing_callsign():
    n = normalize_reapi({"hex": "abc123", "alt_baro": "ground", "lat": 1, "lon": 2}, "x")
    assert n["alt_ft"] is None
    assert n["callsign"] is None


def test_opensky_normalize_converts_units():
    # [icao24, callsign, country, t_pos, last_contact, lon, lat, baro_alt_m,
    #  on_ground, velocity_ms, track, vrate_ms, sensors, geo_alt_m, squawk, spi, src]
    s = [
        "4baa12",
        "IGO2416 ",
        "India",
        1788187509,
        1788187509,
        75.2,
        19.1,
        10668.0,
        False,
        239.0,
        210.2,
        -2.03,
        None,
        11277.6,
        None,
        False,
        0,
    ]
    n = opensky_normalize(s)
    assert n["hex"] == "4baa12"
    assert n["callsign"] == "IGO2416"
    assert n["lat"] == 19.1 and n["lon"] == 75.2
    assert n["alt_ft"] == 35000  # barometric altitude, m -> ft
    assert n["gs_kt"] == 465  # m/s -> kt
    assert n["vs_fpm"] == -400  # m/s -> fpm
    assert n["source"] == "opensky"


def test_opensky_normalize_rejects_incomplete():
    assert opensky_normalize([]) is None
    assert opensky_normalize(["abc", "X", "IN", 0, 0, None, None, 0, False, 0, 0, 0]) is None


def test_grid_covers_india_span():
    lats = {p[0] for p in GRID}
    lons = {p[1] for p in GRID}
    assert min(lats) <= 12 and max(lats) >= 28
    assert min(lons) <= 73 and max(lons) >= 91
    assert len(GRID) == 12


def test_build_sources_parses_and_dedups_kinds():
    names = [s.name for s in build_sources("demo,adsblol,adsbfi,opensky,readsb:http://pi/x.json,bogus")]
    assert names == ["demo", "adsblol", "adsbfi", "opensky", "readsb"]


def test_build_sources_falls_back_to_demo():
    assert [s.name for s in build_sources("")] == ["demo"]
    assert [s.name for s in build_sources("readsb:")] == ["demo"]  # missing url -> skipped -> fallback


# ---- OpenSky: OAuth2, quota back-off, stale-drop ----


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._body


class _FakeHttp:
    """Stands in for httpx.AsyncClient; hands back queued responses and records calls."""

    def __init__(self, gets=(), posts=()):
        self.gets, self.posts, self.calls = list(gets), list(posts), []

    async def get(self, url, params=None, headers=None):
        self.calls.append(("get", headers or {}))
        return self.gets.pop(0)

    async def post(self, url, data=None):
        self.calls.append(("post", data))
        return self.posts.pop(0)

    async def aclose(self):
        pass


_STATE = ["4baa12", "IGO2416 ", "India", 1, 1, 75.2, 19.1, 10668.0, False, 239.0, 210.2, -2.0, None, 11000.0]


def _opensky(monkeypatch, *, creds, http):
    from app.sources import opensky as osk

    monkeypatch.setattr(osk.settings, "opensky_client_id", "cid" if creds else "")
    monkeypatch.setattr(osk.settings, "opensky_client_secret", "sec" if creds else "")
    monkeypatch.setattr(osk.settings, "opensky_interval", 0.0)
    src = osk.OpenSkySource()
    src._client = http
    return src


def test_opensky_interval_follows_the_credit_budget(monkeypatch):
    # India bbox = 4 credits/call: 400/day anon, 4000/day registered
    assert _opensky(monkeypatch, creds=False, http=_FakeHttp())._interval == 900.0
    assert _opensky(monkeypatch, creds=True, http=_FakeHttp())._interval == 100.0


async def test_opensky_oauth_flow_reuses_token_and_throttles(monkeypatch):
    http = _FakeHttp(
        posts=[_Resp(200, {"access_token": "tok", "expires_in": 1800})],
        gets=[_Resp(200, {"states": [_STATE]}, {"X-Rate-Limit-Remaining": "3996"})],
    )
    src = _opensky(monkeypatch, creds=True, http=http)
    out = await src.fetch()
    assert [a["hex"] for a in out] == ["4baa12"]
    assert ("get", {"Authorization": "Bearer tok"}) in http.calls
    assert "3996 credits left" in src.note
    # within the interval: served from cache, no second request of any kind
    assert await src.fetch() == out
    assert len(http.calls) == 2  # one token POST + one states GET


async def test_opensky_429_honours_retry_after_and_drops_stale(monkeypatch):
    http = _FakeHttp(
        gets=[
            _Resp(200, {"states": [_STATE]}),
            _Resp(429, headers={"X-Rate-Limit-Retry-After-Seconds": "5400"}),
        ]
    )
    src = _opensky(monkeypatch, creds=False, http=http)
    assert len(await src.fetch()) == 1
    # force the next call through the throttle, and age the cache past STALE_TTL
    src._last_call -= 10_000
    src._cache_ts -= 10_000
    assert await src.fetch() == []  # stale positions are NOT re-served as fresh
    assert "429" in src.note and "5400" in src.note
    n = len(http.calls)
    assert await src.fetch() == []  # blocked: no further request until Retry-After
    assert len(http.calls) == n


async def test_opensky_connect_failure_backs_off(monkeypatch):
    import httpx

    class _Dead(_FakeHttp):
        async def get(self, url, params=None, headers=None):
            self.calls.append(("get", headers or {}))
            raise httpx.ConnectTimeout("no route")

    http = _Dead()
    src = _opensky(monkeypatch, creds=False, http=http)
    assert await src.fetch() == []
    assert "ConnectTimeout" in src.note
    assert await src.fetch() == []
    assert len(http.calls) == 1  # no retry next cycle -- it would stall the whole ingest loop


async def test_opensky_bad_credentials_backs_off(monkeypatch):
    http = _FakeHttp(posts=[_Resp(401, {"error": "invalid_client"})])
    src = _opensky(monkeypatch, creds=True, http=http)
    assert await src.fetch() == []
    assert "token rejected" in src.note
    assert [c[0] for c in http.calls] == ["post"]  # never reached the data API
    assert await src.fetch() == []
    assert len(http.calls) == 1  # and doesn't hammer the auth server

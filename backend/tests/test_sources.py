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


# ---- Grid poll: Retry-After retry, oldest-first order ----


class _GridHttp:
    """Fake client for GridPollSource: per-cell response queues, records request order."""

    def __init__(self, plan=None):
        self.plan, self.order = plan or {}, []

    async def get(self, url):
        _, lat, lon, _dist = url.rsplit("/", 3)
        cell = (int(lat), int(lon))
        self.order.append(cell)
        queue = self.plan.get(cell)
        return queue.pop(0) if queue else _Resp(200, {"ac": []})

    async def aclose(self):
        pass


def _aircraft(hexid):
    return {"hex": hexid, "flight": "IGO1", "lat": 20.0, "lon": 78.0, "alt_baro": 30000}


def _grid(monkeypatch, plan=None):
    import types

    from app.sources import base

    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(base, "asyncio", types.SimpleNamespace(sleep=fake_sleep))
    src = base.GridPollSource()
    src.url_template = "http://x/{lat}/{lon}/{dist}"
    src._client = _GridHttp(plan)
    return src, sleeps


async def test_grid_honours_retry_after_and_recovers_the_cell(monkeypatch):
    plan = {(20, 79): [_Resp(429, headers={"retry-after": "2"}), _Resp(200, {"ac": [_aircraft("aaa111")]})]}
    src, sleeps = _grid(monkeypatch, plan)
    out = await src.fetch()
    assert [a["hex"] for a in out] == ["aaa111"]  # the retried cell's aircraft are kept
    assert 2.25 in sleeps  # waited the Retry-After (+ margin) before retrying
    assert src._client.order.count((20, 79)) == 2
    assert "rate-limited" not in src.note and "1 recovered" in src.note


async def test_grid_skips_cells_whose_retry_after_is_too_long(monkeypatch):
    plan = {(20, 79): [_Resp(429, headers={"retry-after": "60"})]}
    src, sleeps = _grid(monkeypatch, plan)
    assert await src.fetch() == []
    assert src._client.order.count((20, 79)) == 1  # not retried: would stall the sweep
    assert max(sleeps) == src.spacing
    assert "1/12 cells rate-limited" in src.note


async def test_grid_caps_total_time_spent_waiting(monkeypatch):
    plan = {
        c: [_Resp(429, headers={"retry-after": "3"}), _Resp(429, headers={"retry-after": "3"})]
        for c in [(12, 73), (12, 79)]
    }
    src, _ = _grid(monkeypatch, plan)
    src.retry_budget = 5.0  # room for one 3 s wait, not two
    await src.fetch()
    retried = [c for c in plan if src._client.order.count(c) == 2]
    assert len(retried) == 1
    assert "2/12 cells rate-limited" in src.note  # neither recovered (both retries 429'd again)


async def test_grid_serves_the_longest_waiting_cell_first(monkeypatch):
    import time

    src, _ = _grid(monkeypatch)
    from app.sources.base import GRID

    starved = (28, 91)
    src._last_ok = {c: time.time() for c in GRID if c != starved}  # everything else was just served
    await src.fetch()
    assert src._client.order[0] == starved
    assert starved in src._last_ok  # and a successful fetch is remembered


def test_retry_wait_falls_back_when_header_is_missing_or_a_date():
    from app.sources.base import _retry_wait

    assert _retry_wait(_Resp(429, headers={"retry-after": "5"}), 3.0) == 5.0
    assert _retry_wait(_Resp(429), 3.0) == 3.0
    assert _retry_wait(_Resp(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}), 3.0) == 3.0


async def test_grid_defers_quiet_cells_but_never_busy_ones(monkeypatch):
    import time

    src, _ = _grid(monkeypatch)
    now = time.time()
    quiet, busy = (12, 91), (12, 79)
    src._last_ok = {quiet: now - 20, busy: now - 20}  # both served 20 s ago
    src._last_n = {quiet: 0, busy: 40}
    await src.fetch()
    assert quiet not in src._client.order  # thin answer, recent -> skip this sweep
    assert busy in src._client.order  # busy cells are polled every sweep
    assert "1 quiet cells deferred" in src.note


async def test_grid_repolls_a_quiet_cell_once_its_answer_is_old(monkeypatch):
    import time

    src, _ = _grid(monkeypatch)
    quiet = (12, 91)
    src._last_ok = {quiet: time.time() - src.quiet_period - 1}
    src._last_n = {quiet: 0}
    await src.fetch()
    assert quiet in src._client.order
    assert "deferred" not in src.note

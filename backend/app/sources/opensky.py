from __future__ import annotations

import logging
import time

import httpx

from ..config import settings
from .base import Source

log = logging.getLogger("sources")

_M_TO_FT = 3.28084
_MS_TO_KT = 1.94384
_MS_TO_FPM = 196.850


_TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"


class OpenSkySource(Source):
    """OpenSky Network -- one bounding-box call to /states/all for all of India.

    A different feeder network + MLAT from adsb.lol, so it picks up aircraft the
    grid poll misses (notably Mode-S-only airframes and low climb-outs).

    Quota is the whole story: the India bbox is ~990 sq deg, which costs 4 credits
    per call, against 400 credits/day anonymous (bucketed per IP -- a shared host's
    budget is gone almost immediately) or 4000 registered. So the source polls at
    >= 100 s with credentials, ~15 min without, honors the server's Retry-After on
    a 429, and only re-serves its last result while it is still fresher than
    STALE_TTL -- otherwise the store would keep frozen positions looking alive.
    Set OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET (OAuth2 client credentials; Basic
    auth is no longer accepted).
    """

    name = "opensky"
    _URL = "https://opensky-network.org/api/states/all"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=25.0)
        self._cid = settings.opensky_client_id
        self._secret = settings.opensky_client_secret
        self._authed = bool(self._cid and self._secret)
        self._interval = settings.opensky_interval or (100.0 if self._authed else 900.0)
        self._token: str | None = None
        self._token_exp = 0.0
        self._last_call = 0.0
        self._blocked_until = 0.0
        self._cache: list[dict] = []
        self._cache_ts = 0.0
        self.note = "authenticated" if self._authed else "anonymous (400 credits/day per IP)"

    def _cached(self, now: float) -> list[dict]:
        """The last result, but only while it is still fresh enough to show."""
        return self._cache if now - self._cache_ts < settings.stale_ttl else []

    async def _bearer(self, now: float) -> str | None:
        if self._token and now < self._token_exp:
            return self._token
        try:
            r = await self._client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._cid,
                    "client_secret": self._secret,
                },
            )
        except httpx.HTTPError as e:
            self.note = f"token request failed: {e.__class__.__name__}"
            return None
        if r.status_code != 200:
            self.note = f"token rejected (HTTP {r.status_code}) - check OPENSKY_CLIENT_ID/SECRET"
            self._blocked_until = now + 300.0  # don't hammer the auth server
            return None
        body = r.json()
        self._token = body.get("access_token")
        self._token_exp = now + float(body.get("expires_in", 1800)) - 60.0  # refresh a minute early
        return self._token

    async def fetch(self) -> list[dict]:
        now = time.time()
        if now < self._blocked_until:
            return self._cached(now)
        if self._last_call and now - self._last_call < self._interval:
            return self._cached(now)

        headers = {}
        if self._authed:
            token = await self._bearer(now)
            if not token:
                return self._cached(now)
            headers["Authorization"] = f"Bearer {token}"

        la_min, la_max, lo_min, lo_max = settings.india_bbox
        params = {"lamin": la_min, "lamax": la_max, "lomin": lo_min, "lomax": lo_max}
        try:
            r = await self._client.get(self._URL, params=params, headers=headers)
        except httpx.HTTPError as e:
            self.note = f"network error: {e.__class__.__name__}"
            return self._cached(now)
        self._last_call = now

        if r.status_code == 401 and self._authed:
            self._token = None  # expired early; fetch a fresh one next cycle
            self.note = "token rejected; refreshing"
            return self._cached(now)
        if r.status_code == 429:
            wait = _retry_after(r)
            self._blocked_until = now + wait
            self.note = f"quota exhausted (429); retry in {int(wait)}s" + (
                "" if self._authed else " - anonymous quota is per IP, set OPENSKY_CLIENT_ID/SECRET"
            )
            log.warning("opensky: %s", self.note)
            return self._cached(now)
        if r.status_code != 200:
            self.note = f"HTTP {r.status_code}"
            return self._cached(now)

        try:
            states = r.json().get("states") or []
        except ValueError:
            self.note = "bad JSON"
            return self._cached(now)

        out = [n for s in states if (n := _normalize(s))]
        self._cache, self._cache_ts = out, now
        left = r.headers.get("X-Rate-Limit-Remaining")
        self.note = "ok" + (f", {left} credits left" if left else "")
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


def _retry_after(r: httpx.Response) -> float:
    try:
        return max(60.0, float(r.headers.get("X-Rate-Limit-Retry-After-Seconds", "")))
    except ValueError:
        return 3600.0


def _normalize(s: list) -> dict | None:
    """One OpenSky state vector -> the common aircraft dict."""
    if not s or len(s) < 12:
        return None
    hexid = (s[0] or "").lower().strip()
    lat, lon = s[6], s[5]
    if not hexid or lat is None or lon is None:
        return None
    alt_m = s[7] if s[7] is not None else s[13]  # barometric altitude, fall back to geometric
    return {
        "hex": hexid,
        "callsign": (s[1] or "").strip() or None,
        "registration": None,
        "type": None,
        "lat": lat,
        "lon": lon,
        "alt_ft": round(alt_m * _M_TO_FT) if alt_m is not None else None,
        "gs_kt": round(s[9] * _MS_TO_KT) if s[9] is not None else None,
        "track_deg": s[10],
        "vs_fpm": round(s[11] * _MS_TO_FPM) if s[11] is not None else None,
        "source": "opensky",
    }

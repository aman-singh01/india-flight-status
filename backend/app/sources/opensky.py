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


class OpenSkySource(Source):
    """OpenSky Network -- one bounding-box call to /states/all for all of India.

    A different feeder network + MLAT from adsb.lol, so it picks up aircraft the
    grid poll misses (notably Mode-S-only airframes and low climb-outs). Anonymous
    access works but is quota-capped, so the source self-throttles and re-serves
    its last result between real calls; set OPENSKY_USER / OPENSKY_PASS for a free
    account and a much tighter interval.
    """

    name = "opensky"
    _URL = "https://opensky-network.org/api/states/all"

    def __init__(self) -> None:
        auth = None
        if settings.opensky_user and settings.opensky_pass:
            auth = (settings.opensky_user, settings.opensky_pass)
        self._client = httpx.AsyncClient(timeout=25.0, auth=auth)
        self._interval = settings.opensky_interval or (10.0 if auth else 300.0)
        self._authed = auth is not None
        self._last_call = 0.0
        self._cache: list[dict] = []
        self._warned = False

    async def fetch(self) -> list[dict]:
        now = time.time()
        if self._cache and now - self._last_call < self._interval:
            return self._cache

        la_min, la_max, lo_min, lo_max = settings.india_bbox
        params = {"lamin": la_min, "lamax": la_max, "lomin": lo_min, "lomax": lo_max}
        try:
            r = await self._client.get(self._URL, params=params)
        except httpx.HTTPError as e:
            log.debug("opensky: %s", e)
            return self._cache
        self._last_call = now
        if r.status_code == 429:
            if not self._warned:
                log.warning(
                    "opensky rate-limited (429) -- anonymous cap is ~400/day; set "
                    "OPENSKY_USER / OPENSKY_PASS for a free account"
                )
                self._warned = True
            return self._cache
        if r.status_code != 200:
            log.debug("opensky HTTP %s: %s", r.status_code, r.text[:120])
            return self._cache

        try:
            states = r.json().get("states") or []
        except ValueError:
            return self._cache

        out: list[dict] = []
        for s in states:
            n = _normalize(s)
            if n:
                out.append(n)
        self._cache = out
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


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

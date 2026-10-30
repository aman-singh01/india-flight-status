from __future__ import annotations

import asyncio
import logging
import random
import time

import httpx

log = logging.getLogger("sources")

# 12-point grid, 250 nm radius per point -- covers mainland India (the far NE and
# the island groups are thin on public coverage anyway).
GRID: list[tuple[int, int]] = [(lat, lon) for lat in (12, 20, 28) for lon in (73, 79, 85, 91)]

_UA = "india-domestic-flight-tracker/0.1"


class Source:
    """A pluggable feed of aircraft observations."""

    name = "base"
    note = ""  # one-line status of the last fetch, surfaced in /api/health

    async def fetch(self) -> list[dict]:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


class GridPollSource(Source):
    """Polls a shuffled lat/lon grid against a re-api ('/lat/{lat}/lon/{lon}/dist')
    style endpoint. Subclasses set `url_template`, `spacing` and (optionally) `dist`."""

    url_template = ""
    dist = 250
    spacing = 3.0
    timeout = 20.0
    # A 429 carries Retry-After (adsb.lol sends 1-5 s). Sit it out and retry the cell
    # once rather than losing it until the next sweep -- but cap the waiting, so a
    # starved sweep can't stretch past what STALE_TTL tolerates.
    max_retry_wait = 10.0  # longest single Retry-After we'll honour (s)
    retry_budget = 30.0  # total seconds per sweep spent waiting on 429s
    # The limiter allows roughly 10 requests per window and the grid is 12 cells, some of
    # them over sea / the Himalaya. A cell whose last answer was this thin is re-polled
    # only after quiet_period -- its aircraft outlive that anyway (STALE_TTL).
    quiet_max = 5  # aircraft
    quiet_period = 110.0  # seconds

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": _UA})
        self._warned: set[int] = set()
        self._last_ok: dict[tuple[int, int], float] = {}  # cell -> when it last returned data
        self._last_n: dict[tuple[int, int], int] = {}  # cell -> aircraft in that answer

    def _quiet(self, cell: tuple[int, int], now: float) -> bool:
        return (
            self._last_n.get(cell, self.quiet_max + 1) <= self.quiet_max
            and now - self._last_ok.get(cell, 0.0) < self.quiet_period
        )

    async def _get(self, lat: int, lon: int) -> httpx.Response | None:
        url = self.url_template.format(lat=lat, lon=lon, dist=self.dist)
        try:
            return await self._client.get(url)
        except httpx.HTTPError as e:
            log.debug("%s cell %s,%s: %s", self.name, lat, lon, e)
            return None

    async def fetch(self) -> list[dict]:
        out: dict[str, dict] = {}
        grid = GRID[:]
        random.shuffle(grid)
        # Least-recently-served first. The limiter cuts a sweep short at a roughly fixed
        # request count, so with a fixed order the same cells would starve every time.
        grid.sort(key=lambda c: self._last_ok.get(c, 0.0))
        now = time.time()
        total = len(grid)
        grid = [c for c in grid if not self._quiet(c, now)]
        deferred = total - len(grid)
        limited = blocked = recovered = 0
        waited = 0.0
        for i, cell in enumerate(grid):
            r = await self._get(*cell)
            if r is not None and r.status_code == 429:
                wait = _retry_wait(r, self.spacing)
                if wait <= self.max_retry_wait and waited + wait <= self.retry_budget:
                    waited += wait
                    await asyncio.sleep(wait + 0.25)
                    r = await self._get(*cell)
                    if r is not None and r.status_code == 200:
                        recovered += 1
            if r is None:
                pass
            elif r.status_code == 429:
                limited += 1
            elif r.status_code in (401, 403):
                blocked += 1
                if r.status_code not in self._warned:
                    log.warning("%s: HTTP %s -- %s", self.name, r.status_code, r.text[:180])
                    self._warned.add(r.status_code)
            elif r.status_code == 200:
                seen = 0
                for ac in r.json().get("ac", []):
                    n = normalize_reapi(ac, self.name)
                    if n and n["lat"] is not None and n["lon"] is not None:
                        out[n["hex"]] = n
                        seen += 1
                self._last_ok[cell], self._last_n[cell] = time.time(), seen
            else:
                log.debug("%s cell %s,%s: HTTP %s", self.name, *cell, r.status_code)
            if i < len(grid) - 1:
                await asyncio.sleep(self.spacing)
        if limited:
            log.info(
                "%s sweep: %d aircraft, %d/%d cells rate-limited", self.name, len(out), limited, len(grid)
            )
        bits = []
        if limited:
            bits.append(f"{limited}/{len(grid)} cells rate-limited")
        if recovered:
            bits.append(f"{recovered} recovered after Retry-After")
        if blocked:
            bits.append(f"{blocked}/{len(grid)} cells HTTP 401/403")
        self.note = "; ".join(bits) or "ok"
        if deferred:
            self.note += f" ({deferred} quiet cells deferred)"
        return list(out.values())

    async def aclose(self) -> None:
        await self._client.aclose()


def _retry_wait(r: httpx.Response, default: float) -> float:
    """Seconds a 429 asks us to wait (Retry-After), or `default` if absent/unparseable."""
    try:
        return max(0.5, float(r.headers.get("retry-after", default)))
    except ValueError:  # an HTTP-date, which these APIs don't send
        return default


def norm_alt(v) -> int | None:
    if v is None or v == "ground":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize_reapi(ac: dict, source: str) -> dict | None:
    """Normalize one aircraft record in the readsb / re-api ('aircraft.json') shape."""
    hexid = (ac.get("hex") or "").lower().strip()
    if not hexid or hexid.startswith("~"):  # ~ = non-ICAO address (TIS-B / ADS-R); skip
        return None
    lat, lon = ac.get("lat"), ac.get("lon")
    return {
        "hex": hexid,
        "callsign": (ac.get("flight") or "").strip() or None,
        "registration": ac.get("r"),
        "type": ac.get("t"),
        "lat": lat,
        "lon": lon,
        "alt_ft": norm_alt(ac.get("alt_baro")),
        "gs_kt": ac.get("gs"),
        "track_deg": ac.get("track"),
        "vs_fpm": norm_alt(ac.get("baro_rate")),
        "source": source,
    }

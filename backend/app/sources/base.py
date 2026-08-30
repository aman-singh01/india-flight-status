from __future__ import annotations

import asyncio
import logging
import random

import httpx

log = logging.getLogger("sources")

# 12-point grid, 250 nm radius per point -- covers mainland India (the far NE and
# the island groups are thin on public coverage anyway).
GRID: list[tuple[int, int]] = [(lat, lon) for lat in (12, 20, 28) for lon in (73, 79, 85, 91)]

_UA = "india-domestic-flight-tracker/0.1"


class Source:
    """A pluggable feed of aircraft observations."""

    name = "base"

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

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": _UA})
        self._warned: set[str] = set()

    async def fetch(self) -> list[dict]:
        out: dict[str, dict] = {}
        grid = GRID[:]
        random.shuffle(grid)
        limited = 0
        for i, (lat, lon) in enumerate(grid):
            url = self.url_template.format(lat=lat, lon=lon, dist=self.dist)
            try:
                r = await self._client.get(url)
                if r.status_code == 429:
                    limited += 1
                elif r.status_code in (401, 403):
                    if r.status_code not in self._warned:
                        log.warning("%s: HTTP %s -- %s", self.name, r.status_code, r.text[:180])
                        self._warned.add(r.status_code)
                else:
                    r.raise_for_status()
                    for ac in r.json().get("ac", []):
                        n = normalize_reapi(ac, self.name)
                        if n and n["lat"] is not None and n["lon"] is not None:
                            out[n["hex"]] = n
            except httpx.HTTPError as e:
                log.debug("%s cell %s,%s: %s", self.name, lat, lon, e)
            if i < len(grid) - 1:
                await asyncio.sleep(self.spacing)
        if limited:
            log.info(
                "%s sweep: %d aircraft, %d/%d cells rate-limited", self.name, len(out), limited, len(grid)
            )
        return list(out.values())

    async def aclose(self) -> None:
        await self._client.aclose()


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

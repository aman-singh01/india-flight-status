from __future__ import annotations

import asyncio
import logging
import random

import httpx

from .base import Source, normalize_reapi

log = logging.getLogger("adsblol")

# 12-point grid, 250 nm radius per point (adsb.lol's max). Covers the mainland
# Chennai/Mumbai/Delhi/Kolkata FIRs; the far NE and the island groups are thin on
# public coverage anyway -- your own readsb feeders fill those in.
GRID: list[tuple[int, int]] = [
    (lat, lon) for lat in (12, 20, 28) for lon in (73, 79, 85, 91)
]

_BASE = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250"

# api.adsb.lol rate-limits anonymous callers hard. One request every ~3 s, no
# retries -- a cell that 429s is just skipped this sweep; the shuffled order plus
# STALE_TTL (150 s) means every cell still refreshes within 2-3 sweeps.
_REQ_SPACING = 3.0


class AdsbLolSource(Source):
    """Bootstrap feed from the public api.adsb.lol. Community coverage; switch to
    your own readsb feeders for production. Polls a shuffled 12-point grid at
    ~1 req/3 s, so one sweep takes ~35 s."""

    name = "adsblol"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "india-domestic-flight-tracker/0.1"},
        )

    async def fetch(self) -> list[dict]:
        out: dict[str, dict] = {}
        grid = GRID[:]
        random.shuffle(grid)
        limited = 0
        for i, (lat, lon) in enumerate(grid):
            try:
                r = await self._client.get(_BASE.format(lat=lat, lon=lon))
                if r.status_code == 429:
                    limited += 1
                else:
                    r.raise_for_status()
                    for ac in r.json().get("ac", []):
                        n = normalize_reapi(ac, self.name)
                        if n and n["lat"] is not None and n["lon"] is not None:
                            out[n["hex"]] = n
            except httpx.HTTPError as e:
                log.debug("cell %s,%s failed: %s", lat, lon, e)
            if i < len(grid) - 1:
                await asyncio.sleep(_REQ_SPACING)
        if limited:
            log.info("sweep: %d aircraft, %d/%d cells rate-limited", len(out), limited, len(grid))
        return list(out.values())

    async def aclose(self) -> None:
        await self._client.aclose()

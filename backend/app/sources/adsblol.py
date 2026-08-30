from __future__ import annotations

import asyncio

import httpx

from .base import Source, normalize_reapi

# 16-point grid, 250 nm radius per point -> covers the Chennai/Mumbai/Delhi/Kolkata
# FIRs plus the Andaman & Nicobar and Lakshadweep island groups.
GRID: list[tuple[int, int]] = [
    (lat, lon) for lat in (10, 17, 24, 31) for lon in (71, 78, 85, 92)
]

_BASE = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250"


class AdsbLolSource(Source):
    """Bootstrap feed from the public api.adsb.lol. Community coverage; use your own
    readsb feeders for production. Be gentle: this polls a 16-point grid per cycle."""

    name = "adsblol"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": "india-domestic-flight-tracker/0.1"},
        )

    async def fetch(self) -> list[dict]:
        out: dict[str, dict] = {}
        for lat, lon in GRID:
            try:
                r = await self._client.get(_BASE.format(lat=lat, lon=lon))
                r.raise_for_status()
            except httpx.HTTPError:
                continue
            for ac in r.json().get("ac", []):
                n = normalize_reapi(ac, self.name)
                if n and n["lat"] is not None:
                    out[n["hex"]] = n
            await asyncio.sleep(0.15)
        return list(out.values())

    async def aclose(self) -> None:
        await self._client.aclose()

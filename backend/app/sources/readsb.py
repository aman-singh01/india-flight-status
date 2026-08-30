from __future__ import annotations

import httpx

from .base import Source, normalize_reapi


class ReadsbSource(Source):
    """Your own ADS-B feeder. Points at the aircraft.json that readsb / tar1090 serves,
    e.g. http://192.168.1.50/tar1090/data/aircraft.json  (or /run/readsb/aircraft.json
    exposed over HTTP). Multiple feeders = multiple `readsb:<url>` entries in SOURCES."""

    name = "readsb"

    def __init__(self, url: str) -> None:
        self.url = url
        self._client = httpx.AsyncClient(timeout=8.0)

    async def fetch(self) -> list[dict]:
        r = await self._client.get(self.url)
        r.raise_for_status()
        data = r.json()
        out = []
        for ac in data.get("aircraft", []):
            n = normalize_reapi(ac, self.name)
            if n and n["lat"] is not None:
                out.append(n)
        return out

    async def aclose(self) -> None:
        await self._client.aclose()

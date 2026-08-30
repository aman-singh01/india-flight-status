from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .config import settings


@dataclass
class Tracked:
    hex: str
    callsign: str | None = None
    registration: str | None = None
    type: str | None = None
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    lat: float = 0.0
    lon: float = 0.0
    alt_ft: int | None = None
    gs_kt: float | None = None
    track_deg: float | None = None
    vs_fpm: int | None = None
    source: str = ""
    track: deque = field(default_factory=lambda: deque(maxlen=settings.track_maxlen))
    klass: dict | None = None  # cached domestic classification, or None if not a domestic flight


class LiveStore:
    """In-memory state of every aircraft currently in range, keyed by ICAO 24-bit hex."""

    def __init__(self) -> None:
        self._ac: dict[str, Tracked] = {}

    def upsert(self, items: list[dict]) -> None:
        now = time.time()
        for it in items:
            hexid = it["hex"]
            t = self._ac.get(hexid)
            if t is None:
                t = Tracked(hex=hexid, first_seen=now)
                self._ac[hexid] = t
            t.last_seen = now
            t.callsign = it.get("callsign") or t.callsign
            t.registration = it.get("registration") or t.registration
            t.type = it.get("type") or t.type
            t.source = it.get("source") or t.source
            if it.get("lat") is not None and it.get("lon") is not None:
                t.lat = it["lat"]
                t.lon = it["lon"]
                t.alt_ft = it.get("alt_ft")
                t.gs_kt = it.get("gs_kt")
                t.track_deg = it.get("track_deg")
                t.vs_fpm = it.get("vs_fpm")
                t.track.append((now, t.lat, t.lon, t.alt_ft))

    def prune(self) -> None:
        cutoff = time.time() - settings.stale_ttl
        for k in [k for k, v in self._ac.items() if v.last_seen < cutoff]:
            del self._ac[k]

    def get(self, hexid: str) -> Tracked | None:
        return self._ac.get(hexid)

    def all(self) -> list[Tracked]:
        return list(self._ac.values())


store = LiveStore()

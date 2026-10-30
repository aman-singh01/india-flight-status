"""Observed movements: takeoffs and landings seen in the live ADS-B stream, per airport.

The schedule board says what should happen; this log records what the transponders
showed. Four cases, because ADS-B coverage near the ground is patchy in India:

  takeoff           on the ground at an airport, then airborne and moving
  climb-out         first seen already low, climbing and close to an airport
  touchdown         low in the air, then on the ground at an airport
  lost-on-approach  last seen low and descending close to an airport (coverage ended
                    before the wheels touched, so the arrival is inferred)

Every event carries its `basis` and a `confidence` ("observed" or "inferred") so an
inference isn't passed off as a sighting. Only domestic flights are logged. State is
in memory, like the rest of the live store: a restart empties it.

Replayed over 16 h of real tracks, arrivals dominate (receivers hear descending aircraft
far better than climbing ones) and most are "lost-on-approach"; ~98% of those stayed down
for the next 15 minutes. "On the ground" is judged by speed as well as altitude, because
a parked jet can read a few hundred feet on the standard altimeter setting.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .config import settings
from .domestic import nearest_indian_airport
from .store import Tracked

GROUND_NM = 4.0  # on the ground this close to an airport's reference point
AIR_NM = 10.0  # airborne this close = in its circuit
LOW_FT = 3500  # below this an aircraft near an airport is climbing out or on approach
SLOW_KT = 50  # slower than this and low down, an aircraft is on the ground
FLYING_KT = 80  # an arrival was at least this fast on its last report; a taxiing jet is not
MAX_GAP_S = 300.0  # ground -> air (or air -> ground) only counts if the reports are this close
COOLDOWN_S = 1200.0  # the same aircraft isn't logged for the same airport and kind twice inside this
MAX_PER_AIRPORT = 300
_INFERRED = {"climb-out", "lost-on-approach"}


@dataclass
class _Seen:
    ts: float  # last_seen of the report this record was built from
    state: str | None  # "ground" | "air" | None (altitude unknown on a fast mover)
    alt: int | None
    vs: int | None
    gs: float | None
    lat: float
    lon: float
    info: dict = field(default_factory=dict)


def _state(alt: int | None, gs: float | None) -> str | None:
    if alt is None:
        # feeds report an aircraft on the surface as having no altitude
        return "ground" if gs is not None and gs <= 120 else None
    if alt < 75:
        return "ground"
    # A parked jet can read a few hundred feet: pressure altitude on a low-QNH day at a
    # sea-level field is about +350 ft. Nothing that flies is this slow this low.
    if gs is not None and gs < SLOW_KT and alt < LOW_FT:
        return "ground"
    return "air"


def _near(lat: float, lon: float, nm: float) -> str | None:
    return nearest_indian_airport(lat, lon, max_nm=nm)[0]


def _detect(prev: _Seen | None, cur: _Seen) -> tuple[str, str, str] | None:
    """(kind, airport, basis) if the step prev -> cur is a movement."""
    airborne = cur.state == "air" and cur.alt is not None
    close = prev is not None and cur.ts - prev.ts <= MAX_GAP_S
    # on the ground at A a moment ago, flying now: it took off (however high it is by now)
    if (
        airborne
        and close
        and prev.state == "ground"
        and (cur.gs or 0) >= FLYING_KT
        and (ap := _near(prev.lat, prev.lon, GROUND_NM))
    ):
        return "departure", ap, "takeoff"
    # first seen already low, fast and climbing close to an airport (no ground phase heard)
    if (
        airborne
        and prev is None
        and cur.alt < LOW_FT
        and (cur.vs or 0) >= 500
        and (cur.gs or 0) >= 100
        and (ap := _near(cur.lat, cur.lon, AIR_NM))
    ):
        return "departure", ap, "climb-out"
    # low and at flying speed a moment ago, on the ground at A now: it landed
    if (
        cur.state == "ground"
        and close
        and prev.state == "air"
        and prev.alt is not None
        and prev.alt < LOW_FT
        and (prev.gs or 0) >= FLYING_KT
        and (ap := _near(cur.lat, cur.lon, GROUND_NM))
    ):
        return "arrival", ap, "touchdown"
    return None


def _lost(prev: _Seen) -> tuple[str, str, str] | None:
    """An aircraft that dropped out of coverage: low, descending, near an airport = landed."""
    if (
        prev.state == "air"
        and prev.alt is not None
        and prev.alt < LOW_FT
        and (prev.vs or 0) <= -300
        and (ap := _near(prev.lat, prev.lon, AIR_NM))
    ):
        return "arrival", ap, "lost-on-approach"
    return None


class MovementLog:
    def __init__(self) -> None:
        self._seen: dict[str, _Seen] = {}
        self._by_airport: dict[str, deque] = {}
        self._last: dict[tuple[str, str, str], float] = {}

    def observe(self, tracked: list[Tracked], now: float | None = None) -> list[dict]:
        """Feed the current store; returns the movements newly detected."""
        now = now if now is not None else time.time()
        out: list[dict] = []
        present = {t.hex for t in tracked}
        for t in tracked:
            if not t.klass:  # domestic flights only
                self._seen.pop(t.hex, None)
                continue
            prev = self._seen.get(t.hex)
            if prev is not None and t.last_seen <= prev.ts:
                continue  # no new report since last cycle; don't replay stale state
            cur = _Seen(
                ts=t.last_seen,
                state=_state(t.alt_ft, t.gs_kt),
                alt=t.alt_ft,
                vs=t.vs_fpm,
                gs=t.gs_kt,
                lat=t.lat,
                lon=t.lon,
                info=_info(t),
            )
            self._seen[t.hex] = cur
            if (hit := _detect(prev, cur)) and (ev := self._emit(t.hex, cur, *hit)):
                out.append(ev)
        for hexid in [h for h in self._seen if h not in present]:  # pruned from the store
            prev = self._seen.pop(hexid)
            if (hit := _lost(prev)) and (ev := self._emit(hexid, prev, *hit)):
                out.append(ev)
        # the cooldown map only needs entries still inside the window
        for key in [k for k, ts in self._last.items() if now - ts > COOLDOWN_S]:
            del self._last[key]
        return out

    def _emit(self, hexid: str, s: _Seen, kind: str, airport: str, basis: str) -> dict | None:
        ts = s.ts  # when the aircraft reported it, not when we got round to it
        key = (hexid, airport, kind)
        if ts - self._last.get(key, -1e12) < COOLDOWN_S:
            return None
        self._last[key] = ts
        info = s.info
        other = info.get("arr") if kind == "departure" else info.get("dep")
        ev = {
            "airport": airport,
            "kind": kind,
            "ts": round(ts),
            "basis": basis,
            "confidence": "inferred" if basis in _INFERRED else "observed",
            "hex": hexid,
            "callsign": info.get("callsign"),
            "flight_no": info.get("flight_no"),
            "airline": info.get("airline"),
            "registration": info.get("registration"),
            "type": info.get("type"),
            "other": other if other != airport else None,
            "other_city": info.get("arr_city") if kind == "departure" else info.get("dep_city"),
        }
        self._by_airport.setdefault(airport, deque(maxlen=MAX_PER_AIRPORT)).append(ev)
        return ev

    def recent(
        self, airport: str, hours: float = 6.0, limit: int = 100, now: float | None = None
    ) -> list[dict]:
        """Movements at `airport` within the last `hours`, newest first."""
        cutoff = (now if now is not None else time.time()) - min(hours, settings.movements_keep_hours) * 3600
        rows = [e for e in self._by_airport.get(airport, ()) if e["ts"] >= cutoff]
        return sorted(rows, key=lambda e: e["ts"], reverse=True)[:limit]

    def stats(self) -> dict:
        return {
            "events": sum(len(d) for d in self._by_airport.values()),
            "airports": sum(1 for d in self._by_airport.values() if d),
            "watching": len(self._seen),
        }


def _info(t: Tracked) -> dict:
    k = t.klass or {}
    return {
        "callsign": t.callsign,
        "registration": t.registration,
        "type": t.type,
        "flight_no": k.get("flight_no"),
        "airline": k.get("airline"),
        "dep": k.get("dep"),
        "arr": k.get("arr"),
        "dep_city": k.get("dep_city"),
        "arr_city": k.get("arr_city"),
    }


movement_log = MovementLog()

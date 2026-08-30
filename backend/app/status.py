from __future__ import annotations

import math

from . import route as route_db
from .domestic import (
    INDIAN_AIRPORTS,
    _airlines,
    _haversine_nm,
    airline_from_callsign,
    flight_number,
)
from .store import store

# IATA -> ICAO airline code, derived from the airline table.
_IATA_TO_ICAO: dict[str, str] = {}
for _icao, _info in _airlines.items():
    _ia = (_info.get("iata") or "").upper()
    if _ia and _ia not in _IATA_TO_ICAO:
        _IATA_TO_ICAO[_ia] = _icao

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def _norm(q: str) -> str:
    return q.upper().replace(" ", "").replace("-", "").strip()


def candidate_callsigns(query: str) -> list[str]:
    """Every ADS-B callsign the user's input could plausibly mean."""
    q = _norm(query)
    out: list[str] = []

    def add(c: str) -> None:
        if c and c not in out:
            out.append(c)

    # ICAO airline prefix already, e.g. IGO203
    if len(q) >= 4 and q[:3].isalpha() and q[:3] in _airlines:
        add(q)
    # IATA airline prefix + number, e.g. 6E203 / AI2984 / QP1401 (a trailing
    # letter suffix on the number, as some ADS-B callsigns carry, is fine too)
    for n in (2, 3):
        head, tail = q[:n], q[n:]
        if tail and tail[0].isdigit() and head in _IATA_TO_ICAO:
            add(_IATA_TO_ICAO[head] + tail)
    add(q)  # last resort: match the raw string
    return out


def _bearing(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    dl = math.radians(to_lon - from_lon)
    y = math.sin(dl) * math.cos(math.radians(to_lat))
    x = math.cos(math.radians(from_lat)) * math.sin(math.radians(to_lat)) - math.sin(
        math.radians(from_lat)
    ) * math.cos(math.radians(to_lat)) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def nearest_place(lat: float, lon: float) -> str | None:
    best = None
    best_nm = 1e9
    for _iata, _icao, alat, alon, city in INDIAN_AIRPORTS:
        d = _haversine_nm(lat, lon, alat, alon)
        if d < best_nm:
            best_nm, best = d, (city, alat, alon)
    if best is None:
        return None
    city, alat, alon = best
    km = best_nm * 1.852
    if km < 6:
        return f"over {city}"
    word = _COMPASS[round(_bearing(alat, alon, lat, lon) / 22.5) % 16]
    return f"{km:.0f} km {word} of {city}"


def phase(alt: int | None, vs: int | None, gs: float | None) -> tuple[str, str]:
    vs = vs or 0
    gs = gs or 0
    if alt is None or alt < 75:
        if alt is None and gs > 120:
            return "Airborne", "altitude not reported"
        return ("On ground", "taxiing" if gs >= 40 else "at stand / parked")
    fl = f"FL{round(alt / 100):03d}"
    if alt < 10000 and vs > 400:
        return "Departed", f"climbing through {alt:,} ft"
    if alt < 13000 and vs < -400:
        return "On approach", f"descending through {alt:,} ft"
    if vs > 400:
        return "En route", f"climbing through {fl}"
    if vs < -400:
        return "En route", f"descending through {fl}"
    return "En route", f"cruising at {fl}"


def _match(query: str):
    cands = candidate_callsigns(query)
    q = _norm(query)
    exact = None
    loose = None
    for t in store.all():
        cs = (t.callsign or "").upper().replace(" ", "")
        if not cs:
            continue
        if cs in cands:
            exact = t
            break
        if loose is None:
            for c in cands:
                if cs.startswith(c) and len(cs) - len(c) <= 1:
                    loose = t
                    break
        if t.registration and _norm(t.registration) == q:
            exact = t
            break
        if t.hex.upper() == q:
            exact = t
            break
    return exact or loose, cands


def lookup(query: str) -> dict:
    hit, cands = _match(query)
    if hit is None:
        return {
            "query": query,
            "found": False,
            "tried": cands,
            "reason": (
                "No aircraft is transmitting this flight's callsign in coverage right now. "
                "It may not have departed yet, may already be parked, or may be outside "
                "ADS-B range (this feed covers Indian airspace)."
            ),
        }

    airline = airline_from_callsign(hit.callsign)
    k = hit.klass or {}
    status, detail = phase(hit.alt_ft, hit.vs_fpm, hit.gs_kt)
    return {
        "query": query,
        "found": True,
        "hex": hit.hex,
        "flight_no": k.get("flight_no") or flight_number(hit.callsign, airline) or (hit.callsign or "").strip(),
        "callsign": (hit.callsign or "").strip() or None,
        "airline": airline["name"] if airline else None,
        "registration": hit.registration,
        "aircraft_type": hit.type,
        "status": status,
        "detail": detail,
        "altitude_ft": hit.alt_ft,
        "ground_speed_kt": round(hit.gs_kt) if hit.gs_kt is not None else None,
        "vertical_rate_fpm": hit.vs_fpm,
        "heading_deg": round(hit.track_deg) if hit.track_deg is not None else None,
        "lat": round(hit.lat, 4),
        "lon": round(hit.lon, 4),
        "near": nearest_place(hit.lat, hit.lon),
        "origin": k.get("dep"),
        "destination": k.get("arr"),
        "origin_city": k.get("dep_city"),
        "destination_city": k.get("arr_city"),
        "route_src": k.get("route_src"),
        "schedule": route_db.schedule_summary(hit.callsign),
        "tracked_since": round(hit.first_seen),
        "last_update": round(hit.last_seen),
        "source": hit.source,
        "note": (
            "Position and phase are live from ADS-B. Route is from adsbdb's free "
            "routeset, or a keyed schedule API when configured (which also fills "
            "scheduled/estimated times, gate and delay)."
        ),
    }

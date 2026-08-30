from __future__ import annotations

import json
import math
from pathlib import Path

from . import route as route_db
from .config import settings

_DATA = Path(__file__).resolve().parent.parent / "data"

_airports: list[dict] = json.loads((_DATA / "airports_in.json").read_text(encoding="utf-8"))
_airlines: dict[str, dict] = json.loads((_DATA / "airlines_in.json").read_text(encoding="utf-8"))
_INDIAN_IATA: set[str] = {a["iata"] for a in _airports}

# (iata, icao, lat, lon, city)
INDIAN_AIRPORTS: list[tuple[str, str, float, float, str]] = [
    (a["iata"], a["icao"], a["lat"], a["lon"], a["city"]) for a in _airports
]

# Foreign airports close enough to India that an Indian carrier could reach them
# without leaving the India bounding box. Used to reject short international hops
# (e.g. DEL-KTM, MAA-CMB, CCU-DAC) that would otherwise look domestic.
FOREIGN_NEAR: list[tuple[str, float, float]] = [
    ("KTM", 27.6966, 85.3591),   # Kathmandu
    ("CMB", 7.1808, 79.8841),    # Colombo
    ("DAC", 23.8433, 90.3978),   # Dhaka
    ("CGP", 22.2496, 91.8133),   # Chittagong
    ("PBH", 27.4032, 89.4246),   # Paro
    ("MLE", 4.1918, 73.5291),    # Male
    ("LHE", 31.5216, 74.4036),   # Lahore
    ("KHI", 24.9065, 67.1608),   # Karachi
    ("ISB", 33.5490, 72.8258),   # Islamabad
]

_EARTH_NM = 3440.065


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_NM * math.asin(math.sqrt(a))


def in_bbox(lat: float, lon: float) -> bool:
    la_min, la_max, lo_min, lo_max = settings.india_bbox
    return la_min <= lat <= la_max and lo_min <= lon <= lo_max


def nearest_indian_airport(lat: float, lon: float, max_nm: float = 35.0) -> tuple[str | None, str | None]:
    best_iata: str | None = None
    best_city: str | None = None
    best_d = 1e9
    for iata, _icao, alat, alon, city in INDIAN_AIRPORTS:
        d = _haversine_nm(lat, lon, alat, alon)
        if d < best_d:
            best_d, best_iata, best_city = d, iata, city
    if best_iata is not None and best_d <= max_nm:
        return best_iata, best_city
    return None, None


def nearest_foreign(lat: float, lon: float, max_nm: float = 40.0) -> str | None:
    for iata, alat, alon in FOREIGN_NEAR:
        if _haversine_nm(lat, lon, alat, alon) <= max_nm:
            return iata
    return None


def airline_from_callsign(cs: str | None) -> dict | None:
    if not cs or len(cs) < 3:
        return None
    prefix = cs[:3].upper()
    info = _airlines.get(prefix)
    if info is None:
        return None
    return {"icao": prefix, **info}


def flight_number(cs: str | None, airline: dict | None) -> str | None:
    if not cs:
        return None
    if not airline:
        return cs
    tail = cs[3:].strip()
    tail = tail.lstrip("0") or tail
    iata = airline.get("iata")
    return f"{iata}{tail}" if iata and tail else cs


def classify(ac: dict, track: list[tuple[float, float, float, int | None]]) -> dict | None:
    """Decide whether an aircraft is currently operating a domestic Indian route.

    Heuristic (India-specific): domestic point-to-point flying is legally reserved
    for Indian-AOC carriers (cabotage), so:
      * the callsign must belong to a known Indian scheduled operator, and
      * the aircraft (and every track point we have) must be inside the India bbox, and
      * it must not be near a reachable foreign airport (KTM/CMB/DAC/...).
    Departure / arrival airports are inferred from low-altitude track endpoints.

    Returns a classification dict, or None if the flight is not domestic.
    """
    cs = ac.get("callsign")
    airline = airline_from_callsign(cs)
    reg = (ac.get("registration") or "").upper()

    is_indian_carrier = airline is not None
    is_vt = reg.startswith("VT")

    if not is_indian_carrier and not (settings.include_ga and is_vt):
        return None

    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None or not in_bbox(lat, lon):
        return None

    for _ts, tlat, tlon, _alt in track:
        if not in_bbox(tlat, tlon):
            return None  # this aircraft has crossed the border -> international

    if nearest_foreign(lat, lon) is not None:
        return None
    if track and nearest_foreign(track[0][1], track[0][2]) is not None:
        return None

    # Origin/destination from vertical state: a low, climbing aircraft is leaving
    # the airport under it; a low, descending one is arriving at the airport under
    # it. In cruise we can't tell without a schedule feed, so leave both blank.
    dep = arr = None
    alt = ac.get("alt_ft")
    vs = ac.get("vs_fpm") or 0
    if alt is not None and alt < 11000:
        if vs > 250:
            dep, _ = nearest_indian_airport(lat, lon, max_nm=45)
        elif vs < -250:
            arr, _ = nearest_indian_airport(lat, lon, max_nm=45)
        if track and len(track) >= 6:
            # if we've watched it climb out of somewhere, remember that as origin
            t0 = track[0]
            if t0[3] is not None and t0[3] < 8000:
                d0, _ = nearest_indian_airport(t0[1], t0[2], max_nm=35)
                if d0 and d0 != arr:
                    dep = d0

    return {
        "status": "domestic",
        "airline": airline["name"] if airline else "General Aviation",
        "airline_icao": airline["icao"] if airline else None,
        "flight_no": flight_number(cs, airline),
        "dep": dep,
        "arr": arr,
    }

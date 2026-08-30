from __future__ import annotations

from .status import nearest_place, phase
from .store import Tracked


def flight_dict(t: Tracked) -> dict | None:
    """Serialize a tracked aircraft for the API, or None if it is not a domestic flight."""
    if not t.klass:
        return None
    phase_name, phase_detail = phase(t.alt_ft, t.vs_fpm, t.gs_kt)
    return {
        "hex": t.hex,
        "callsign": t.callsign,
        "registration": t.registration,
        "type": t.type,
        "airline": t.klass["airline"],
        "airline_icao": t.klass["airline_icao"],
        "flight_no": t.klass["flight_no"],
        "dep": t.klass["dep"],
        "arr": t.klass["arr"],
        "dep_city": t.klass.get("dep_city"),
        "arr_city": t.klass.get("arr_city"),
        "route_src": t.klass.get("route_src"),
        "status": t.klass["status"],
        "phase": phase_name,
        "phase_detail": phase_detail,
        "near": nearest_place(t.lat, t.lon),
        "lat": round(t.lat, 5),
        "lon": round(t.lon, 5),
        "alt_ft": t.alt_ft,
        "gs_kt": round(t.gs_kt) if t.gs_kt is not None else None,
        "track_deg": round(t.track_deg) if t.track_deg is not None else None,
        "vs_fpm": t.vs_fpm,
        "first_seen": round(t.first_seen),
        "last_seen": round(t.last_seen),
        "source": t.source,
    }

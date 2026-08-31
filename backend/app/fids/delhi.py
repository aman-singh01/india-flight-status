from __future__ import annotations

import logging
import time

import httpx

from .base import (
    airline_name_to_icao,
    icao_callsign,
    ist_to_utc_iso,
    norm_flight_no,
    resolve_airport,
)

log = logging.getLogger("fids")

_BASE = "https://www.newdelhiairport.in/dial-api/flight-info/flight"
_UA = "Mozilla/5.0 (compatible; india-flight-status/1.0)"


def _phase(status: str | None, arriving: bool) -> tuple[str | None, str]:
    s = (status or "").lower()
    if "cancel" in s:
        return "Cancelled", "cancelled"
    if "not operating" in s:
        return None, ""  # drop these rows entirely
    if "divert" in s:
        return "Diverted", "diverted"
    if arriving:
        if "arriv" in s or "land" in s:
            return "Landed", "landed at Delhi"
        if "delay" in s:
            return "Delayed", "inbound to Delhi, delayed"
        return "En route", "inbound to Delhi"
    if "departed" in s:
        return "Departed", "departed Delhi"
    if "gate closed" in s:
        return "Boarding", "gate closed at Delhi"
    if "board" in s or "gate open" in s:
        return "Boarding", "boarding at Delhi"
    if "delay" in s:
        return "Delayed", "delayed at Delhi"
    return "Scheduled", "scheduled from Delhi"


class DelhiFids:
    """Delhi (DEL) schedule board from newdelhiairport.in's open dial-api."""

    airport = "DEL"
    name = "fids:del"

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=25.0, headers={"User-Agent": _UA, "Accept": "application/json"}
        )

    async def _leg(self, arriving: bool) -> list[dict]:
        path = "arrival" if arriving else "departure"
        r = await self._client.get(
            f"{_BASE}/{path}", params={"FltWay": "D", "Terminal": "", "search": "", "loadMore": "true"}
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return []
        return data

    async def fetch(self) -> list[dict]:
        rows: list[dict] = []
        for arriving in (False, True):
            try:
                legs = await self._leg(arriving)
            except (httpx.HTTPError, ValueError) as e:
                log.warning("fids:del %s fetch failed: %s", "arr" if arriving else "dep", e)
                continue
            for it in legs:
                row = _normalize(it, arriving)
                if row:
                    rows.append(row)
        return rows


def _normalize(it: dict, arriving: bool) -> dict | None:
    fno = norm_flight_no(it.get("FLIGHTNUMBER"))
    if not fno:
        return None
    phase, detail = _phase(it.get("FLIGHT_STATUS_DESCRIPTION"), arriving)
    if phase is None:
        return None

    other_iata, other_city = resolve_airport(it.get("AIRPORT_DESCRIPTION"))
    icao, airline = airline_name_to_icao(it.get("airline_name"))
    date_str = it.get("estimated_date_display") or it.get("FLIGHTDATE") or it.get("FDATE")
    sched_iso = ist_to_utc_iso(date_str, it.get("SCHEDULED_TIME") or "")
    gate_belt = (it.get("GATE_BELT") or "").strip().strip("_-").strip() or None
    terminal = (it.get("terminal") or "").strip().strip("_-").strip() or None
    sched_time = (it.get("SCHEDULED_TIME") or "").strip()

    if arriving:
        dep, dep_city, arr, arr_city = other_iata, other_city, "DEL", "Delhi"
        sched_dep, sched_arr = None, sched_iso
    else:
        dep, dep_city, arr, arr_city = "DEL", "Delhi", other_iata, other_city
        sched_dep, sched_arr = sched_iso, None

    bits = [detail]
    if sched_time:
        bits.append(f"{'arr' if arriving else 'dep'} {sched_time} IST")
    if gate_belt:
        bits.append(f"{'belt' if arriving else 'gate'} {gate_belt}")

    return {
        "hex": f"fids-{fno}",  # synthetic id; the board keys rows by hex
        "callsign": icao_callsign(fno),
        "registration": None,
        "type": None,
        "airline": airline,
        "airline_icao": icao,
        "flight_no": fno,
        "dep": dep,
        "arr": arr,
        "dep_city": dep_city,
        "arr_city": arr_city,
        "route_src": "fids",
        "schedule": {
            "sched_dep": sched_dep,
            "sched_arr": sched_arr,
            "est_arr": None,
            "delay_min": None,
            "sched_status": it.get("FLIGHT_STATUS_DESCRIPTION"),
            "gate": gate_belt,
            "terminal": terminal,
        },
        "status": "domestic",
        "phase": phase,
        "phase_detail": " · ".join(bits),
        "near": None,
        "lat": None,
        "lon": None,
        "alt_ft": None,
        "gs_kt": None,
        "track_deg": None,
        "vs_fpm": None,
        "first_seen": int(time.time()),
        "last_seen": int(time.time()),
        "source": "fids:del",
        "position": False,
        "_sched_ts": _epoch(sched_iso),
    }


def _epoch(iso: str | None) -> float:
    if not iso:
        return 0.0
    import datetime as _dt

    try:
        return _dt.datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0

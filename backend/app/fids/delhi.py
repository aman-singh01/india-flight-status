from __future__ import annotations

import logging

import httpx

from .base import build_row, ist_to_utc_iso, norm_flight_no, phase_for, resolve_airport

log = logging.getLogger("fids")

_BASE = "https://www.newdelhiairport.in/dial-api/flight-info/flight"
_UA = "Mozilla/5.0 (compatible; india-flight-status/1.0)"


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
        return data if isinstance(data, list) else []

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
    phase, detail = phase_for(it.get("FLIGHT_STATUS_DESCRIPTION"), arriving, "Delhi")
    if phase is None:
        return None

    other_iata, other_city = resolve_airport(it.get("AIRPORT_DESCRIPTION"))
    date_str = it.get("estimated_date_display") or it.get("FLIGHTDATE") or it.get("FDATE")
    sched_iso = ist_to_utc_iso(date_str, it.get("SCHEDULED_TIME") or "")
    gate_belt = (it.get("GATE_BELT") or "").strip().strip("_-").strip() or None
    terminal = (it.get("terminal") or "").strip().strip("_-").strip() or None
    stime = (it.get("SCHEDULED_TIME") or "").strip()

    if arriving:
        dep, dep_city, arr, arr_city, sd, sa = other_iata, other_city, "DEL", "Delhi", None, sched_iso
    else:
        dep, dep_city, arr, arr_city, sd, sa = "DEL", "Delhi", other_iata, other_city, sched_iso, None

    bits = [detail]
    if stime:
        bits.append(f"{'arr' if arriving else 'dep'} {stime} IST")
    if gate_belt:
        bits.append(f"{'belt' if arriving else 'gate'} {gate_belt}")

    return build_row(
        flight_no=fno,
        airline_name=it.get("airline_name"),
        dep=dep,
        arr=arr,
        dep_city=dep_city,
        arr_city=arr_city,
        sched_dep_iso=sd,
        sched_arr_iso=sa,
        status_raw=it.get("FLIGHT_STATUS_DESCRIPTION"),
        phase=phase,
        detail_bits=bits,
        gate=gate_belt,
        terminal=terminal,
        source="fids:del",
    )

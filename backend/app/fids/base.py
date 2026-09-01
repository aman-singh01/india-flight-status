from __future__ import annotations

import datetime as _dt
import re

from ..domestic import INDIAN_AIRPORTS, airline_from_callsign, flight_number

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

# name -> (iata, city) from the bundled airport table, for resolving the free-text
# "other airport" a FIDS row gives (e.g. "Bengaluru Intl Airport" -> BLR).
_AP_BY_NAME: dict[str, tuple[str, str]] = {}
_CITY_BY_IATA: dict[str, str] = {}
for _iata, _icao, _la, _lo, _city in INDIAN_AIRPORTS:
    _AP_BY_NAME[_city.lower()] = (_iata, _city)
    _CITY_BY_IATA[_iata] = _city


def city_of(iata: str | None) -> str | None:
    return _CITY_BY_IATA.get((iata or "").upper())


_STOPWORDS = re.compile(r"\b(intl|international|airport|apt|domestic|terminal|civil|aerodrome)\b")


def resolve_airport(desc: str | None) -> tuple[str | None, str | None]:
    """Free-text airport name -> (IATA, tidy city). (None, raw) if unrecognised."""
    if not desc:
        return None, None
    raw = desc.strip()
    key = _STOPWORDS.sub("", raw.lower()).strip(" .-")
    if key in _AP_BY_NAME:
        return _AP_BY_NAME[key]
    for name, (iata, city) in _AP_BY_NAME.items():
        if name and (name in key or key in name):
            return iata, city
    first = key.split()[0] if key.split() else ""
    for name, (iata, city) in _AP_BY_NAME.items():
        if first and name.split()[0] == first:
            return iata, city
    return None, raw


def ist_to_utc_iso(date_str: str, hhmm: str) -> str | None:
    """'2026-08-31' + '21:30' (IST) -> ISO-8601 UTC string."""
    try:
        y, m, d = (int(x) for x in date_str.split("-")[:3])
        hh, mm = (int(x) for x in hhmm.split(":")[:2])
        return _dt.datetime(y, m, d, hh, mm, tzinfo=_IST).astimezone(_dt.UTC).isoformat()
    except (ValueError, AttributeError):
        return None


def norm_flight_no(raw: str | None) -> str | None:
    return re.sub(r"\s+", "", raw).upper() if raw else None


def icao_callsign(flight_no: str | None) -> str | None:
    """'6E2361' -> 'IGO2361' (best-effort, for matching against ADS-B callsigns)."""
    m = re.match(r"^([0-9A-Z]{2})\s?(\d{1,4}[A-Z]?)$", (flight_no or "").upper())
    if not m:
        return None
    iata, num = m.group(1), m.group(2)
    for icao, info in _airlines_by_iata().items():
        if info == iata:
            return f"{icao}{num}"
    return None


def _airlines_by_iata() -> dict[str, str]:
    from ..domestic import _airlines

    return {icao: (info.get("iata") or "").upper() for icao, info in _airlines.items()}


def airline_name_to_icao(name: str | None) -> tuple[str | None, str | None]:
    """'INDIGO AIRLINES' -> ('IGO', 'IndiGo') via the bundled airline table.

    Also accepts a bare IATA/ICAO code ('6E', 'IGO') or a flight number ('6E123').
    """
    from ..domestic import _airlines

    if not name:
        return None, None
    tok = name.strip().upper()
    m = re.match(r"^([0-9A-Z]{2,3})\d", tok) or re.match(r"^([0-9A-Z]{2,3})$", tok)
    if m:
        code = m.group(1)
        if code in _airlines:
            return code, _airlines[code]["name"]
        for icao, info in _airlines.items():
            if (info.get("iata") or "").upper() == code:
                return icao, info["name"]
    n = re.sub(r"\b(airlines?|the|ltd|limited|aviation)\b", "", name.lower()).strip()
    for icao, info in _airlines.items():
        if n and n in info["name"].lower():
            return icao, info["name"]
    return None, name.title()


def phase_for(status: str | None, arriving: bool, place: str) -> tuple[str | None, str]:
    """Airport status text -> (phase badge, human detail). None phase = drop the row."""
    s = (status or "").lower()
    if "cancel" in s:
        return "Cancelled", "cancelled"
    if "not operating" in s or "no operation" in s:
        return None, ""
    if "divert" in s:
        return "Diverted", "diverted"
    if arriving:
        if "arriv" in s or "land" in s:
            return "Landed", f"landed at {place}"
        if "final" in s or "approach" in s:
            return "On approach", f"on approach to {place}"
        if "delay" in s:
            return "Delayed", f"inbound to {place}, delayed"
        return "En route", f"inbound to {place}"
    if "departed" in s or "airborne" in s or "left" in s:
        return "Departed", f"departed {place}"
    if "gate closed" in s:
        return "Boarding", f"gate closed at {place}"
    if "board" in s or "gate open" in s:
        return "Boarding", f"boarding at {place}"
    if "delay" in s:
        return "Delayed", f"delayed at {place}"
    return "Scheduled", f"scheduled from {place}"


def epoch_of(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return _dt.datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def build_row(
    *,
    flight_no: str,
    airline_name: str | None,
    dep: str | None,
    arr: str | None,
    dep_city: str | None,
    arr_city: str | None,
    sched_dep_iso: str | None,
    sched_arr_iso: str | None,
    status_raw: str | None,
    phase: str,
    detail_bits: list[str],
    gate: str | None,
    terminal: str | None,
    source: str,
) -> dict:
    """Assemble the board row shape shared by every FIDS scraper / the push ingest."""
    import time as _time

    icao, airline = airline_name_to_icao(airline_name)
    now = int(_time.time())
    return {
        "hex": f"fids-{flight_no}",  # synthetic id; the board keys rows by hex
        "callsign": icao_callsign(flight_no),
        "registration": None,
        "type": None,
        "airline": airline,
        "airline_icao": icao,
        "flight_no": flight_no,
        "dep": dep,
        "arr": arr,
        "dep_city": dep_city,
        "arr_city": arr_city,
        "route_src": "fids",
        "schedule": {
            "sched_dep": sched_dep_iso,
            "sched_arr": sched_arr_iso,
            "est_arr": None,
            "delay_min": None,
            "sched_status": status_raw,
            "gate": gate,
            "terminal": terminal,
        },
        "status": "domestic",
        "phase": phase,
        "phase_detail": " · ".join(b for b in detail_bits if b),
        "near": None,
        "lat": None,
        "lon": None,
        "alt_ft": None,
        "gs_kt": None,
        "track_deg": None,
        "vs_fpm": None,
        "first_seen": now,
        "last_seen": now,
        "source": source,
        "position": False,
        "_sched_ts": epoch_of(sched_dep_iso or sched_arr_iso),
    }


def normalize_pushed(airport_iata: str, it: dict) -> dict | None:
    """One item from `POST /ingest/fids` -> a board row. Loose input:

    {flight_no, airline, direction: "departure"|"arrival", other, sched_time,
     sched_date?, status, gate?, terminal?}
    """
    fno = norm_flight_no(it.get("flight_no"))
    if not fno:
        return None
    arriving = str(it.get("direction", "")).lower().startswith("arr")
    this_iata = airport_iata.upper()
    this_city = city_of(this_iata) or this_iata
    place = this_city
    phase, detail = phase_for(it.get("status"), arriving, place)
    if phase is None:
        return None

    other_iata, other_city = resolve_airport(it.get("other"))
    import datetime as _d

    date_str = it.get("sched_date") or _d.datetime.now(_IST).strftime("%Y-%m-%d")
    sched_iso = ist_to_utc_iso(date_str, it.get("sched_time") or "")
    gate = (str(it.get("gate") or "")).strip().strip("_-").strip() or None
    terminal = (str(it.get("terminal") or "")).strip().strip("_-").strip() or None
    stime = (str(it.get("sched_time") or "")).strip()

    if arriving:
        dep, dep_city, arr, arr_city = other_iata, other_city, this_iata, this_city
        sd, sa = None, sched_iso
    else:
        dep, dep_city, arr, arr_city = this_iata, this_city, other_iata, other_city
        sd, sa = sched_iso, None

    bits = [detail]
    if stime:
        bits.append(f"{'arr' if arriving else 'dep'} {stime} IST")
    if gate:
        bits.append(f"{'belt' if arriving else 'gate'} {gate}")

    return build_row(
        flight_no=fno,
        airline_name=it.get("airline"),
        dep=dep,
        arr=arr,
        dep_city=dep_city,
        arr_city=arr_city,
        sched_dep_iso=sd,
        sched_arr_iso=sa,
        status_raw=it.get("status"),
        phase=phase,
        detail_bits=bits,
        gate=gate,
        terminal=terminal,
        source=f"push:{this_iata}",
    )


__all__ = [
    "airline_from_callsign",
    "airline_name_to_icao",
    "build_row",
    "city_of",
    "epoch_of",
    "flight_number",
    "icao_callsign",
    "ist_to_utc_iso",
    "norm_flight_no",
    "normalize_pushed",
    "phase_for",
    "resolve_airport",
]

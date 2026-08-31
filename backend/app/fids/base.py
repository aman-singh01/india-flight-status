from __future__ import annotations

import datetime as _dt
import re

from ..domestic import INDIAN_AIRPORTS, airline_from_callsign, flight_number

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))

# name -> (iata, city) from the bundled airport table, for resolving the free-text
# "other airport" a FIDS row gives (e.g. "Bengaluru Intl Airport" -> BLR).
_AP_BY_NAME: dict[str, tuple[str, str]] = {}
for _iata, _icao, _la, _lo, _city in INDIAN_AIRPORTS:
    _AP_BY_NAME[_city.lower()] = (_iata, _city)

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
    """'INDIGO AIRLINES' -> ('IGO', 'IndiGo') via the bundled airline table."""
    from ..domestic import _airlines

    if not name:
        return None, None
    n = re.sub(r"\b(airlines?|the|ltd|limited|aviation)\b", "", name.lower()).strip()
    for icao, info in _airlines.items():
        if n and n in info["name"].lower():
            return icao, info["name"]
    return None, name.title()


__all__ = [
    "airline_from_callsign",
    "airline_name_to_icao",
    "flight_number",
    "icao_callsign",
    "ist_to_utc_iso",
    "norm_flight_no",
    "resolve_airport",
]

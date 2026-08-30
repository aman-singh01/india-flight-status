"""Pluggable keyed schedule providers.

Used only as a second stage, for callsigns the free adsbdb routeset doesn't know.
`build_provider()` returns None unless `SCHEDULE_PROVIDER` + `SCHEDULE_API_KEY` are
set, so the app runs fine without any key.

A provider's `route(flight_no, callsign)` returns, or None (or "ratelimited"):
    {
      "dep", "arr",                      IATA codes
      "dep_city", "arr_city",
      "dep_country", "arr_country",      ISO-2 (used for the domestic check)
      "sched_dep", "sched_arr",          ISO8601 UTC strings or None
      "est_arr",                         ISO8601 UTC (revised/predicted) or None
      "sched_status",                    provider status text or None
      "gate", "terminal",               or None
    }
"""

from __future__ import annotations

import datetime as _dt
import logging
import re

import httpx

from .config import settings

log = logging.getLogger("schedule")


def _parse_dt(node: dict | None) -> str | None:
    """AeroDataBox time node -> ISO8601 UTC string."""
    if not node:
        return None
    raw = node.get("utc") or node.get("local")
    if not raw:
        return None
    raw = raw.replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return _dt.datetime.strptime(raw[:16], fmt).replace(tzinfo=_dt.UTC).isoformat()
        except ValueError:
            continue
    return None


def _iso(s: str | None) -> str | None:
    """ISO8601 string (FlightAware style, may end in Z) -> normalized UTC ISO."""
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


class AeroDataBoxProvider:
    """AeroDataBox via RapidAPI (has a modest free tier). Flight-number lookup."""

    name = "aerodatabox"
    _HOST = "aerodatabox.p.rapidapi.com"

    def __init__(self, key: str) -> None:
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={
                "X-RapidAPI-Key": key,
                "X-RapidAPI-Host": self._HOST,
                "Accept": "application/json",
            },
        )

    async def route(self, flight_no: str, callsign: str | None = None) -> dict | None:
        num = flight_no.replace(" ", "").upper()
        url = f"https://{self._HOST}/flights/number/{num}"
        try:
            r = await self._client.get(url, params={"withAircraftImage": "false", "withLocation": "false"})
        except httpx.HTTPError as e:
            log.debug("aerodatabox %s: %s", num, e)
            return None
        if r.status_code == 429:
            log.warning("aerodatabox rate-limited (429) for %s", num)
            return "ratelimited"  # signal to back off
        if r.status_code in (401, 403):
            log.warning(
                "aerodatabox auth failed (%s) -- check the key is from the app subscribed "
                "to AeroDataBox: %s",
                r.status_code,
                r.text[:160],
            )
            return None
        if r.status_code == 204:
            return None  # AeroDataBox has no data for this flight number right now
        if r.status_code != 200:
            log.warning("aerodatabox %s -> HTTP %s: %s", num, r.status_code, r.text[:120])
            return None
        try:
            legs = r.json()
        except ValueError:
            return None
        if not isinstance(legs, list) or not legs:
            return None

        # a bare number (S5623, 6E544) can belong to a different carrier elsewhere
        # in the world -- keep only legs flown by the expected airline
        m = re.match(r"^([0-9A-Z]{2})", num)
        want_iata = m.group(1) if m else None
        if want_iata:
            matched = [
                lg for lg in legs if ((lg.get("airline") or {}).get("iata") or "").upper() == want_iata
            ]
            if matched:
                legs = matched  # prefer the leg actually flown by this carrier

        now = _dt.datetime.now(_dt.UTC)
        best, best_gap = None, 1e18
        for leg in legs:
            dep = leg.get("departure") or {}
            iso = _parse_dt(dep.get("scheduledTime"))
            gap = 0.0
            if iso:
                try:
                    gap = abs((_dt.datetime.fromisoformat(iso) - now).total_seconds())
                except ValueError:
                    gap = 1e17
            if gap < best_gap:
                best, best_gap = leg, gap
        if best is None:
            best = legs[0]

        d = best.get("departure") or {}
        a = best.get("arrival") or {}
        da = d.get("airport") or {}
        aa = a.get("airport") or {}
        dep_iata = (da.get("iata") or "").upper() or None
        arr_iata = (aa.get("iata") or "").upper() or None
        if not dep_iata or not arr_iata:
            return None
        return {
            "dep": dep_iata,
            "arr": arr_iata,
            "dep_city": da.get("municipalityName") or da.get("name"),
            "arr_city": aa.get("municipalityName") or aa.get("name"),
            "dep_country": (da.get("countryCode") or "").upper() or None,
            "arr_country": (aa.get("countryCode") or "").upper() or None,
            "sched_dep": _parse_dt(d.get("scheduledTime")),
            "sched_arr": _parse_dt(a.get("scheduledTime")),
            "est_arr": _parse_dt(a.get("revisedTime") or a.get("predictedTime") or a.get("runwayTime")),
            "sched_status": best.get("status"),
            "gate": a.get("gate") or d.get("gate"),
            "terminal": a.get("terminal") or d.get("terminal"),
        }

    async def aclose(self) -> None:
        await self._client.aclose()


class FlightAwareProvider:
    """FlightAware AeroAPI (aeroapi.flightaware.com). Pay-per-query; the key goes
    in SCHEDULE_API_KEY. Excellent coverage, ISO timestamps, gate/terminal/delay."""

    name = "flightaware"
    _BASE = "https://aeroapi.flightaware.com/aeroapi"
    _IN_ICAO = ("VA", "VE", "VI", "VO")  # India's airport ICAO prefixes
    _ACTIVE = {"en route", "taxiing", "departed", "airborne", "in flight", "arrived"}

    def __init__(self, key: str) -> None:
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"x-apikey": key, "Accept": "application/json"},
        )

    async def route(self, flight_no: str, callsign: str | None = None) -> dict | None:
        # AeroAPI takes ICAO or IATA idents; the ICAO callsign is the safer key.
        ident = (callsign or flight_no or "").replace(" ", "").upper()
        if not ident:
            return None
        url = f"{self._BASE}/flights/{ident}"
        try:
            r = await self._client.get(url, params={"max_pages": 1})
        except httpx.HTTPError as e:
            log.debug("flightaware %s: %s", ident, e)
            return None
        if r.status_code == 429:
            log.warning("flightaware rate-limited (429) for %s", ident)
            return "ratelimited"
        if r.status_code in (401, 403):
            log.warning(
                "flightaware auth failed (%s) -- check the AeroAPI key: %s", r.status_code, r.text[:160]
            )
            return None
        if r.status_code in (400, 404):
            return None  # unknown ident / no data
        if r.status_code != 200:
            log.warning("flightaware %s -> HTTP %s: %s", ident, r.status_code, r.text[:120])
            return None
        try:
            flights = r.json().get("flights") or []
        except ValueError:
            return None
        if not flights:
            return None

        now = _dt.datetime.now(_dt.UTC)

        def gap(fl: dict) -> float:
            for k in ("scheduled_out", "scheduled_off", "estimated_out"):
                iso = _iso(fl.get(k))
                if iso:
                    try:
                        return abs((_dt.datetime.fromisoformat(iso) - now).total_seconds())
                    except ValueError:
                        pass
            return 1e17

        active = [f for f in flights if (f.get("status") or "").lower() in self._ACTIVE]
        best = min(active or flights, key=gap)

        o = best.get("origin") or {}
        d = best.get("destination") or {}
        dep = (o.get("code_iata") or o.get("code_icao") or "").upper() or None
        arr = (d.get("code_iata") or d.get("code_icao") or "").upper() or None
        if not dep or not arr:
            return None

        def country(node: dict) -> str | None:
            return "IN" if (node.get("code_icao") or "").upper()[:2] in self._IN_ICAO else None

        return {
            "dep": dep,
            "arr": arr,
            "dep_city": o.get("city"),
            "arr_city": d.get("city"),
            "dep_country": country(o),
            "arr_country": country(d),
            "sched_dep": _iso(best.get("scheduled_out") or best.get("scheduled_off")),
            "sched_arr": _iso(best.get("scheduled_in") or best.get("scheduled_on")),
            "est_arr": _iso(
                best.get("estimated_in")
                or best.get("actual_in")
                or best.get("estimated_on")
                or best.get("actual_on")
            ),
            "sched_status": best.get("status"),
            "gate": best.get("gate_destination"),
            "terminal": best.get("terminal_destination"),
        }

    async def aclose(self) -> None:
        await self._client.aclose()


_PROVIDERS = {
    "aerodatabox": AeroDataBoxProvider,
    "flightaware": FlightAwareProvider,
}


def build_provider():
    name = (settings.schedule_provider or "").strip().lower()
    if not name:
        return None
    cls = _PROVIDERS.get(name)
    if cls is None:
        log.warning("unknown SCHEDULE_PROVIDER %r (have: %s)", name, ", ".join(_PROVIDERS))
        return None
    if not settings.schedule_api_key:
        log.warning("SCHEDULE_PROVIDER=%s set but SCHEDULE_API_KEY is empty -- skipping", name)
        return None
    log.info("schedule provider: %s", name)
    return cls(settings.schedule_api_key)

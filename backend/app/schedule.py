"""Pluggable keyed schedule providers.

Used only as a second stage, for callsigns the free adsbdb routeset doesn't know.
`build_provider()` returns None unless `SCHEDULE_PROVIDER` + `SCHEDULE_API_KEY` are
set, so the app runs fine without any key.

A provider's `route(flight_no)` returns, or None:
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
            return _dt.datetime.strptime(raw[:16], fmt).replace(tzinfo=_dt.timezone.utc).isoformat()
        except ValueError:
            continue
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

    async def route(self, flight_no: str) -> dict | None:
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
        if r.status_code != 200:
            return None
        try:
            legs = r.json()
        except ValueError:
            return None
        if not isinstance(legs, list) or not legs:
            return None

        now = _dt.datetime.now(_dt.timezone.utc)
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


_PROVIDERS = {"aerodatabox": AeroDataBoxProvider}


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

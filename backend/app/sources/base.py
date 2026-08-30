from __future__ import annotations


class Source:
    """A pluggable feed of aircraft observations."""

    name = "base"

    async def fetch(self) -> list[dict]:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


def norm_alt(v) -> int | None:
    if v is None or v == "ground":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def normalize_reapi(ac: dict, source: str) -> dict | None:
    """Normalize one aircraft record in the readsb / re-api ('aircraft.json') shape."""
    hexid = (ac.get("hex") or "").lower().strip()
    if not hexid or hexid.startswith("~"):  # ~ = non-ICAO address (TIS-B / ADS-R); skip
        return None
    lat, lon = ac.get("lat"), ac.get("lon")
    return {
        "hex": hexid,
        "callsign": (ac.get("flight") or "").strip() or None,
        "registration": ac.get("r"),
        "type": ac.get("t"),
        "lat": lat,
        "lon": lon,
        "alt_ft": norm_alt(ac.get("alt_baro")),
        "gs_kt": ac.get("gs"),
        "track_deg": ac.get("track"),
        "vs_fpm": norm_alt(ac.get("baro_rate")),
        "source": source,
    }

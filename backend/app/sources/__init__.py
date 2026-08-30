from __future__ import annotations

import logging

from .adsbfi import AdsbFiSource
from .adsblol import AdsbLolSource
from .airplaneslive import AirplanesLiveSource
from .base import Source
from .demo import DemoSource
from .readsb import ReadsbSource

log = logging.getLogger("sources")

_SIMPLE = {
    "demo": DemoSource,
    "adsblol": AdsbLolSource,
    "adsbfi": AdsbFiSource,
    "airplaneslive": AirplanesLiveSource,
}


def build_sources(spec: str) -> list[Source]:
    """Parse the SOURCES string. Multiple sources are unioned (deduped by ICAO hex,
    later source wins on conflict). e.g.
        adsblol,adsbfi
        readsb:http://pi1/tar1090/data/aircraft.json,readsb:http://pi2/..."""
    sources: list[Source] = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        kind, _, arg = item.partition(":")
        kind = kind.strip().lower()
        arg = arg.strip()
        try:
            if kind in _SIMPLE:
                sources.append(_SIMPLE[kind]())
            elif kind == "readsb":
                if not arg:
                    log.warning("readsb source needs a URL, e.g. readsb:http://host/data/aircraft.json")
                    continue
                sources.append(ReadsbSource(arg))
            else:
                log.warning(
                    "unknown source %r (want: %s | readsb:<url>)", item, " | ".join(_SIMPLE)
                )
        except Exception:
            log.exception("failed to build source %r", item)
    if not sources:
        log.warning("no valid sources in %r, falling back to demo", spec)
        sources.append(DemoSource())
    return sources

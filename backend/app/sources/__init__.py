from __future__ import annotations

import logging

from .adsblol import AdsbLolSource
from .base import Source
from .demo import DemoSource
from .readsb import ReadsbSource

log = logging.getLogger("sources")


def build_sources(spec: str) -> list[Source]:
    """Parse the SOURCES string, e.g. 'demo' or
    'readsb:http://pi1/tar1090/data/aircraft.json,readsb:http://pi2/...'."""
    sources: list[Source] = []
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        kind, _, arg = item.partition(":")
        kind = kind.strip().lower()
        arg = arg.strip()
        try:
            if kind == "demo":
                sources.append(DemoSource())
            elif kind == "adsblol":
                sources.append(AdsbLolSource())
            elif kind == "readsb":
                if not arg:
                    log.warning("readsb source needs a URL, e.g. readsb:http://host/data/aircraft.json")
                    continue
                sources.append(ReadsbSource(arg))
            else:
                log.warning("unknown source %r (want: demo | adsblol | readsb:<url>)", item)
        except Exception:
            log.exception("failed to build source %r", item)
    if not sources:
        log.warning("no valid sources in %r, falling back to demo", spec)
        sources.append(DemoSource())
    return sources

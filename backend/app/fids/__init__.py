"""Airport FIDS (Flight Information Display System) scrapers.

Unlike the ADS-B sources, these return *scheduled* flights -- every leg the airport
knows about, whether or not a transponder is visible -- so the board can show
grounded, boarding, delayed and cancelled flights too.

Only Delhi is implemented: its `dial-api` is an open JSON endpoint. The other big
Indian airports sit behind Akamai / edge bot protection that blocks server-side
requests, so there's nothing to scrape there.
"""

from __future__ import annotations

import logging

from .delhi import DelhiFids

log = logging.getLogger("fids")

_SCRAPERS = {"del": DelhiFids}


def build_fids(spec: str) -> list:
    out = []
    for raw in (spec or "").split(","):
        name = raw.strip().lower()
        if not name:
            continue
        cls = _SCRAPERS.get(name)
        if cls is None:
            log.warning("unknown FIDS source %r (have: %s)", name, ", ".join(_SCRAPERS))
            continue
        out.append(cls())
    return out

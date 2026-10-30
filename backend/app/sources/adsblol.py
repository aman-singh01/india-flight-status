from __future__ import annotations

from .base import GridPollSource


class AdsbLolSource(GridPollSource):
    """Public api.adsb.lol. Best free coverage over India, but it rate-limits
    anonymous callers to roughly ten requests per window (429 + Retry-After); the
    base class waits that out once per cell. ~25-40 s per sweep."""

    name = "adsblol"
    url_template = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"
    spacing = 3.0

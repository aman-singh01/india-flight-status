from __future__ import annotations

from .base import GridPollSource


class AdsbLolSource(GridPollSource):
    """Public api.adsb.lol. Best free coverage over India, but rate-limits
    anonymous callers hard (~half the grid 429s per sweep). ~35 s per sweep."""

    name = "adsblol"
    url_template = "https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/{dist}"
    spacing = 3.0

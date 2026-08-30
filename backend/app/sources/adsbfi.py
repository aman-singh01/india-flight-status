from __future__ import annotations

from .base import GridPollSource


class AdsbFiSource(GridPollSource):
    """Public opendata.adsb.fi. More generous rate limit than adsb.lol, but its
    feeder base is Europe-heavy -- coverage over India is currently sparse to
    nil, so it mostly fills the odd gap. Non-commercial use only."""

    name = "adsbfi"
    url_template = "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}"
    spacing = 1.5

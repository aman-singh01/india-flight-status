from __future__ import annotations

from .base import GridPollSource


class AirplanesLiveSource(GridPollSource):
    """api.airplanes.live. The public REST API is now gated -- it returns HTTP 403
    ("contact us...") unless your IP/project has been allow-listed by airplanes.live
    (email contact@airplanes.live). Once allow-listed it just works: no key needed,
    generous rate limit. Left out of the default SOURCES because it 403s otherwise."""

    name = "airplaneslive"
    url_template = "https://api.airplanes.live/v2/point/{lat}/{lon}/{dist}"
    spacing = 1.5

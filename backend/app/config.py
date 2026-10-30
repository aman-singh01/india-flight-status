from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config, loaded from environment / backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # demo | adsblol | adsbfi | opensky | readsb:<url>  (comma-separated, later wins on conflict)
    sources: str = "adsblol"
    # adsblol self-paces at ~25s/sweep; this just gates the WS push cadence.
    poll_interval: float = 5.0
    # retain an aircraft this many seconds after its last report -- long enough to
    # survive a sweep where its region was rate-limited.
    stale_ttl: float = 210.0

    # OpenSky Network (sources=...,opensky). OAuth2 client-credentials only (Basic auth
    # is no longer accepted); create an API client on the OpenSky account page. Blank
    # = anonymous. A /states/all call over the India bbox costs 4 credits, and the
    # daily budget is 400 anonymous (bucketed per IP -- exhausted on a shared host) /
    # 4000 registered, so the poll interval must stay >= ~90 s.
    opensky_client_id: str = ""
    opensky_client_secret: str = ""
    opensky_interval: float = 0.0  # 0 = auto (100 s authed, 900 s anonymous)
    include_ga: bool = False
    persist: bool = True
    db_path: str = "flights.db"
    track_maxlen: int = 900
    cors_origins: str = "*"

    # lat_min, lat_max, lon_min, lon_max  -- covers mainland India + Andaman & Nicobar + Lakshadweep.
    india_bbox: tuple[float, float, float, float] = (6.0, 37.5, 67.0, 98.5)

    # Optional keyed schedule API.
    #   schedule_provider="aerodatabox"  schedule_api_key="<RapidAPI key>"
    schedule_provider: str = ""
    schedule_api_key: str = ""
    # False: query only callsigns adsbdb can't resolve (route gap fill).
    # True:  query every domestic flight -> scheduled times / gate / delay for all,
    #        refreshed every schedule_refresh seconds. Watch your monthly quota.
    schedule_all: bool = False
    schedule_refresh: float = 1200.0
    schedule_max_per_hour: int = 30
    schedule_max_per_day: int = 300
    schedule_spacing: float = 4.0
    route_cache_path: str = "route_cache.json"

    # Airport FIDS scrapers -> scheduled flights on the board even with no transponder
    # in view (grounded / boarding / delayed / cancelled). Only "del" is implemented.
    fids_sources: str = "del"
    fids_refresh: float = 200.0  # seconds between FIDS polls
    fids_window_behind: float = 2.0  # hours: keep flights scheduled up to this long ago
    fids_window_ahead: float = 10.0  # hours: keep flights scheduled up to this far ahead
    # POST /ingest/fids lets a scraper run elsewhere push airports the app can't scrape;
    # disabled unless a token is set, and pushed rows expire.
    fids_ingest_token: str = ""
    fids_push_ttl: float = 600.0  # seconds a pushed airport's rows survive without a refresh


settings = Settings()

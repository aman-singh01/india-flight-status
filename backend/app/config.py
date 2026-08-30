from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config, loaded from environment / backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # demo | adsblol | readsb:<url>  (comma-separated, later wins on conflict)
    sources: str = "adsblol"
    # adsblol self-paces at ~25s/sweep; this just gates the WS push cadence.
    poll_interval: float = 5.0
    stale_ttl: float = 150.0
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


settings = Settings()

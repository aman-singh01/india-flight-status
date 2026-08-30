from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime config, loaded from environment / backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sources: str = "demo"
    poll_interval: float = 5.0
    stale_ttl: float = 90.0
    include_ga: bool = False
    persist: bool = True
    db_path: str = "flights.db"
    track_maxlen: int = 900
    cors_origins: str = "*"

    # lat_min, lat_max, lon_min, lon_max  -- covers mainland India + Andaman & Nicobar + Lakshadweep.
    india_bbox: tuple[float, float, float, float] = (6.0, 37.5, 67.0, 98.5)


settings = Settings()

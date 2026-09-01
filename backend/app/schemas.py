"""Pydantic response models -- these drive the OpenAPI schema at /docs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Schedule(BaseModel):
    sched_dep: str | None = Field(None, description="scheduled gate departure, ISO-8601 UTC")
    sched_arr: str | None = Field(None, description="scheduled gate arrival, ISO-8601 UTC")
    est_arr: str | None = Field(None, description="estimated/actual arrival, ISO-8601 UTC")
    delay_min: int | None = Field(None, description="arrival delay in minutes (negative = early)")
    sched_status: str | None = None
    gate: str | None = None
    terminal: str | None = None


class Flight(BaseModel):
    hex: str = Field(description="ICAO 24-bit address, lowercase hex")
    callsign: str | None = None
    registration: str | None = None
    type: str | None = Field(None, description="ICAO aircraft type code")
    airline: str | None = None
    airline_icao: str | None = None
    flight_no: str | None = Field(None, description="IATA flight number, e.g. 6E2416")
    dep: str | None = Field(None, description="origin IATA")
    arr: str | None = Field(None, description="destination IATA")
    dep_city: str | None = None
    arr_city: str | None = None
    route_src: str | None = Field(None, description='"schedule" | "inferred" | "fids" | null')
    schedule: Schedule | None = None
    status: str = "domestic"
    phase: str = Field(
        description="On ground | Departed | En route | On approach | Airborne | "
        "Scheduled | Boarding | Landed | Delayed | Cancelled | Diverted"
    )
    phase_detail: str
    near: str | None = Field(None, description='e.g. "34 km ESE of Mumbai"')
    position: bool = Field(True, description="false = scheduled-only row (no live transponder)")
    scheduled_status: str | None = Field(None, description="raw airport FIDS status, when matched")
    lat: float | None = None
    lon: float | None = None
    alt_ft: int | None = None
    gs_kt: int | None = None
    track_deg: int | None = None
    vs_fpm: int | None = None
    first_seen: int = Field(description="unix seconds")
    last_seen: int
    source: str


class FlightDetail(Flight):
    track: list[list[float]] = Field(
        default_factory=list, description="[[ts, lat, lon, alt_ft], ...] oldest first"
    )


class FlightsResponse(BaseModel):
    count: int
    flights: list[Flight]


class RouteStats(BaseModel):
    resolved: int
    unknown: int
    queued: int
    sched_queued: int
    sched_calls_1h: int
    sched_calls_24h: int


class BoardStats(BaseModel):
    rows: int
    in_window: int
    last_ok_age_s: int


class Health(BaseModel):
    ok: bool
    tracked: int
    domestic: int
    sources: str
    routes: RouteStats
    board: BoardStats


class Airport(BaseModel):
    iata: str
    icao: str
    name: str
    city: str
    lat: float
    lon: float


class AirportsResponse(BaseModel):
    count: int
    airports: list[Airport]


class AirportBoard(BaseModel):
    airport: Airport
    departures: list[Flight]
    arrivals: list[Flight]
    count: int


class AirlineInfo(BaseModel):
    name: str
    iata: str = ""
    callsign: str = ""


class Stats(BaseModel):
    total: int
    airborne: int
    by_airline: dict[str, int]


class HistorySpan(BaseModel):
    from_: int | None = Field(None, alias="from", description="oldest position, unix seconds")
    to: int | None = Field(None, description="newest position, unix seconds")
    rows: int


class HistoryAircraft(BaseModel):
    hex: str
    callsign: str | None = None
    ts: int
    lat: float
    lon: float
    alt_ft: int | None = None
    track_deg: int | None = None


class HistorySnapshot(BaseModel):
    at: int
    aircraft: list[HistoryAircraft]


class FlightStatus(BaseModel):
    query: str
    found: bool
    reason: str | None = Field(None, description="present when found is false")
    tried: list[str] | None = None
    hex: str | None = None
    flight_no: str | None = None
    callsign: str | None = None
    airline: str | None = None
    registration: str | None = None
    aircraft_type: str | None = None
    status: str | None = None
    detail: str | None = None
    altitude_ft: int | None = None
    ground_speed_kt: int | None = None
    vertical_rate_fpm: int | None = None
    heading_deg: int | None = None
    lat: float | None = None
    lon: float | None = None
    near: str | None = None
    origin: str | None = None
    destination: str | None = None
    origin_city: str | None = None
    destination_city: str | None = None
    route_src: str | None = None
    schedule: Schedule | None = None
    tracked_since: int | None = None
    last_update: int | None = None
    source: str | None = None
    note: str | None = None

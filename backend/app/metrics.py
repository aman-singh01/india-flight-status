"""Prometheus metrics, exposed at /metrics."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

ingest_cycles = Counter("tracker_ingest_cycles_total", "ingest cycles completed")
aircraft_tracked = Gauge("tracker_aircraft_tracked", "aircraft currently in the store")
flights_domestic = Gauge("tracker_flights_domestic", "domestic flights on the board")
routes_resolved = Gauge("tracker_routes_resolved", "callsign->route cache entries")
schedule_calls_24h = Gauge("tracker_schedule_calls_24h", "keyed schedule-API calls in the last 24h")
ws_clients = Gauge("tracker_ws_clients", "connected WebSocket clients")
source_aircraft = Gauge(
    "tracker_source_aircraft", "aircraft returned by a source on its last sweep", ["source"]
)


def render() -> tuple[bytes, str]:
    """(body, content-type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST

# India Flight Status

A live status board for domestic flights over India, built on public ADS-B data. Each
tracked flight is shown with its phase, route, altitude and speed; with an optional
schedule-API key it also carries scheduled and estimated times, gate and delay.

**Stack:** Python, FastAPI, SQLite, WebSockets; vanilla JavaScript with MapLibre GL on
the front end. No build step and no required API keys.

[![CI](https://github.com/aman-singh01/india-flight-status/actions/workflows/ci.yml/badge.svg)](https://github.com/aman-singh01/india-flight-status/actions/workflows/ci.yml)

**Live demo: <https://india-flight-status.onrender.com>** — hosted on a free tier, so
the first request after an idle period takes ~40 s to wake.

![Status board: every live domestic flight over India, each with its phase, route and a plain-language status line.](docs/board.png)

## Features

- **Status board** - one row per domestic flight: number, airline, aircraft type, a
  phase badge (En route / On approach / Boarding / Scheduled / Landed / Delayed /
  Cancelled …), origin, destination, and a status line such as *"descending through
  1,875 ft, 34 km ESE of Mumbai"* or *"boarding at Delhi · gate D22"*. Live ADS-B
  flights are unioned with the **Delhi airport schedule** (FIDS), so grounded, boarding
  and cancelled flights show too. Filter, sort, expand a row for detail.
- **Airport boards & deep-links** - click any airport code in a route for that
  airport's board, split into departures and arrivals. Every view is a shareable URL:
  `#airport/DEL`, `#flight/6E2361`, `#map`. `GET /api/airport/{iata}` serves the same
  split as JSON.
- **Live map** - aircraft as heading-aligned silhouettes coloured by altitude, with
  dead-reckoned motion between updates and a trail for the selected flight.
- **Historical playback** - a scrubber over persisted position history; replay any
  period at 60-900x.
- **Flight lookup** - `GET /api/status/{ident}` resolves a flight by IATA or ICAO flight
  number, registration or ICAO hex, even when it is filtered off the board.
- **Operational endpoints** - Prometheus metrics at `/metrics`, resolver statistics at
  `/api/health`.

![Airport board: Delhi departures and arrivals, split, at `#airport/DEL`.](docs/airport.png)

![Live map: aircraft as altitude-coloured, heading-aligned silhouettes over an Esri satellite basemap.](docs/map.jpg)

## Architecture

```mermaid
flowchart LR
  subgraph Sources["ADS-B sources (SOURCES env, merged by ICAO hex)"]
    A[adsb.lol grid poll]
    B[adsb.fi grid poll]
    E["OpenSky (bbox + MLAT)"]
    C["readsb (private feeder)"]
    D["demo (synthetic)"]
  end
  A --> I
  B --> I
  E --> I
  C --> I
  D --> I
  I[ingest loop: normalise + merge] --> S[(in-memory store + track history)]
  S --> K["classify(): domestic? + route"]
  K -->|callsign| R[route resolver]
  R --> R0[(routes_seed.json - shipped)]
  R --> R1[adsbdb.com free routeset]
  R --> R2["schedule API - AeroDataBox / FlightAware (optional key)"]
  R --> RC[(route_cache.json - live)]
  K --> M[flight record + phase + nearest place + schedule]
  F["Delhi FIDS scraper"] --> SB["schedule board (windowed)"]
  M --> MG["merge: ADS-B + schedule board"]
  SB --> MG
  MG --> WS[WebSocket /ws]
  MG --> API[REST /api/*]
  WS --> UI[status board + map]
  API --> UI
```

Design rationale and trade-offs are recorded in [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Implementation notes

- **Resilient, multi-network ingestion.** `adsb.lol` / `adsb.fi` are polled over a
  shuffled 12-point grid (~1 request / 3 s, rate-limited cells skipped not retried);
  `OpenSky` is a single bounding-box call against a different feeder network with MLAT,
  which roughly doubles the flights seen. Sources are merged and deduplicated by ICAO
  24-bit address, and aircraft are retained for `STALE_TTL` seconds so a starved sweep
  doesn't drop them.
- **Route resolution with a shipped seed.** A snapshot of ~1,000 resolved routes
  (`data/routes_seed.json`) ships with the app, so a cold start (e.g. a free host waking
  from sleep) isn't route-blind. On top of that: the free adsbdb routeset for anything
  new, then an optional keyed schedule API (AeroDataBox / FlightAware) for the remaining
  gaps, quota-guarded and cached to disk. Without a key the board still runs, showing
  route but no scheduled times.
- **Domain-aware classification.** A flight counts as domestic only when both endpoints
  are Indian airports, which also excludes international flights operated by Indian
  carriers. This follows India's cabotage rule: domestic point-to-point service is
  reserved for Indian-AOC operators.
- **Schedule board, not just transponders.** A scraper for Delhi's open `dial-api`
  pulls ~950 scheduled legs per poll and keeps the ~350–550 within a −2 h/+10 h window,
  then unions them with the ADS-B feed: a flight-number match (gated to Delhi-touching
  flights) enriches the live row with gate and airport status, the rest show
  position-less. On a measured afternoon that took the board to **555 flights (7 live
  ADS-B + 548 scheduled)** — En route 239, Scheduled 175, Landed 43, Departed 38,
  Boarding 26, Delayed 21, Cancelled 6 — states no ADS-B feed can produce. ~95 % of
  scraped rows carry a gate/belt. The other big airports (BOM / BLR / HYD / COK) sit
  behind Akamai / Radware / edge IP-blocks that reject datacenter traffic, so the app
  scrapes only Delhi itself; `POST /ingest/fids` + [`tools/fids_push.py`](tools/) let a
  scraper on a residential IP push those, keyed per airport and expiring after a TTL.
- **Smooth motion from a slow feed.** The client advances each aircraft along its track
  at ground speed at 20 fps and eases into each WebSocket update, so ~25 s server sweeps
  still render as continuous movement.
- **Extensible by design.** A new ADS-B source is one `GridPollSource` subclass; a new
  schedule provider is one class implementing `route(flight_no, callsign)`.

## Getting started

### Local

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # optional
uvicorn app.main:app --reload
```

Served at <http://localhost:8000>. Set `SOURCES=demo` in `.env` for a synthetic feed
that needs no network access.

### Docker

```bash
docker compose up --build
```

## Configuration

Set in `backend/.env`; see [`.env.example`](backend/.env.example) for the full list.

| Variable | Default | Purpose |
|---|---|---|
| `SOURCES` | `adsblol,adsbfi,opensky` | Comma-separated ingest sources, merged: `adsblol`, `adsbfi`, `opensky`, `demo`, `readsb:<url>` |
| `OPENSKY_USER` / `OPENSKY_PASS` | _(none)_ | Optional free OpenSky account -> 10 s poll instead of 300 s anonymous |
| `FIDS_SOURCES` | `del` | Airport FIDS scrapers for the schedule board; only `del` implemented, empty to disable |
| `POLL_INTERVAL` | `5` | WebSocket push interval (seconds) |
| `STALE_TTL` | `210` | Drop an aircraft after this many seconds unseen |
| `PERSIST` / `DB_PATH` | `true` / `flights.db` | SQLite position history |
| `SCHEDULE_PROVIDER` | _(none)_ | `aerodatabox` or `flightaware`; requires `SCHEDULE_API_KEY` |
| `SCHEDULE_ALL` | `false` | Query the schedule API for every flight, not only unresolved ones |
| `SCHEDULE_MAX_PER_HOUR` / `_PER_DAY` | `30` / `300` | Hard quota caps; calls pause when reached |

## API

| Endpoint | Description |
|---|---|
| `GET /api/flights` | Current domestic flights with route, phase and schedule |
| `GET /api/flights/{hex}` | One flight with its position trail |
| `GET /api/airport/{iata}` | One airport's board — departures and arrivals |
| `POST /ingest/fids` | Push an airport's FIDS rows (token-gated; see `tools/`) |
| `GET /api/status/{ident}` | Live status by flight number, registration or hex |
| `GET /api/history/span` &middot; `GET /api/history?at=` | Playback data range and a snapshot at time `at` |
| `GET /api/airports` &middot; `/api/airlines` &middot; `/api/stats` | Reference data and breakdowns |
| `GET /api/health` | Counts, active sources, resolver statistics |
| `GET /metrics` | Prometheus metrics |
| `WS /ws` | Pushes the full domestic list every poll |

Interactive OpenAPI schema at `/docs`.

## Tests and CI

```bash
pip install -r backend/requirements-dev.txt
pytest -q
ruff check backend/
black --check backend/app backend/tests
```

The unit tests cover the classification, route-inference, source-normalisation and
provider-parsing logic. CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs
linting, formatting, tests and a Docker build on every push and pull request.

## Deployment

- **Render** - New > Blueprint, point it at this repository; it reads
  [`render.yaml`](render.yaml). Free tier, no card required.
- **Fly.io** - `fly launch --copy-config --no-deploy`, then `fly deploy`
  ([`fly.toml`](fly.toml), region `bom`). Set the schedule key with
  `fly secrets set SCHEDULE_API_KEY=...`. Requires a payment method on file.

## Limitations

- Coverage: ~110–160 airborne flights from the free ADS-B feeds, plus ~350–550 Delhi
  legs from FIDS in the display window (varies with time of day) — a board of roughly
  400–560 versus ~2,800 domestic movements/day nationally. The app scrapes only Delhi
  (others block datacenter IPs); adding BOM/BLR/HYD needs `tools/fids_push.py` on a
  residential IP, a private `readsb` feeder, or a paid aggregator. ~4–5% of Delhi rows
  have a destination the bundled airport table can't map to an IATA code (shown by
  city name).
- Scheduled times, gate and delay require a keyed schedule provider.
- Air India files many domestic legs under opaque ATC callsigns (`AIC2CE`) that
  don't encode the marketed flight number. A FlightAware key resolves these
  automatically (its `ident_iata`), or add rows to
  [`data/callsign_flightno.json`](backend/data/callsign_flightno.json) by hand;
  otherwise the board shows the callsign rather than inventing an `AI2CE`.
- The shipped route seed is a point-in-time snapshot; airlines renumber flights
  seasonally, so a stale seed entry can misroute until it's regenerated.
- Track-history origin inference needs a feed that captures departures and is inactive
  on the public feed.

## License

MIT - see [LICENSE](LICENSE). ADS-B data is used under each provider's community terms;
military and state aircraft positions are not published.

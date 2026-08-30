# India Domestic Flight Tracker

Live map of **domestic** flights over India. An aircraft is shown only when it is
operating an India-internal route (Indian-AOC carrier, both endpoints in India);
international flights and overflights are filtered out.

```
ADS-B source(s) ──> ingest loop ──> in-memory state (keyed by ICAO hex)
   demo | adsblol | readsb          + domestic classifier
                                    + SQLite position history (optional)
                                        │
                        REST /api/*  +  WebSocket /ws
                                        │
                          MapLibre GL web frontend
```

## Quick start

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell:  .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env             # then edit if you like
uvicorn app.main:app --reload
```

Open <http://localhost:8000>. Out of the box it runs the `demo` source
(synthetic domestic traffic between real Indian airports — no network needed),
so you should see ~45 aircraft moving immediately.

## Data sources (`SOURCES` in `.env`)

Comma-separated, evaluated left → right (a later source wins on conflict).

| Value | What it is | Use for |
|---|---|---|
| `demo` | Synthetic flights between real Indian airports | Offline dev, UI work |
| `adsblol` | Public `api.adsb.lol`, 16-point grid over the Indian FIRs | Bootstrap / first look |
| `readsb:<url>` | Your own feeder's `aircraft.json` | **Production** |

Example once your feeders are up:

```
SOURCES=readsb:http://192.168.1.50/tar1090/data/aircraft.json,readsb:http://192.168.1.51/tar1090/data/aircraft.json
```

## Running your own ADS-B feeders (recommended)

Each RTL-SDR receiver covers ~250–400 km line-of-sight. Place them near the metros
you care about (DEL, BOM, BLR, MAA, HYD, CCU…) and near approach corridors.

Per receiver (Raspberry Pi + RTL-SDR v3 dongle + 1090 MHz antenna):

1. Install **readsb** + **tar1090** (e.g. the `wiedehopf/adsb-scripts` installers).
2. Confirm `http://<pi-ip>/tar1090/` shows aircraft.
3. Add `readsb:http://<pi-ip>/tar1090/data/aircraft.json` to `SOURCES`.
4. Optionally also feed adsb.lol / adsb.fi / FlightAware from the same Pi to get
   their wider network back (and, from FlightAware, a free API key).

Receive-only SDR needs no licence in India. Do **not** transmit. Keep the feed
list to receivers you control.

## How "domestic" is decided

`app/domestic.py`. A flight is domestic when **all** hold:

- callsign prefix matches a known Indian scheduled operator (`data/airlines_in.json`) —
  India's cabotage rules reserve domestic point-to-point flying for Indian carriers,
  so this is a strong signal;
- the aircraft, and every track point collected since first seen, is inside the
  India bounding box;
- it is not within ~40 nm of a reachable foreign airport (KTM, CMB, DAC, CGP, PBH,
  MLE, LHE, KHI, ISB) — this rejects short international hops that never leave the box.

Departure / arrival airports are inferred from low-altitude track endpoints against
`data/airports_in.json` (~90 airports; replace with an OurAirports `IN` extract for
full coverage).

**Known limitations:** during cruise, an Indian-carrier flight to Kathmandu/Colombo
can briefly look domestic until it descends; a flight already airborne when first
seen has no departure airport; `INCLUDE_GA=true` adds VT- bizjets that stay inside
the box but have no airline/route.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | counts + active sources |
| `GET /api/flights` | all current domestic flights |
| `GET /api/flights/{hex}` | one flight + position trail |
| `GET /api/airports` | bundled Indian airport list |
| `GET /api/airlines` | Indian airline callsign table |
| `GET /api/stats` | totals + breakdown by airline |
| `WS  /ws` | pushes the full domestic list every poll |

## Layout

```
backend/
  app/
    main.py         FastAPI app, routes, static mount, lifespan tasks
    ingest.py       poll loop: fetch -> merge -> store -> classify -> persist
    store.py        in-memory aircraft state + per-aircraft track deque
    domestic.py     domestic-route classifier, airport/airline lookups
    sources/        demo.py | adsblol.py | readsb.py  (shared re-api normalizer)
    db.py           optional SQLite position history
    ws.py           websocket broadcast
    models.py       Tracked -> API dict
  data/             airlines_in.json, airports_in.json
frontend/           index.html, app.js, style.css  (MapLibre GL, no build step)
```

Frontend notes: the basemap is Esri's no-key dark raster tiles; planes are DOM
markers and the selected-flight trail is a `<canvas>` overlay (both projected via
`map.project`), so nothing depends on MapLibre's vector worker. Swap `BASE_STYLE`
in `app.js` for a vector style (OpenFreeMap / MapTiler / your own) if you want
sharper labels.

## Roadmap

- Airport pages: live arrival / departure boards per Indian airport
- Historical playback (scrub a day of traffic from SQLite / TimescaleDB)
- Alerts ("VT-XXX just departed BLR")
- Delay stats per airline / airport
- Swap SQLite → Postgres + PostGIS + TimescaleDB for real history
- Filter/label state aircraft (IAF/BSF/VIP) explicitly

## Notes

- Respect each source's terms (adsb.lol / adsb.fi are community, non-abusive use).
- Do not publish military or state aircraft positions.
- Times are UTC internally; display in IST (UTC+5:30) in the UI layer.

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

## Origin / destination

`app/route.py` resolves `callsign → origin/destination`:

1. **adsbdb.com** — free community routeset, no key, for every Indian-carrier
   callsign. A background loop drains a queue at ~2.5 req/s; the cache is written
   to `route_cache.json` so restarts don't re-fetch.
2. **Keyed schedule API** (optional) — tried *only* for the callsigns adsbdb
   doesn't have (odd ferry/positioning callsigns), quota-guarded. Set
   `SCHEDULE_PROVIDER=aerodatabox` + `SCHEDULE_API_KEY=<RapidAPI key>`. This also
   fills scheduled / estimated times, gate, and arrival delay.
3. **Fallback** — if neither has it: arrival inferred from a low-altitude descent
   near an airport, else `••• → •••`.

A known route is authoritative for the domestic check: a flight is domestic only
if **both** endpoints are in India — this also drops international flights that
Indian carriers operate (e.g. `AIC356` BOM–HND).

Without a schedule key, expect ~85–90% of live flights to show a real route once
the resolver warms up; with one, nearly all.

**Other limitations:** a flight already airborne when first seen and with no route
yet shows `••• → •••` until adsbdb/schedule resolves it; `INCLUDE_GA=true` adds
VT- bizjets that have no airline/route.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | counts + active sources + route-resolver stats |
| `GET /api/flights` | all current domestic flights (with `dep`/`arr`/`schedule`) |
| `GET /api/status/{q}` | live status by flight no / registration / hex |
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
- Delay stats per airline / airport (needs a schedule key)
- More schedule providers (AviationStack, FlightAware AeroAPI)
- Swap SQLite → Postgres + PostGIS + TimescaleDB for real history
- Filter/label state aircraft (IAF/BSF/VIP) explicitly

## Notes

- Respect each source's terms (adsb.lol / adsb.fi are community, non-abusive use).
- Do not publish military or state aircraft positions.
- Times are UTC internally; display in IST (UTC+5:30) in the UI layer.

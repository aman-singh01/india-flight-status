# Architecture decisions

Short ADR-style notes on the load-bearing choices. Each is a trade-off, not a
universal truth — the context is a single-instance, near-real-time tracker for
Indian domestic airspace fed mostly by rate-limited public APIs.

---

## 1. In-memory aircraft state, SQLite only for history

**Decision.** Live state (every aircraft in range, keyed by ICAO 24-bit address,
with a per-aircraft position deque) lives in a plain `dict` in the process. SQLite
is written to opportunistically and only for *historical* positions.

**Why.** The working set is tiny — a few hundred aircraft, each a handful of floats
and a bounded deque. A round-trip to a database per WebSocket push (every ~5 s, to
every client) would add latency and complexity for no benefit at this scale. The
data is also inherently ephemeral: an aircraft that stops transmitting for
`STALE_TTL` seconds is gone. SQLite earns its place only for "what was the track an
hour ago", which the map's trail and any future playback need.

**Cost / when it breaks.** State is lost on restart (routes are re-resolved from the
disk cache; positions repopulate within a sweep). Horizontal scaling would need the
store moved to Redis and the WebSocket fan-out decoupled. Both are noted in the
roadmap; neither is worth doing before there's a second instance.

---

## 2. Sources are a *union*, not a fallback chain

**Decision.** `SOURCES` is a comma list; every source is polled each cycle and the
results are merged, deduped by ICAO hex (a later source wins a conflict).

**Why.** No single free feed has good coverage over India. `adsb.lol` is best but
rate-limits hard; `adsb.fi` is gentler but currently sparse here; a user's own
`readsb` feeder is complete but only within its receiver's range. "Try A, else B"
would throw away B's unique aircraft. Union + dedup keeps whatever any source can
see, and adding a feeder later just increases coverage with no code change.

**Cost.** More outbound requests per cycle, and a slow/failing source can drag a
cycle out. Mitigated by per-source request spacing and treating a failed grid cell
as "skip this sweep", not "retry into the wall".

---

## 3. "Domestic" is a heuristic, anchored on a legal fact

**Decision.** A flight is domestic if its callsign is an Indian scheduled operator
**and** (when a route is known) both endpoints are Indian airports; otherwise a
geometry fallback (inside the India bbox, not near a reachable foreign airport).

**Why.** There is no feed of "this flight is domestic". But India's cabotage rules
reserve domestic point-to-point flying for Indian-AOC carriers, so the callsign
prefix is a strong, cheap signal. Layering the both-endpoints-in-India check on top
of a resolved route removes the false positives the callsign alone can't — e.g. an
`AI` flight from Mumbai to Tokyo.

**Cost.** A brand-new Indian regional carrier not in `airlines_in.json` is missed
until added. During the window before a route resolves, an international flight by an
Indian carrier can briefly show as domestic, then drops off. Both are acceptable for
a tracker whose whole premise is "domestic".

---

## 4. No frontend framework, no build step

**Decision.** The UI is one `index.html`, one `app.js`, one `style.css`, served as
static files by the same FastAPI process. Assets are cache-busted with a `?v=N`
query string.

**Why.** The app is a status board and a map — a few hundred lines of DOM updates
driven by one WebSocket. A framework + bundler would add a toolchain, a `node_modules`,
and a build stage to CI for no reduction in this code's size or clarity. "Clone,
`pip install`, run" with nothing else is a feature. MapLibre GL is the only runtime
dependency and it loads from a CDN.

**Cost.** Manual DOM manipulation and no component model; the cache-bust bump is a
step to remember on frontend changes. If the UI grew a third view with shared state,
this would stop paying off.

---

## 5. Route resolution is two-stage, key optional

**Decision.** Stage 1 is `adsbdb.com` (free, no key) for every Indian-carrier
callsign. Stage 2 is an optional keyed schedule API (AeroDataBox or FlightAware),
tried only for the callsigns stage 1 can't resolve — behind a per-hour/per-day quota
guard, with an on-disk cache so restarts don't re-spend quota.

**Why.** Most routes are resolvable for free; paying per query for those would be
waste. The keyed API is genuinely better for the odd ferry/positioning callsigns and
adds scheduled times / gate / delay — but its free tiers are tiny, so the default is
"fill only the gaps" and the quota guard makes exhaustion a graceful pause, not an
error. The app must run with no key at all.

**Cost.** Route coverage on the free path plateaus around 75–90 %; the rest need a
key or the user's feeders (which also activate track-history origin inference). The
provider abstraction (`route(flight_no, callsign)`) keeps adding a third provider to
one class.

---

## 6. A shipped route seed, not a live schedule feed

**Decision.** `data/routes_seed.json` — a committed snapshot of ~1,000 callsign →
route entries resolved from adsbdb — is loaded at startup as the base layer, with
the live disk cache overlaid on top. The alternative considered was a *schedule-first*
design: poll a provider's airport departures/arrivals endpoints so every scheduled
flight is a row whether or not ADS-B sees it.

**Why.** Schedule-first is how commercial products (MMT, Google Flights) get to "every
flight" — but the airport-schedule endpoints are unit-expensive and none of the free
tiers sustain continuous coverage of even six airports. The seed gets the cheap 80 %
of that benefit for free: a cold start on a host that sleeps (the free deploy target)
begins knowing most routes instead of hammering adsbdb from zero, which also cuts
rate-limiting on the resolver.

**Cost.** The seed is point-in-time; a seasonally renumbered flight can carry a stale
route until the seed is regenerated from `route_cache.json`. It does nothing for
completeness — a flight ADS-B can't see is still absent. Schedule-first stays the
right move the day a paid key or an FIDS scraper is in the budget.

---

## 7. OpenSky joins the union as a bounding-box source

**Decision.** Add OpenSky Network alongside the grid-poll sources: one
`/states/all` call for the whole India bbox per cycle, self-throttled (10 s with a
free account, 300 s anonymous) and re-serving its last result in between.

**Why.** It roughly doubled the flights seen in testing (~46 → ~110 at evening peak)
— a different feeder network plus MLAT surfaces Mode-S-only airframes and low
climb-outs the `adsb.lol` grid misses. It doesn't fit `GridPollSource` (one wide
query, not a tiled one) so it's a plain `Source` with its own state-vector
normaliser. `adsb.one` was evaluated and rejected — it sits behind a Cloudflare
bot-wall that blocks server-side clients.

**Cost.** Anonymous OpenSky is capped near 400 calls/day, so without an account the
positions can be up to 300 s stale (the client still shows them, dead-reckoned).
Unit conversions (m→ft, m/s→kt/fpm) are one more place for an off-by-a-factor bug,
covered by a normaliser test.

---

## 8. A schedule board from Delhi FIDS, unioned with the feed

**Decision.** `board.py` polls a scraper for Delhi's `dial-api` (its open FIDS
JSON), turns every leg into a schedule row, keeps the ones within a time window of
now, and `merge()`s them with the ADS-B domestic list: a flight-number match
enriches the live row, unmatched rows are appended position-less. `Flight.lat` /
`lon` became optional and a `position` flag was added; the frontend skips
plotting a null-position row and hides its "track on map" button.

**Why.** ADS-B can only ever show airborne aircraft in range — never a flight
that's boarding, delayed on the ground, or cancelled. The airport already
publishes all of that. Delhi is the only big Indian airport whose site isn't
behind Akamai / edge bot protection, and it's the largest by movements, so one
scraper roughly quadruples what the board shows for flights touching Delhi.
Building it as a *union* (not a replacement) keeps the live map and dead-reckoning
working unchanged for the ADS-B half.

**Cost.** Delhi-only, and dependent on one undocumented endpoint that can change
or block. A reused flight number on an unrelated sector could mis-enrich, so the
match is gated to flights that touch Delhi (or have no known route yet). ~4% of
FIDS destinations don't resolve to an IATA code in the bundled table. No revised
*times* are exposed by the endpoint, only a revised date and a status string.

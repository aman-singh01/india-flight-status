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

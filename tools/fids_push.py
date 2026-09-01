#!/usr/bin/env python3
"""Scrape airport FIDS boards and push them to India Flight Status.

The big Indian airports (BOM / BLR / HYD / COK) block datacenter IPs, so the app
can't scrape them from its host. Run this on a machine with a normal residential
connection (your PC, a home Pi) and it POSTs the rows to `/ingest/fids`, where
they're merged into the board just like the built-in Delhi feed.

Zero dependencies -- standard library only.

    python fids_push.py --once --airport DEL --dry-run     # prove the pipeline
    python fids_push.py                                    # loop, all configured

Config via environment (or edit the defaults below):

    APP_URL        base URL of the app        (default: the Render deploy)
    INGEST_TOKEN   must equal the app's FIDS_INGEST_TOKEN   (required to POST)
    AIRPORTS       comma list to scrape       (default: BOM,BLR,HYD)
    INTERVAL       seconds between cycles      (default: 180)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

APP_URL = os.environ.get("APP_URL", "https://india-flight-status.onrender.com").rstrip("/")
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")
AIRPORTS = [a.strip().upper() for a in os.environ.get("AIRPORTS", "BOM,BLR,HYD").split(",") if a.strip()]
INTERVAL = float(os.environ.get("INTERVAL", "180"))

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"


def _get_json(url: str, headers: dict | None = None, timeout: float = 25.0):
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json", **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - trusted URLs
        return json.loads(r.read().decode("utf-8", "replace"))


# --------------------------------------------------------------------------------
# One function per airport. Each returns a list of loose flight dicts:
#   {"flight_no", "airline"?, "direction": "departure"|"arrival", "other"?,
#    "sched_time": "HH:MM", "sched_date"? "YYYY-MM-DD", "status"?, "gate"?, "terminal"?}
# The server normalises the rest (phase, IATA resolution, IST->UTC).
# --------------------------------------------------------------------------------


def scrape_del() -> list[dict]:
    """Delhi -- open dial-api, works from anywhere. Reference implementation."""
    base = "https://www.newdelhiairport.in/dial-api/flight-info/flight"
    out: list[dict] = []
    for direction in ("departure", "arrival"):
        rows = _get_json(f"{base}/{direction}?FltWay=D&Terminal=&search=&loadMore=true")
        if not isinstance(rows, list):
            continue
        for it in rows:
            out.append(
                {
                    "flight_no": it.get("FLIGHTNUMBER"),
                    "airline": it.get("airline_name"),
                    "direction": direction,
                    "other": it.get("AIRPORT_DESCRIPTION"),
                    "sched_time": it.get("SCHEDULED_TIME"),
                    "sched_date": it.get("estimated_date_display") or it.get("FLIGHTDATE"),
                    "status": it.get("FLIGHT_STATUS_DESCRIPTION"),
                    "gate": it.get("GATE_BELT"),
                    "terminal": it.get("terminal"),
                }
            )
    return out


def scrape_bom() -> list[dict]:
    """Mumbai (csmia.adani.com). TODO from a residential IP:

    1. Open https://csmia.adani.com/ (or the current CSMIA site) in Chrome.
    2. Go to the flight-status page; open DevTools -> Network -> filter XHR/Fetch.
    3. Find the request that returns the departures/arrivals list (JSON).
    4. Reproduce it here with urllib and map its fields to the dict shape above.
    """
    return []


def scrape_blr() -> list[dict]:
    """Bengaluru (bengaluruairport.com). React SPA behind Akamai -- from a real
    browser session the flight list XHR is reachable; find it the same way as BOM."""
    return []


def scrape_hyd() -> list[dict]:
    """Hyderabad (hyderabad.aero). 503s to datacenter IPs; usually fine residential."""
    return []


def scrape_cok() -> list[dict]:
    """Cochin (cial.aero). Radware bot check -- realistic headers + a residential IP
    usually pass; find the FIDS endpoint via DevTools."""
    return []


SCRAPERS = {
    "DEL": scrape_del,
    "BOM": scrape_bom,
    "BLR": scrape_blr,
    "HYD": scrape_hyd,
    "COK": scrape_cok,
}


def push(airport: str, flights: list[dict], dry_run: bool) -> None:
    payload = json.dumps({"airport": airport, "flights": flights}).encode()
    if dry_run:
        print(f"  [dry-run] would POST {len(flights)} rows for {airport}")
        if flights:
            print("  sample:", json.dumps(flights[0], indent=2))
        return
    req = urllib.request.Request(
        f"{APP_URL}/ingest/fids",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {INGEST_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            print(f"  {airport}: {r.read().decode()}")
    except urllib.error.HTTPError as e:
        print(f"  {airport}: HTTP {e.code} {e.read().decode()[:200]}", file=sys.stderr)


def cycle(airports: list[str], dry_run: bool) -> None:
    for a in airports:
        fn = SCRAPERS.get(a)
        if fn is None:
            print(f"  {a}: no scraper", file=sys.stderr)
            continue
        try:
            flights = fn()
        except Exception as e:  # noqa: BLE001
            print(f"  {a}: scrape failed: {e}", file=sys.stderr)
            continue
        if not flights:
            print(f"  {a}: 0 rows (stub not implemented, or blocked)")
            continue
        push(a, flights, dry_run)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="one cycle then exit")
    ap.add_argument("--dry-run", action="store_true", help="print instead of POST")
    ap.add_argument("--airport", action="append", help="override AIRPORTS (repeatable)")
    args = ap.parse_args()

    airports = [a.upper() for a in args.airport] if args.airport else AIRPORTS
    if not args.dry_run and not INGEST_TOKEN:
        sys.exit("INGEST_TOKEN is not set (must match the app's FIDS_INGEST_TOKEN)")

    print(f"fids_push -> {APP_URL}  airports={airports}  interval={INTERVAL}s")
    while True:
        print(time.strftime("%Y-%m-%d %H:%M:%S"))
        cycle(airports, args.dry_run)
        if args.once:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()

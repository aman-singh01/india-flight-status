# `fids_push.py` — residential FIDS scraper

The app scrapes **Delhi** itself (its `dial-api` is open). The other big airports
(**BOM, BLR, HYD, COK**) sit behind Akamai / Radware / edge IP-blocks that reject
datacenter traffic — so they can only be scraped from a normal home connection.

This script runs on your machine, scrapes those boards, and POSTs the rows to the
app's `POST /ingest/fids`, where they merge into the board exactly like the Delhi
feed. Pushed rows expire after `FIDS_PUSH_TTL` seconds (default 600) if the script
stops, so a dead scraper doesn't leave stale flights on the board.

Zero dependencies — standard-library Python 3.9+.

## 1. Turn ingest on (once)

`POST /ingest/fids` returns **503** until `FIDS_INGEST_TOKEN` is set on the server.

1. Generate a token:

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(36))"
   ```

2. Render dashboard → **`india-flight-status`** → **Environment** → **Add Environment
   Variable**:

   | Key | Value |
   |---|---|
   | `FIDS_INGEST_TOKEN` | *(the generated token)* |

3. **Save Changes** — Render redeploys (~3–5 min).

Keep the token out of git — it lives only in the Render dashboard and in your local
shell env (below). It's a shared secret between the server and this script; anyone
with it can push rows to the board.

**Sanity check** once the redeploy is done:

```bash
curl -X POST https://india-flight-status.onrender.com/ingest/fids \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"airport":"DEL","flights":[{"flight_no":"6E9","direction":"departure","sched_time":"10:00","status":"On Time"}]}'
```

`{"airport":"DEL","accepted":1,"dropped":0}` = working. `401` = wrong token. `503` =
`FIDS_INGEST_TOKEN` still not set / redeploy not finished.

## 2. Prove the pipeline

Delhi works from any IP, so use it to check end to end:

```bash
export APP_URL=https://india-flight-status.onrender.com
export INGEST_TOKEN=<the same string>
python fids_push.py --once --airport DEL --dry-run   # see the rows it would send
python fids_push.py --once --airport DEL             # actually POST them
```

Then check `GET /api/health` → `board.sources` should show `push:DEL`.

## 3. Fill in the airports you want

Each `scrape_*()` in `fids_push.py` for BOM/BLR/HYD/COK is a stub returning `[]`.
To implement one:

1. Open that airport's flight-status page in Chrome **on this machine**.
2. DevTools → **Network** → filter **Fetch/XHR** → reload.
3. Find the request that returns the departures / arrivals list as JSON.
4. Reproduce it in the stub with `urllib` and map its fields to:
   `{"flight_no", "airline"?, "direction": "departure"|"arrival", "other"?,
     "sched_time": "HH:MM", "sched_date"? "YYYY-MM-DD", "status"?, "gate"?, "terminal"?}`

`scrape_del()` is a complete worked example.

## 4. Run it continuously

```bash
export AIRPORTS=BOM,BLR,HYD
python fids_push.py            # loops every INTERVAL seconds (default 180)
```

**Windows** — Task Scheduler → Create Task → Trigger "At log on" → Action:
`python.exe C:\path\to\tools\fids_push.py`, "Start in" = the `tools` folder, and
set the env vars in the task or a `.bat` wrapper.

**Linux/Mac** — a `@reboot` cron entry, or a tiny systemd user service running
`python fids_push.py`.

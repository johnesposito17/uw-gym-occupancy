# UW–Madison Rec Well occupancy — data collection

Appends a snapshot of live facility occupancy at the two UW–Madison Rec Well
buildings (Nicholas Recreation Center and Bakke Recreation & Wellbeing Center)
every 10 minutes. Phase 1 of a portfolio project; the goal here is a clean,
gap-honest time series to model later.

## Where the numbers come from

Not from `services.recwell.wisc.edu/FacilityOccupancy` — that page currently
renders **no** occupancy data (it's empty even in a real browser). The live
numbers come from the **Connect2Concepts / GoBoard** widget that the official
RecWell site embeds (linked from `recwell.wisc.edu/locations/nick/`):

```
https://goboardapi.azurewebsites.net/api/FacilityCount/GetCountsByAccount?AccountAPIKey=7938fc89-a15c-492d-9566-12c961bc1f27
```

It returns a JSON array, one object per zone (~27 zones across the two
buildings: tracks, courts, pools, fitness floors, the ice center, etc.).

### Feed quirks worth knowing (they shaped the schema)

- **The current count is `LastCount`, not `CountOfParticipants`.** In this feed
  `CountOfParticipants` and `PercetageCapacity` are `0` for every zone — dead
  fields. We read `LastCount` and compute `pct_full` ourselves.
- **Closed ≠ empty.** The feed has an `IsClosed` boolean. A closed zone and a
  zone with zero people are different states; we keep `IsClosed` as its own
  column so the distinction survives into the model.
- **The feed's timestamp is naive Central time.** `LastUpdatedDateAndTime` has
  no timezone; it's wall-clock at the facility (America/Chicago). We store both
  the raw string and a UTC conversion so it's never ambiguous (DST-safe).
- **Zones update on their own schedules.** At any fetch some zones may be
  minutes-to-hours stale — compare `source_updated_at_utc` to `observed_at_utc`
  to judge freshness.

## Schema (`data/occupancy.csv`)

Append-only. One row per zone per fetch.

| column                  | meaning |
|-------------------------|---------|
| `observed_at_utc`       | when this collector fetched, UTC ISO-8601 (our clock — the one we fully trust) |
| `source_updated_at_raw` | feed's own timestamp, verbatim (naive Central) |
| `source_updated_at_utc` | same instant converted to UTC; blank if unparseable |
| `facility`              | building name, e.g. `Nicholas Recreation Center` |
| `zone`                  | sub-location, e.g. `Nick Level 3 Fitness` |
| `count`                 | current occupancy (from `LastCount`) |
| `capacity`              | posted capacity for the zone |
| `pct_full`              | `count / capacity`, blank if capacity ≤ 0 |
| `is_closed`             | `True`/`False` from the feed's `IsClosed` |
| `location_id`           | stable per-zone id — the best join key |
| `facility_id`           | parent building id |
| `flags`                 | data-quality flags, `;`-joined; blank = clean. **Never** used to drop a row |

`flags` values: `negative_count`, `over_capacity` (count exceeds capacity),
`missing_count`. Outliers are flagged and kept, never silently dropped.

## Cadence & gaps

Target cadence is **every 10 minutes**. Two runners exist:

- **Primary: local `launchd` agent** (`deploy/edu.wisc.gymoccupancy.collector.plist`
  → `run_collect.sh`). GitHub Actions' scheduled cron proved too unreliable on this
  repo (it dropped ~16 of every 17 runs), so a `launchd` job on the collecting Mac
  runs `collect.py` every 10 min and pushes here. Reliable *while that Mac is awake*;
  laptop sleep shows up as honest gaps (launchd doesn't back-fill).
- **Backstop: GitHub Actions** (`.github/workflows/collect.yml`, `*/10`). Left in
  place to catch intervals when the Mac is off. Same append-only, fail-loud logic.

Either way: **gaps are expected and honest.** A failed fetch writes nothing and exits
non-zero; nothing is ever back-filled or imputed. Change cadence via the plist's
`StartInterval` (seconds) and/or the workflow's `cron:` line — no code change.

### Managing the local agent

```bash
# install / reload
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/edu.wisc.gymoccupancy.collector.plist
# stop / uninstall
launchctl bootout   gui/$(id -u)/edu.wisc.gymoccupancy.collector
# is it loaded? what was its last exit code?
launchctl print     gui/$(id -u)/edu.wisc.gymoccupancy.collector | grep -iE 'state|last exit'
# live logs
tail -f launchd.out.log launchd.err.log
```
To install on a different Mac, edit the absolute paths in `run_collect.sh` and the
plist (repo location, `python3`, and `CONTACT_EMAIL`), copy the plist into
`~/Library/LaunchAgents/`, and bootstrap it.

## Monitoring (freshness watchdog)

A gap you don't notice is a gap you can't act on. `check_freshness.py` runs hourly
(`.github/workflows/freshness.yml`), reads the newest `observed_at_utc`, and **fails
the run if it's older than 24 hours** — which turns the job red and triggers
GitHub's built-in failure email to the repo owner. (The threshold is 24h, not minutes,
because the primary collector is a laptop `launchd` job: normal overnight sleep leaves
data briefly stale, and a tight limit would email every night. 24h still catches a real
multi-day break. Tighten it via `--max-age-min` if the Mac runs ~24/7.) It's separate from the collector
so it catches the worst case: collection stopping *entirely* (no failed collector
runs to notice, because there are no runs at all).

```bash
python3 check_freshness.py                 # OK, or non-zero + reason if stale
python3 check_freshness.py --max-age-min 60
```

Its one blind spot: if GitHub Actions itself stops scheduling, this checker won't run
either. The robust upgrade is a **dead-man's-switch** — have `collect.py` ping a
service like [healthchecks.io](https://healthchecks.io) on each success and let *it*
alert when the pings stop. Not set up yet; the in-repo check is the zero-account
first line of defense.

## Load it into pandas

```python
import pandas as pd
df = pd.read_csv("data/occupancy.csv", parse_dates=["observed_at_utc", "source_updated_at_utc"])
df["is_closed"] = df["is_closed"].map({"True": True, "False": False})
# example: busiest zones right now
df.sort_values("observed_at_utc").groupby("zone").tail(1).sort_values("pct_full", ascending=False)
```

## Run it yourself

```bash
export CONTACT_EMAIL="you@wisc.edu"   # goes in the polite User-Agent
python3 collect.py            # fetch once, append to data/occupancy.csv
python3 collect.py --dry-run  # print what it would write, write nothing
```

Zero third-party dependencies — Python 3.9+ standard library only.
(macOS python.org builds may need `export SSL_CERT_FILE=/etc/ssl/cert.pem` or a
one-time run of *Install Certificates.command* for TLS to verify.)

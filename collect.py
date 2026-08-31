#!/usr/bin/env python3
"""Collect UW-Madison Rec Well live facility occupancy, one fetch per run.

Source: the Connect2Concepts / GoBoard feed that the official RecWell site embeds
(https://recwell.wisc.edu/locations/nick/ links to it). We hit its JSON API directly.

The InnoSoft page at services.recwell.wisc.edu/FacilityOccupancy renders NOTHING
right now (empty even in a real browser), so this is the live source, not that page.

Design rules (Phase 1 == reliable collection only):
  - Append-only. Never rewrite history.
  - On ANY failure: log to stderr, exit non-zero, write NOTHING. A gap in the data
    must be visible as a gap, never papered over with a partial or imputed row.
  - Zero third-party dependencies (stdlib only) so it can't rot from a bad pin.

Usage:
  python3 collect.py            # fetch once and append rows to data/occupancy.csv
  python3 collect.py --dry-run  # print the rows it WOULD write, write nothing
  python3 collect.py --output /path/to.csv   # override the CSV path
"""

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Configuration -----------------------------------------------------------

# The public account key from the widget the RecWell site embeds. Not a secret;
# it's in the page source of recwell.wisc.edu. If the numbers ever go stale,
# re-check that page for a new key.
API_URL = (
    "https://goboardapi.azurewebsites.net/api/FacilityCount/GetCountsByAccount"
    "?AccountAPIKey=7938fc89-a15c-492d-9566-12c961bc1f27"
)

# Polite, identifying User-Agent. TODO: put a real contact address here so RecWell/
# Connect2Concepts can reach you if the polling is ever a problem. Override with the
# CONTACT_EMAIL env var without editing the file.
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "REPLACE_ME@wisc.edu")
USER_AGENT = f"uw-gym-occupancy-collector (student data-science project; {CONTACT_EMAIL})"

REQUEST_TIMEOUT_SECONDS = 30

# The feed's own timestamps ("2026-08-31T13:24:22.617") carry no timezone. They are
# wall-clock time at the facility, i.e. America/Chicago. We keep the raw string AND a
# UTC conversion so nothing is ambiguous later (DST would otherwise bite us twice a year).
FACILITY_TZ = ZoneInfo("America/Chicago")

# Column order for the CSV. Changing this after data exists is expensive — append new
# columns at the end if you must, never reorder or rename in place.
FIELDNAMES = [
    "observed_at_utc",       # when THIS script fetched (UTC, our clock)
    "source_updated_at_raw",  # feed's own timestamp, verbatim (naive Central)
    "source_updated_at_utc",  # same instant converted to UTC (unambiguous)
    "facility",               # e.g. "Nicholas Recreation Center"
    "zone",                   # e.g. "Nick Level 3 Fitness"
    "count",                  # current occupancy (from LastCount — see note below)
    "capacity",               # posted capacity for the zone
    "pct_full",               # count / capacity, blank if capacity <= 0
    "is_closed",              # feed's IsClosed flag: closed != count­==0
    "location_id",            # stable per-zone id (best join key)
    "facility_id",            # parent facility id
    "flags",                  # data-quality flags; blank == clean. Never used to DROP.
]


def fetch(url):
    """GET the feed and return parsed JSON. Raises on any HTTP/network/JSON problem."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        if resp.status != 200:
            raise RuntimeError(f"unexpected HTTP status {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def to_utc(raw):
    """Convert a naive Central timestamp string to a UTC ISO-8601 string.

    Returns "" if the value is missing or unparseable — we never guess, and a blank
    is honest about what we don't know.
    """
    if not raw:
        return ""
    try:
        # fromisoformat handles the fractional seconds the feed sends.
        naive = datetime.fromisoformat(raw)
    except ValueError:
        return ""
    local = naive.replace(tzinfo=FACILITY_TZ)
    return local.astimezone(timezone.utc).isoformat()


def build_rows(payload, observed_at_utc):
    """Turn the feed's list of zones into our row dicts. Validates but never drops."""
    rows = []
    for z in payload:
        count = z.get("LastCount")           # NOTE: NOT CountOfParticipants — that field
        capacity = z.get("TotalCapacity")    # is 0 for every zone in this feed. LastCount
                                             # is the real current number.

        # pct_full only when capacity is a usable positive number.
        if isinstance(capacity, (int, float)) and capacity > 0 and isinstance(count, (int, float)):
            pct_full = round(count / capacity, 4)
        else:
            pct_full = ""

        # Plausibility checks: flag, don't drop. A weird row we can see beats a missing
        # row we can't. An analyst downstream decides what to do with a flagged row.
        flags = []
        if isinstance(count, (int, float)) and count < 0:
            flags.append("negative_count")
        if (
            isinstance(count, (int, float))
            and isinstance(capacity, (int, float))
            and capacity > 0
            and count > capacity
        ):
            flags.append("over_capacity")
        if count is None:
            flags.append("missing_count")

        rows.append({
            "observed_at_utc": observed_at_utc,
            "source_updated_at_raw": z.get("LastUpdatedDateAndTime") or "",
            "source_updated_at_utc": to_utc(z.get("LastUpdatedDateAndTime")),
            "facility": (z.get("FacilityName") or "").strip(),
            "zone": (z.get("LocationName") or "").strip(),
            "count": count if count is not None else "",
            "capacity": capacity if capacity is not None else "",
            "pct_full": pct_full,
            "is_closed": z.get("IsClosed"),
            "location_id": z.get("LocationId"),
            "facility_id": z.get("FacilityId"),
            "flags": ";".join(flags),
        })
    return rows


def append_rows(path, rows):
    """Append rows to the CSV, writing the header first if the file is new/empty."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Collect UW Rec Well live occupancy.")
    parser.add_argument("--dry-run", action="store_true",
                        help="print rows that would be written, write nothing")
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parent / "data" / "occupancy.csv",
                        help="CSV path to append to")
    args = parser.parse_args()

    # Our own fetch time, in UTC, to the second. This is the one timestamp we fully trust.
    observed_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        payload = fetch(API_URL)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"[{observed_at_utc}] FETCH FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[{observed_at_utc}] BAD JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # An empty list is a valid JSON response but a broken state for us: we KNOW this
    # account reports ~27 zones. Treat it as a failure so the gap stays visible rather
    # than silently logging a run that captured nothing.
    if not isinstance(payload, list) or len(payload) == 0:
        print(f"[{observed_at_utc}] EMPTY FEED: got {type(payload).__name__} "
              f"with {len(payload) if isinstance(payload, list) else '?'} zones",
              file=sys.stderr)
        sys.exit(1)

    rows = build_rows(payload, observed_at_utc)

    if args.dry_run:
        writer = csv.DictWriter(sys.stdout, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
        print(f"\n[dry-run] {len(rows)} rows from {len(payload)} zones — nothing written.",
              file=sys.stderr)
        return

    append_rows(args.output, rows)
    print(f"[{observed_at_utc}] wrote {len(rows)} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()

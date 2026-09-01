#!/usr/bin/env python3
"""Fail loudly if occupancy collection has gone stale.

The collector (collect.py) runs every 10 minutes. If it silently stops committing
new rows — the API key rotated, the feed changed shape, GitHub stopped scheduling
it — nothing else would tell us, and a gap you don't notice is a gap you can't act
on. This script reads the newest observed_at_utc in the CSV and exits non-zero when
it's too old. Run on a schedule, that turns the job RED, which triggers GitHub's
built-in failure email to the repo owner.

Limitation: this can't catch GitHub Actions being down entirely (then this checker
wouldn't run either). The more robust upgrade for that is a dead-man's-switch — have
collect.py ping a service like healthchecks.io on success, and let IT alert when the
pings stop. See README. This in-repo check is the zero-account first line of defense.

Usage:
  python3 check_freshness.py                  # stale if newest row > 90 min old
  python3 check_freshness.py --max-age-min 60
"""

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


def newest_observation(path):
    """Return the newest observed_at_utc in the CSV as an aware datetime, or None."""
    newest = None
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw = row.get("observed_at_utc")
            if not raw:
                continue
            try:
                ts = datetime.fromisoformat(raw)
            except ValueError:
                continue
            # observed_at_utc is always written UTC-aware, but be defensive.
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if newest is None or ts > newest:
                newest = ts
    return newest


def main():
    p = argparse.ArgumentParser(description="Alert if occupancy data is stale.")
    p.add_argument("--input", type=Path,
                   default=Path(__file__).resolve().parent / "data" / "occupancy.csv")
    p.add_argument("--max-age-min", type=float, default=90.0,
                   help="fail if the newest observation is older than this many minutes "
                        "(default 90 — comfortably past normal 10-min cadence + GitHub "
                        "cron jitter, so it only fires on a real outage)")
    args = p.parse_args()

    now = datetime.now(timezone.utc)

    if not args.input.exists() or args.input.stat().st_size == 0:
        print(f"STALE: {args.input} is missing or empty", file=sys.stderr)
        sys.exit(1)

    newest = newest_observation(args.input)
    if newest is None:
        print("STALE: no parseable observed_at_utc in the CSV", file=sys.stderr)
        sys.exit(1)

    age_min = (now - newest).total_seconds() / 60.0
    if age_min > args.max_age_min:
        print(f"STALE: newest observation {newest.isoformat()} is {age_min:.0f} min old "
              f"(limit {args.max_age_min:.0f}). Collection may have stopped — check the "
              f"collect-occupancy workflow and the feed/API key.", file=sys.stderr)
        sys.exit(1)

    print(f"OK: newest observation {newest.isoformat()} is {age_min:.0f} min old.")


if __name__ == "__main__":
    main()

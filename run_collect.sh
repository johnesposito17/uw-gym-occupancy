#!/bin/bash
# Local launchd bridge for the occupancy collector.
#
# WHY this exists: GitHub Actions' scheduled cron proved too unreliable (dropping most
# 10-min runs). This runs collect.py on a dependable local StartInterval and pushes the
# rows to the same repo, so the git-history-as-proof and CSV stay intact. It runs only
# while this Mac is awake — sleep gaps are honest gaps, never backfilled.
#
# launchd gives us a bare environment, so everything below is explicit: absolute tool
# paths, PATH, TLS cert bundle, and the contact email for the polite User-Agent.

set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export SSL_CERT_FILE=/etc/ssl/cert.pem          # this Mac's Python has no bundled CA certs
export CONTACT_EMAIL="jsesposito@wisc.edu"

GIT=/usr/bin/git
PYTHON=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
REPO=/Users/abbyesposito/uw-gym-occupancy

cd "$REPO" || exit 1

STAMP="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

# Stay in sync with the repo first, in case GitHub's cron (or another machine) also pushed.
"$GIT" pull --rebase --autostash --quiet 2>&1

# Collect. If the fetch fails, collect.py exits non-zero and writes NOTHING — we bail here
# without committing, so a failed fetch is a visible gap, never a partial/imputed row.
if ! "$PYTHON" collect.py; then
    echo "[$STAMP] collect.py failed; nothing committed" >&2
    exit 1
fi

# Commit only if there are genuinely new rows — never an empty commit.
if [ -n "$("$GIT" status --porcelain data/occupancy.csv)" ]; then
    "$GIT" add data/occupancy.csv
    "$GIT" commit -q -m "data: occupancy @ $STAMP (local)"
    # Push; on a race with a concurrent push, rebase once and retry.
    "$GIT" push --quiet || { "$GIT" pull --rebase --autostash --quiet && "$GIT" push --quiet; }
    echo "[$STAMP] committed + pushed new rows"
else
    echo "[$STAMP] no new rows"
fi

#!/usr/bin/env bash
#
# Persist the accumulated price log to its own durable branch.
#
# The price log is the single artefact that must survive across every run of *both* the
# hunt and shortlist workflows and be readable when the public site is built. It therefore
# lives in git, on an orphan branch named `price-history`, rather than in an evictable
# per-run cache. Both workflows check that branch out into the same directory and append to
# the same monthly shards, so there is one log, not two split-brain caches.
#
# "Nothing changed" is normal. A missing checkout is not: recreating it would
# reset the paid-discovery budget, so restore failures must stop the writer.
#
# Environment:
#   PRICE_HISTORY_DIR  directory the price-history branch is checked out into (default:
#                      price-history). The engine writes shards under $PRICE_HISTORY_DIR/prices.
set -euo pipefail

DIR="${PRICE_HISTORY_DIR:-price-history}"
BRANCH="price-history"

git config --global user.name "dealScout bot"
git config --global user.email "dealscout-bot@users.noreply.github.com"

if [ ! -d "$DIR/.git" ]; then
  echo "price-history checkout missing; refusing to reset discovery budget" >&2
  exit 1
fi

cd "$DIR"

# Cache entries contain public product fields and hashed queries, never credentials.
mkdir -p prices discovery
git add prices
if [ -f discovery/state.json ]; then
  git add discovery/state.json
fi

if git diff --cached --quiet; then
  echo "price history unchanged — nothing to commit"
  exit 0
fi

git commit -m "price log: ${GITHUB_WORKFLOW:-run} @ $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Workflows serialize writers. A rejected push still rebases, but never discards
# a budget reservation to resolve a conflict.
for attempt in 1 2 3 4 5; do
  if git push origin "HEAD:$BRANCH"; then
    echo "price history pushed"
    exit 0
  fi
  echo "push rejected (attempt $attempt) — rebasing onto latest $BRANCH"
  git fetch origin "$BRANCH"
  if ! git rebase "origin/$BRANCH"; then
    git rebase --abort
    echo "data-branch conflict; reservations were not published" >&2
    exit 1
  fi
  sleep $((attempt * 3))
done

echo "could not push price history after retries" >&2
exit 1

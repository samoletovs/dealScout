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
# This script is deliberately forgiving: "nothing changed" is the normal outcome on a quiet
# run and must exit 0, and the branch not existing yet (the very first run ever) must not
# fail the job — it is created here.
#
# Environment:
#   PRICE_HISTORY_DIR  directory the price-history branch is checked out into (default:
#                      price-history). The engine writes shards under $PRICE_HISTORY_DIR/prices.
set -euo pipefail

DIR="${PRICE_HISTORY_DIR:-price-history}"
BRANCH="price-history"

git config --global user.name "dealScout bot"
git config --global user.email "dealscout-bot@users.noreply.github.com"

# First run ever: the checkout of a non-existent branch left us with no working tree there.
# Create an orphan branch so the log has a home with no connection to main's history.
if [ ! -d "$DIR/.git" ]; then
  echo "price-history branch not present — creating it"
  rm -rf "$DIR"
  git clone --no-checkout --depth 1 "${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}" "$DIR"
  (
    cd "$DIR"
    git checkout --orphan "$BRANCH"
    git rm -rfq --cached . 2>/dev/null || true
    # Keep the working tree — the engine has already written prices/ into it.
  )
fi

cd "$DIR"

# Only the price log belongs on this branch; nothing else the run may have written.
mkdir -p prices
git add prices

if git diff --cached --quiet; then
  echo "price history unchanged — nothing to commit"
  exit 0
fi

git commit -m "price log: ${GITHUB_WORKFLOW:-run} @ $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Push with a short retry: hunt.yml and shortlist.yml can finish close together, and the
# loser of the race must rebase onto the winner's commit rather than clobber it — the whole
# point of one shared branch is that neither run's observations are lost.
for attempt in 1 2 3 4 5; do
  if git push origin "HEAD:$BRANCH"; then
    echo "price history pushed"
    exit 0
  fi
  echo "push rejected (attempt $attempt) — rebasing onto latest $BRANCH"
  git fetch origin "$BRANCH" || true
  git rebase "origin/$BRANCH" || git rebase --abort || true
  sleep $((attempt * 3))
done

echo "could not push price history after retries" >&2
exit 1

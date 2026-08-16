#!/usr/bin/env bash
# Publish the Health Dashboard to GitHub Pages -- with a HARD verification gate.
#
# Fixes the silent-failure bug: the local repo tracked a non-existent branch
# and pushes were never reaching origin, yet the agent reported success.
#
# Remote truth (verified 2026-07-26): origin = orchid1970/health-dashboard,
# the ONLY remote branch is `main`. There is no `gh-pages` branch.
#
# Exits non-zero on ANY failure. The agent MUST treat a non-zero exit as a
# failed run and say so explicitly -- never claim success without EXIT 0.

set -euo pipefail

DIR="/home/timothy/shared/Health Dashboard"
SRC="Timothy_Health_Progress_Infographic.html"
BRANCH="main"

cd "$DIR"

if [ ! -s "$SRC" ]; then
  echo "FAIL: $SRC missing or empty"
  exit 1
fi

# Guard: refuse to publish a dashboard that wasn't rebuilt today.
TODAY_HUMAN=$(TZ='America/Los_Angeles' date '+%B %-d, %Y')
if ! grep -q "$TODAY_HUMAN" "$SRC"; then
  echo "FAIL: $SRC does not contain today's date ($TODAY_HUMAN) -- rebuild did not run"
  exit 1
fi

BYTES=$(stat -c%s "$SRC")
if [ "$BYTES" -lt 15000 ]; then
  echo "FAIL: $SRC only $BYTES bytes (expected >=15000) -- truncated rebuild"
  exit 1
fi

# index.html is the GitHub Pages entry point; keep it identical to the source.
cp "$SRC" index.html

# Make sure we are on the branch that actually exists on the remote.
git checkout -B "$BRANCH" >/dev/null 2>&1

git add "$SRC" index.html
if git diff --cached --quiet; then
  echo "NOTE: no content change to commit"
  exit 0
else
  git -c user.email="escam02g@gmail.com" -c user.name="Timothy Escamilla" \
      commit -q -m "Weekly health dashboard refresh $(TZ='America/Los_Angeles' date +%Y-%m-%d)"
fi

# Push and FAIL LOUDLY if it does not land.
if ! git push origin "$BRANCH" 2>&1 | tail -3; then
  echo "FAIL: git push to origin/$BRANCH failed"
  exit 1
fi

LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
if [ "$LOCAL_SHA" != "$REMOTE_SHA" ]; then
  echo "FAIL: remote SHA ($REMOTE_SHA) != local SHA ($LOCAL_SHA) -- push did not land"
  exit 1
fi
echo "PUSH OK: origin/$BRANCH @ ${LOCAL_SHA:0:7}"

# Poll the live site until GitHub Pages serves today's date (max ~3 min).
URL="https://orchid1970.github.io/health-dashboard/"
for i in $(seq 1 18); do
  sleep 10
  if curl -s -H 'Cache-Control: no-cache' "${URL}?cb=$(date +%s)" | grep -q "$TODAY_HUMAN"; then
    echo "LIVE OK: $URL shows $TODAY_HUMAN (after $((i*10))s)"
    exit 0
  fi
done

echo "FAIL: pushed successfully but $URL still not showing $TODAY_HUMAN after 180s"
exit 1

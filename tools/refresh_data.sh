#!/usr/bin/env bash
# refresh_data.sh — Process all data files and patch the Health Dashboard HTML.
#
# Chains the four processing steps in order, checks each for success,
# and writes a timestamped refresh log to data/refresh_log.txt.
#
# Does NOT call publish.sh — the agent calls that separately after
# reading the log and verifying the summary.
#
# Exit codes:
#   0  — all steps succeeded
#   1  — at least one step failed (last failure printed to stderr)

set -euo pipefail

DIR="/home/timothy/shared/Health Dashboard"
LOG="$DIR/data/refresh_log.txt"

# ── header ──────────────────────────────────────────────────────────────────
{
  echo "=== Health Dashboard Refresh ==="
  echo "Started: $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"
  echo "Host:    $(hostname)"
  echo "Dir:     $DIR"
  echo "================================="
} > "$LOG"

cd "$DIR"

# ── step runner ──────────────────────────────────────────────────────────────
run_step() {
  local label="$1"
  shift
  echo "" >> "$LOG"
  echo "--- $label ---" >> "$LOG"
  echo "CMD: $*" >> "$LOG"

  if "$@" >> "$LOG" 2>&1; then
    echo "OK: $label" | tee -a "$LOG"
  else
    local code=$?
    echo "FAIL: $label (exit $code)" | tee -a "$LOG"
    echo "" >> "$LOG"
    echo "=== FAILED at step: $label ===" >> "$LOG"
    echo "Completed: $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC" >> "$LOG"
    exit 1
  fi
}

# ── steps ────────────────────────────────────────────────────────────────────
# ── preflight ─────────────────────────────────────────────────────────────────
run_step "validate_refresh" python3 tools/validate_refresh.py

# ── steps ────────────────────────────────────────────────────────────────────
run_step "bp_monthly"       python3 tools/bp_monthly.py      data/bp_raw.json     data/bp_monthly.json
run_step "nocturnal_floor"  python3 tools/nocturnal_floor.py data/glucose_raw.json data/nocturnal_floor.json
run_step "training_load"    python3 tools/training_load.py   data/workouts_raw.json data/training_load.json
run_step "update_dashboard" python3 tools/update_dashboard.py
# ── footer ───────────────────────────────────────────────────────────────────
{
  echo ""
  echo "================================="
  echo "All steps OK"
  echo "Completed: $(date -u '+%Y-%m-%dT%H:%M:%SZ') UTC"
  echo "================================="
} | tee -a "$LOG"

echo ""
echo "=== update_summary.json ==="
cat data/update_summary.json

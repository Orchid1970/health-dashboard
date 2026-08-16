#!/usr/bin/env python3
"""Normalize exact Stelo and Withings source archives into pipeline inputs.

The source files remain untouched. Normalized files are derived deterministically,
with device identity retained for body composition and no fabricated values.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
SOURCES = DATA / "sources"


def load(name: str) -> Any:
    with (SOURCES / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def write(name: str, payload: Any) -> None:
    path = DATA / name
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def iso_epoch(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value)


def normalize_stelo() -> int:
    payload = load("stelo_source.json")
    rows = []
    for row in payload.get("readings", []):
        if not isinstance(row, dict):
            continue
        timestamp = row.get("timestamp")
        value = row.get("glucose_value")
        if timestamp is not None and value is not None:
            rows.append([str(timestamp).replace("T", " ")[:16], value])
    write("glucose_raw.json", {"readings": rows, "source": "data/sources/stelo_source.json", "normalized_at": datetime.now(timezone.utc).isoformat()})
    return len(rows)


def normalize_withings() -> dict[str, int]:
    bp = load("withings_blood_pressure_source.json")
    bp_rows = bp.get("blood_pressure", [])
    write("bp_raw.json", {"blood_pressure": bp_rows, "source": "data/sources/withings_blood_pressure_source.json"})

    workouts = load("withings_workouts_source.json")
    workout_rows = workouts.get("workouts", [])
    write("workouts_raw.json", {"workouts": workout_rows, "source": "data/sources/withings_workouts_source.json"})

    body = load("withings_body_composition_source.json")
    sessions = []
    for device, container in body.get("by_device", {}).items():
        for row in container.get("measurements", []):
            if not isinstance(row, dict):
                continue
            item = dict(row)
            metrics = item.pop("metrics", {})
            if isinstance(metrics, dict):
                item.update({key: value for key, value in metrics.items() if value is not None})
            item["device"] = item.get("device") or device
            sessions.append(item)
    sessions.sort(key=lambda row: float(row.get("timestamp", 0) or 0), reverse=True)
    write("bodycomp_raw.json", {"sessions": sessions, "source": "data/sources/withings_body_composition_source.json", "devices": sorted({row.get("device") for row in sessions})})

    hr = load("withings_heart_rate_source.json")
    hr_rows = hr.get("heart_rate", [])
    recent_hr = sorted(hr_rows, key=lambda row: str(row.get("date", row.get("timestamp", ""))))[-7:]
    write("hr_raw.json", {"readings": [{"date_label": str(row.get("date", row.get("timestamp", ""))), "heart_rate": row.get("bpm"), "source_timestamp": row.get("timestamp")} for row in recent_hr]})

    activity = load("withings_activity_source.json")
    activity_rows = activity.get("activities", [])
    recent_activity = sorted(activity_rows, key=lambda row: str(row.get("date", "")))[-7:]
    write("activity_raw.json", {"days": [{"date_label": row.get("date"), "steps": row.get("steps"), "source": "withings"} for row in recent_activity]})
    return {"bp": len(bp_rows), "workouts": len(workout_rows), "bodycomp": len(sessions), "hr": len(hr_rows), "activity": len(activity_rows)}


def main() -> int:
    try:
        glucose_count = normalize_stelo()
        counts = normalize_withings()
        print(json.dumps({"status": "OK", "glucose": glucose_count, **counts}, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Capture immutable health payloads and write a compact audit manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
SOURCE_DIR = DATA / "sources"
MANIFEST = DATA / "refresh_manifest.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def rows_for(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("readings", "blood_pressure", "measurements", "sessions", "workouts", "activities", "sleep", "heart_rate", "heart_recordings", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows_for(value)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, dict):
            nested = rows_for(value)
            if nested:
                return nested
    return []


def epoch_to_iso(value: Any) -> str | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 100000000:
        return None
    return datetime.fromtimestamp(number, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def timestamp_of(row: Any) -> str | None:
    if isinstance(row, (list, tuple)) and row:
        return epoch_to_iso(row[0]) or str(row[0])
    if not isinstance(row, dict):
        return None
    for key in ("timestamp", "time", "date", "datetime", "start", "startdate", "created_at"):
        value = row.get(key)
        if value not in (None, ""):
            return epoch_to_iso(value) or str(value)
    return None


def coverage(payload: Any) -> dict[str, Any]:
    rows = rows_for(payload)
    timestamps = [ts for row in rows if (ts := timestamp_of(row))]
    return {"record_count": len(rows), "timestamp_count": len(timestamps), "first_timestamp": min(timestamps) if timestamps else None, "last_timestamp": max(timestamps) if timestamps else None}


def schema_summary(payload: Any) -> dict[str, Any]:
    rows = rows_for(payload)
    if not rows:
        return {"format": type(payload).__name__, "record_key": None}
    first = rows[0]
    if isinstance(first, dict):
        keys = sorted(first.keys())
        timestamp_field = next((key for key in ("timestamp", "time", "date", "datetime", "start", "startdate", "created_at") if key in first), None)
        value_field = next((key for key in ("glucose_value", "mg_dl", "systolic", "weight", "workout_type") if key in first), None)
        return {"format": "list-of-dicts", "record_keys": keys, "timestamp_field": timestamp_field, "value_field": value_field}
    return {"format": "list-of-arrays", "columns": payload.get("columns") if isinstance(payload, dict) else None}


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=destination.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with source.open("rb") as src:
            shutil.copyfileobj(src, tmp)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp_path, destination)


def capture_file(name: str, source: Path, provider: str, endpoint: str, requested: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source)
    payload = load_json(source)
    destination = SOURCE_DIR / f"{name}_source.json"
    atomic_copy(source, destination)
    return {"provider": provider, "endpoint": endpoint, "source_file": str(destination.relative_to(BASE)), "collected_at_utc": utc_now(), "requested": requested, "sha256": sha256(destination), "coverage": coverage(payload), "schema": schema_summary(payload), "warnings": warnings, "status": "OK" if not warnings else "PARTIAL_INCOMPLETE_SOURCE"}


def legacy_artifacts() -> list[dict[str, Any]]:
    source_map = {
        "bp_raw": "withings_blood_pressure_source.json",
        "bodycomp_raw": "withings_body_composition_source.json",
        "workouts_raw": "withings_workouts_source.json",
        "hr_raw": "withings_heart_rate_source.json",
        "activity_raw": "withings_activity_source.json",
        "glucose_raw": "stelo_source.json",
    }
    artifacts = []
    for name, source_name in source_map.items():
        path = DATA / f"{name}.json"
        source_path = SOURCE_DIR / source_name
        if not path.is_file() or source_path.is_file():
            continue
        try:
            count = len(rows_for(load_json(path)))
        except Exception:
            count = None
        artifacts.append({"dataset": name, "source_file": str(path.relative_to(BASE)), "status": "UNVERIFIED_LEGACY", "record_count": count, "warnings": ["Existing artifact predates immutable source capture and may be sampled or normalized; not authoritative."]})
    return artifacts


def write_manifest(items: list[dict[str, Any]], notes: list[str]) -> None:
    manifest = {"schema_version": 1, "generated_at_utc": utc_now(), "host": socket.gethostname(), "source_policy": "immutable exact provider payloads; normalized inputs are separate", "stelo_history_requirement": {"available_from": "2025", "verified_earliest_reading": "2025-12-03T21:33:34", "verified_365_day_readings": 58863, "retrieval_must_not_truncate_at": 3000}, "datasets": items, "legacy_unverified_artifacts": legacy_artifacts(), "notes": notes}
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=MANIFEST.parent, delete=False) as tmp:
        json.dump(manifest, tmp, indent=2, sort_keys=True)
        tmp.write("\n")
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, MANIFEST)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--provider", default="unknown")
    parser.add_argument("--endpoint", default="not-specified")
    parser.add_argument("--requested", default="{}")
    parser.add_argument("--warning", action="append", default=[])
    args = parser.parse_args()
    try:
        requested = json.loads(args.requested)
        if not isinstance(requested, dict):
            raise ValueError("--requested must be a JSON object")
        items = []
        for spec in args.source:
            if "=" not in spec:
                raise ValueError(f"invalid --source {spec!r}; expected NAME=PATH")
            name, raw_path = spec.split("=", 1)
            items.append(capture_file(name, Path(raw_path).expanduser().resolve(), args.provider, args.endpoint, requested, list(args.warning)))
        write_manifest(items, ["Existing data/*.json files remain untrusted until captured and classified."])
        status = "OK" if all(item["status"] == "OK" for item in items) else "PARTIAL_INCOMPLETE_SOURCE"
        print(json.dumps({"status": status, "datasets": len(items), "manifest": str(MANIFEST.relative_to(BASE))}, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

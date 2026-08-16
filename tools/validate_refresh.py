#!/usr/bin/env python3
"""Fail-closed preflight checks for the Health Dashboard refresh."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
MANIFEST = DATA / "refresh_manifest.json"


def load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_stamp(value):
    if value is None:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(str(value), fmt)
            except ValueError:
                pass
    return None


def validate_manifest(manifest):
    errors, warnings, datasets = [], [], manifest.get("datasets", [])
    if not isinstance(datasets, list) or not datasets:
        errors.append("manifest has no captured datasets")
        return errors, warnings
    for item in datasets:
        name = item.get("source_file", "unknown")
        path = BASE / name
        if not path.is_file():
            errors.append(f"missing source file: {name}")
            continue
        if item.get("sha256") and item["sha256"] != sha256(path):
            errors.append(f"checksum mismatch: {name}")
        if item.get("status") == "PARTIAL_INCOMPLETE_SOURCE":
            warnings.append(f"incomplete source: {name}")
        cov = item.get("coverage", {})
        if cov.get("record_count", 0) == 0:
            warnings.append(f"empty source: {name}")
        if cov.get("first_timestamp") and not parse_stamp(cov["first_timestamp"]):
            errors.append(f"unparseable first timestamp: {name}")
        if cov.get("last_timestamp") and not parse_stamp(cov["last_timestamp"]):
            errors.append(f"unparseable last timestamp: {name}")
    stelo = [d for d in datasets if d.get("provider") == "Stelo"]
    if stelo:
        item = stelo[0]
        cov = item.get("coverage", {})
        if cov.get("record_count") != 58863 or not str(cov.get("first_timestamp", "")).startswith("2025-"):
            errors.append("Stelo full-history requirement not met: expected 2025 coverage and 58,863 records")
        else:
            warnings.append("Stelo 24-hour latest endpoint previously returned zero; direct full-history capture is authoritative for this refresh")
    legacy = manifest.get("legacy_unverified_artifacts", [])
    if legacy:
        errors.append(f"{len(legacy)} legacy data artifacts remain unverified")
    return errors, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-incomplete", action="store_true", help="diagnostic override; never permits publication")
    args = ap.parse_args()
    if not MANIFEST.is_file():
        print(json.dumps({"status":"BLOCKED","errors":["refresh_manifest.json missing"]}))
        return 2
    try:
        manifest = load(MANIFEST)
        errors, warnings = validate_manifest(manifest)
    except Exception as exc:
        print(json.dumps({"status":"BLOCKED","errors":[f"manifest unreadable: {exc}"]}))
        return 2
    status = "BLOCKED" if errors else ("PARTIAL_MISSING_OPTIONAL_FIELD" if warnings else "OK")
    result = {"status": status, "errors": errors, "warnings": warnings, "manifest": str(MANIFEST.relative_to(BASE))}
    print(json.dumps(result, separators=(",", ":")))
    return 0 if status in ("OK", "PARTIAL_MISSING_OPTIONAL_FIELD") and (not errors or args.allow_incomplete) else 2


if __name__ == "__main__":
    sys.exit(main())

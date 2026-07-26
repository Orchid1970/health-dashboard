#!/usr/bin/env python3
"""
Reduce raw Withings BP readings to monthly medians WITHOUT loading them
into the agent context.

Usage:
    python3 bp_monthly.py <raw_json_path>

Input: a JSON file containing the raw blood-pressure payload as returned by
the Withings MCP tool. The script is tolerant of several shapes:
  - {"readings": [...]}, {"measurements": [...]}, {"data": [...]}
  - a bare list [...]
Each reading must expose a timestamp-ish field (date/datetime/time/timestamp)
and systolic/diastolic values (systolic/sys, diastolic/dia).

Output: writes data/bp_monthly.json and prints a COMPACT summary only.
The agent should read bp_monthly.json (small) -- never the raw file.
"""
import json
import os
import re
import sys
from statistics import median

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data", "bp_monthly.json")


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("readings", "measurements", "data", "blood_pressure", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # single nested dict containing a list
        for val in payload.values():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                return val
    return []


def _get(row, names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _month(ts):
    s = str(ts)
    m = re.search(r"(\d{4})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    if s.isdigit():  # epoch seconds
        import datetime
        return datetime.datetime.utcfromtimestamp(int(s)).strftime("%Y-%m")
    return None


def _all_months(first, last):
    fy, fm = (int(x) for x in first.split("-"))
    ly, lm = (int(x) for x in last.split("-"))
    out = []
    y, m = fy, fm
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return out


def main():
    if len(sys.argv) < 2:
        print("ERROR: raw json path required")
        return 1
    with open(sys.argv[1]) as f:
        payload = json.load(f)

    rows = _rows(payload)
    if not rows:
        print("ERROR: no readings found in raw payload")
        return 1

    # Deduplicate on (timestamp, systolic, diastolic)
    seen = set()
    buckets = {}
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = _get(row, ("datetime", "date", "timestamp", "time", "measured_at"))
        sys_v = _get(row, ("systolic", "sys", "systolic_mmhg", "sbp"))
        dia_v = _get(row, ("diastolic", "dia", "diastolic_mmhg", "dbp"))
        if ts is None or sys_v is None or dia_v is None:
            skipped += 1
            continue
        key = (str(ts), sys_v, dia_v)
        if key in seen:
            continue
        seen.add(key)
        mo = _month(ts)
        if not mo:
            skipped += 1
            continue
        buckets.setdefault(mo, []).append((float(sys_v), float(dia_v)))

    if not buckets:
        print("ERROR: no parseable readings")
        return 1

    months = _all_months(min(buckets), max(buckets))
    labels, sys_series, dia_series, counts = [], [], [], []
    for mo in months:
        labels.append(mo)
        vals = buckets.get(mo)
        if vals:
            sys_series.append(round(median(v[0] for v in vals), 1))
            dia_series.append(round(median(v[1] for v in vals), 1))
            counts.append(len(vals))
        else:
            # Missing month -> real gap. NEVER interpolate.
            sys_series.append(None)
            dia_series.append(None)
            counts.append(0)

    result = {
        "labels": labels,
        "systolic": sys_series,
        "diastolic": dia_series,
        "counts": counts,
        "months_total": len(months),
        "months_with_data": sum(1 for c in counts if c),
        "readings_used": len(seen),
        "readings_skipped": skipped,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(result, f)

    print(f"OK wrote {OUT}")
    print(f"months={len(months)} with_data={result['months_with_data']} "
          f"readings={len(seen)} skipped={skipped}")
    print(f"range={labels[0]}..{labels[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

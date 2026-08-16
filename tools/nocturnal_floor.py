#!/usr/bin/env python3
"""Reduce raw Stelo CGM readings to per-night glucose floors.

Contract mirrors tools/bp_monthly.py: large payload in, tiny JSON out,
exactly one summary line printed. NEVER print raw readings -- keeping the
~1,200-reading payload out of agent context is the entire point of this script.

Usage:
    nocturnal_floor.py <raw_glucose.json> [output.json]

Night N window = N 22:00 -> (N+1) 05:00.
Nights with no readings emit null. Gaps are NEVER interpolated or carried
forward -- a missing night is real information (sensor expiry / transmitter swap).
"""
import sys, json, os
from datetime import datetime, timedelta, date

DEFAULT_OUT = "/home/timothy/shared/Health Dashboard/data/nocturnal_floor.json"
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]


def parse_ts(raw):
    s = str(raw).strip().replace("Z", "").replace("T", " ")
    if "." in s:
        s = s.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def extract(payload):
    """Return list of (datetime, mg_dl). Handles columnar and list-of-dicts."""
    rows = []
    container = payload
    if isinstance(payload, dict):
        for key in ("readings", "data", "values", "records"):
            if isinstance(payload.get(key), list):
                container = payload[key]
                break
        else:
            container = None
    if not isinstance(container, list):
        return []

    for item in container:
        ts = val = None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            ts, val = item[0], item[1]
        elif isinstance(item, dict):
            for tk in ("time", "timestamp", "systemTime", "displayTime", "date"):
                if tk in item:
                    ts = item[tk]
                    break
            for vk in ("mg_dl", "mgDl", "value", "glucose", "sgv"):
                if vk in item:
                    val = item[vk]
                    break
        if ts is None or val is None:
            continue
        dt = parse_ts(ts)
        if dt is None:
            continue
        try:
            mg = float(val)
        except (TypeError, ValueError):
            continue
        rows.append((dt, mg))
    return rows


def night_of(dt):
    """Night key: hour>=22 belongs to that date; hour<5 belongs to previous date."""
    if dt.hour >= 22:
        return dt.date()
    if dt.hour < 5:
        return dt.date() - timedelta(days=1)
    return None


def label(d):
    end = d + timedelta(days=1)
    return "%s %d\u2192%d" % (MONTHS[d.month - 1], d.day, end.day)


def main():
    if len(sys.argv) < 2:
        print("usage: nocturnal_floor.py <raw_glucose.json> [output.json]", file=sys.stderr)
        return 2
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT

    try:
        with open(src) as fh:
            payload = json.load(fh)
    except Exception as exc:
        print("ERROR: cannot read/parse %s: %s" % (src, exc), file=sys.stderr)
        return 1

    rows = extract(payload)
    if not rows:
        print("ERROR: no readings parsed from %s (unrecognized shape)" % src, file=sys.stderr)
        return 1

    buckets = {}
    for dt, mg in rows:
        key = night_of(dt)
        if key is None:
            continue
        buckets.setdefault(key, []).append(mg)

    if not buckets:
        print("ERROR: readings parsed but none fell in the 22:00-05:00 window", file=sys.stderr)
        return 1

    last = max(buckets)
    nights = [last - timedelta(days=i) for i in range(6, -1, -1)]

    labels, floors, counts = [], [], []
    for n in nights:
        vals = buckets.get(n, [])
        labels.append(label(n))
        if vals:
            floors.append(round(min(vals), 1))
            counts.append(len(vals))
        else:
            floors.append(None)   # genuine gap -- never interpolate
            counts.append(0)

    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump({
                "labels": labels,
                "floors": floors,
                "counts": counts,
                "window": "22:00-05:00",
                "generated": datetime.now().isoformat(timespec="seconds"),
            }, fh, indent=2)
    except Exception as exc:
        print("ERROR: cannot write %s: %s" % (out, exc), file=sys.stderr)
        return 1

    present = [f for f in floors if f is not None]
    gaps = len(floors) - len(present)
    span = ("%g..%g" % (min(present), max(present))) if present else "none"
    print("nocturnal_floor: %d nights, %d with data, %d gaps, floors %s"
          % (len(floors), len(present), gaps, span))
    return 0


if __name__ == "__main__":
    sys.exit(main())

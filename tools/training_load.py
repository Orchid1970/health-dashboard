#!/usr/bin/env python3
"""Reduce raw Withings workout records to a small training-load summary.

Contract mirrors tools/nocturnal_floor.py and tools/bp_monthly.py: large
payload in, tiny JSON out, exactly one summary line printed. NEVER print raw
workout records -- keeping the payload out of agent context is the point.

Usage:
    training_load.py <workouts_raw.json> [output.json]

Weeks are ISO weeks (%G-W%V) in America/Los_Angeles. Weeks with no data are
OMITTED, never zero-filled. Missing intensity/hr_average emit null, never 0.
Golf is tracked separately and excluded from the aerobic:resistance ratio --
it is long-duration low-intensity walking with no HR data and distorts it.
"""
import sys, json, os
from datetime import datetime

DEFAULT_OUT = "/home/timothy/shared/Health Dashboard/data/training_load.json"

# FALLBACK ONLY -- type_of() prefers the MCP's server-side `workout_type` string.
# This map is used only if that field is ever missing. Verified against live
# Withings data 2026-08-02: all 7 categories below are present in Timothy's
# 173-session history. Rowing and Yoga were added 2026-08-02 after they appeared
# in real data and were absent from the original 5-entry map.
CATEGORY_MAP = {
    1: "Walk",
    16: "Lift Weights",
    27: "Golf",
    28: "Yoga",
    187: "Rowing",
    307: "Indoor Running",
    308: "Indoor Cycling",
}

LIFT = "Lift Weights"
WALK = "Walk"
GOLF = "Golf"

TARGET_LIFT_MIN = 135
TARGET_SESSIONS = 3
TARGET_SESSION_MIN = 45

try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:
    PACIFIC = None


def container_of(payload):
    """Find the workouts list in whatever shape the payload arrives."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in ("workouts", "series", "data", "records", "activities"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = container_of(v)
            if inner is not None:
                return inner
    return None


def week_key(item):
    """ISO week in Pacific. Prefer epoch; fall back to the date string."""
    sd = item.get("startdate")
    if sd is not None and PACIFIC is not None:
        try:
            return datetime.fromtimestamp(int(sd), PACIFIC).strftime("%G-W%V")
        except Exception:
            pass
    d = item.get("date")
    if d:
        try:
            return datetime.strptime(str(d)[:10], "%Y-%m-%d").strftime("%G-W%V")
        except ValueError:
            pass
    if sd is not None:
        try:
            return datetime.fromtimestamp(int(sd)).strftime("%G-W%V")
        except Exception:
            pass
    return None


def type_of(item):
    wt = item.get("workout_type")
    if isinstance(wt, str) and wt.strip():
        return wt.strip()
    cat = item.get("category")
    try:
        cat = int(cat)
    except (TypeError, ValueError):
        return "Unknown"
    return CATEGORY_MAP.get(cat, "Category %d" % cat)


def minutes_of(item):
    try:
        secs = int(item["enddate"]) - int(item["startdate"])
    except (KeyError, TypeError, ValueError):
        return None
    if secs <= 0:
        return None
    return int(round(secs / 60.0))


def num_or_none(item, *keys):
    for k in keys:
        if k in item and item[k] is not None:
            try:
                v = float(item[k])
            except (TypeError, ValueError):
                continue
            return int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 1)
    return None


def ratio_of(walk_min, cardio_min, lift_min):
    if not lift_min:
        return None          # never divide by zero
    return round((walk_min + cardio_min) / float(lift_min), 2)


def main():
    if len(sys.argv) < 2:
        print("usage: training_load.py <workouts_raw.json> [output.json]", file=sys.stderr)
        return 2
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT

    try:
        with open(src) as fh:
            payload = json.load(fh)
    except Exception as exc:
        print("ERROR: cannot read/parse %s: %s" % (src, exc), file=sys.stderr)
        return 1

    items = container_of(payload)
    if items is None:
        print("ERROR: no workouts list found in %s (unrecognized shape)" % src, file=sys.stderr)
        return 1

    sessions, weeks = [], {}
    for it in items:
        if not isinstance(it, dict):
            continue
        mins = minutes_of(it)
        wk = week_key(it)
        if mins is None or wk is None:
            continue
        wtype = type_of(it)
        rec = {
            "date": str(it.get("date", ""))[:10],
            "type": wtype,
            "minutes": mins,
            "intensity": num_or_none(it, "intensity"),
            "hr_avg": num_or_none(it, "hr_average", "hr_avg"),
            "start": it.get("start"),
            "week": wk,
        }
        sessions.append(rec)

        b = weeks.setdefault(wk, {"walk_min": 0, "lift_min": 0, "cardio_min": 0,
                                  "golf_min": 0, "lift_sessions": 0})
        if wtype == LIFT:
            b["lift_min"] += mins
            b["lift_sessions"] += 1
        elif wtype == WALK:
            b["walk_min"] += mins
        elif wtype == GOLF:
            b["golf_min"] += mins
        else:
            b["cardio_min"] += mins

    if not sessions:
        print("ERROR: parsed 0 usable workouts from %s" % src, file=sys.stderr)
        return 1

    sessions.sort(key=lambda r: (r["date"], r.get("start") or ""))
    lift_sessions = [s for s in sessions if s["type"] == LIFT]

    weekly = []
    for wk in sorted(weeks):                      # omit empty weeks, never zero-fill
        b = weeks[wk]
        weekly.append({
            "week": wk,
            "walk_min": b["walk_min"],
            "lift_min": b["lift_min"],
            "cardio_min": b["cardio_min"],
            "golf_min": b["golf_min"],
            "lift_sessions": b["lift_sessions"],
            "ratio": ratio_of(b["walk_min"], b["cardio_min"], b["lift_min"]),
        })

    cur = weekly[-1]
    avg_session = (int(round(cur["lift_min"] / float(cur["lift_sessions"])))
                   if cur["lift_sessions"] else None)
    current = {
        "week": cur["week"],
        "lift_min": cur["lift_min"],
        "lift_sessions": cur["lift_sessions"],
        "avg_session_min": avg_session,
        "walk_min": cur["walk_min"],
        "cardio_min": cur["cardio_min"],
        "golf_min": cur["golf_min"],
        "ratio": cur["ratio"],
        "target_lift_min": TARGET_LIFT_MIN,
        "target_sessions": TARGET_SESSIONS,
        "target_session_min": TARGET_SESSION_MIN,
        "target_ratio": ratio_of(cur["walk_min"], cur["cardio_min"], TARGET_LIFT_MIN),
    }

    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            json.dump({
                "sessions": sessions,
                "lift_sessions": lift_sessions,
                "weekly": weekly,
                "current": current,
                "generated": datetime.now().isoformat(timespec="seconds"),
            }, fh, indent=2)
    except Exception as exc:
        print("ERROR: cannot write %s: %s" % (out, exc), file=sys.stderr)
        return 1

    print("training_load: %d sessions, %d weeks, current %s lift %dmin/%dsess ratio %s"
          % (len(sessions), len(weekly), current["week"], current["lift_min"],
             current["lift_sessions"],
             current["ratio"] if current["ratio"] is not None else "n/a"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

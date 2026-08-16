#!/usr/bin/env python3
"""
update_dashboard.py — Surgical HTML patcher for Timothy's Health Dashboard.

Reads processed data/*.json files and applies regex patches to the dashboard
HTML in a single subprocess call.  NEVER prints the HTML or raw reading arrays
to stdout — only a compact summary JSON is emitted.

Required data files (script exits 1 if any are missing):
  data/bp_monthly.json        — from tools/bp_monthly.py
  data/nocturnal_floor.json   — from tools/nocturnal_floor.py
  data/glucose_raw.json       — from Stelo debug endpoint
  data/training_load.json     — from tools/training_load.py
  data/bodycomp_raw.json      — from Withings get_body_composition

Optional data files (patches skipped with gap note if absent):
  data/hr_raw.json            — {"readings": [{"date_label": "Aug 5 AM", "heart_rate": 61}]}
  data/activity_raw.json      — {"days": [{"date_label": "Aug 3", "steps": 12500}]}

Usage:
    python3 tools/update_dashboard.py
"""
import json
import re
import sys
import shutil
import datetime
import os

DASHBOARD = "/home/timothy/shared/Health Dashboard/Timothy_Health_Progress_Infographic.html"
INDEX    = "/home/timothy/shared/Health Dashboard/index.html"
DATA     = "/home/timothy/shared/Health Dashboard/data"
SUMMARY  = f"{DATA}/update_summary.json"


# ── helpers ─────────────────────────────────────────────────────────────────

def load_required(name):
    path = f"{DATA}/{name}"
    if not os.path.exists(path):
        print(f"ERROR: required data file missing: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as fh:
        return json.load(fh)


def load_optional(name):
    path = f"{DATA}/{name}"
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


def floor_note(v):
    if v is None:
        return "No CGM data this night"
    if v >= 90:
        return "Good floor \u2014 within target range"
    if v >= 80:
        return "Acceptable floor \u2014 near or at target"
    if v >= 70:
        return "Low-yellow zone \u2014 monitor evening carbs"
    return "Hypo-adjacent \u2014 tighten evening carb protocol"


def sub1(pattern, repl, html, flags=0):
    """Apply re.sub and return (new_html, matched_bool)."""
    new_html, n = re.subn(pattern, repl, html, flags=flags)
    return new_html, n > 0


# ── main ────────────────────────────────────────────────────────────────────

def main():
    patches_applied = []
    gaps = []

    # 1. Load all required data files
    bp        = load_required("bp_monthly.json")
    nocturnal = load_required("nocturnal_floor.json")
    glucose   = load_required("glucose_raw.json")
    training  = load_required("training_load.json")
    bodycomp  = load_required("bodycomp_raw.json")

    # Optional
    hr_data   = load_optional("hr_raw.json")
    act_data  = load_optional("activity_raw.json")

    if hr_data is None:
        gaps.append("hr_raw.json missing — HR chart not updated")
    if act_data is None:
        gaps.append("activity_raw.json missing — Activity chart not updated")

    # 2. Read HTML (silently — do NOT print)
    if not os.path.exists(DASHBOARD):
        print(f"ERROR: dashboard not found: {DASHBOARD}", file=sys.stderr)
        sys.exit(1)

    with open(DASHBOARD, encoding="utf-8") as fh:
        html = fh.read()

    today = datetime.date.today().strftime("%B %-d, %Y")

    # ── PATCH 1: Last Updated dates ──────────────────────────────────────────
    html, ok = sub1(
        r"Last Updated: \w+ \d+, \d{4}",
        f"Last Updated: {today}",
        html
    )
    if ok:
        patches_applied.append("last_updated_main")

    html, ok = sub1(
        r"Last updated: \w+ \d+, \d{4}",
        f"Last updated: {today}",
        html
    )
    if ok:
        patches_applied.append("last_updated_nocturnal")

    # ── PATCH 2: Blood Pressure chart ────────────────────────────────────────
    bp_labels = [l.replace("-", " ") for l in bp["labels"]]

    # BP chart labels (unique anchor: 'Monthly median systolic' appears once)
    html, ok = sub1(
        r"(new Chart\(bpCtx[^;]*?labels: )\[.*?\]",
        lambda m: m.group(1) + json.dumps(bp_labels),
        html,
        flags=re.DOTALL
    )
    if ok:
        patches_applied.append("bp_labels")

    html, ok = sub1(
        r"(label: 'Monthly median systolic',\s+data: )\[.*?\]",
        lambda m: m.group(1) + json.dumps(bp["systolic"]),
        html,
        flags=re.DOTALL
    )
    if ok:
        patches_applied.append("bp_systolic")

    html, ok = sub1(
        r"(label: 'Monthly median diastolic',\s+data: )\[.*?\]",
        lambda m: m.group(1) + json.dumps(bp["diastolic"]),
        html,
        flags=re.DOTALL
    )
    if ok:
        patches_applied.append("bp_diastolic")

    html, ok = sub1(
        r"const bpMonthlyCounts = \[.*?\];",
        f"const bpMonthlyCounts = {json.dumps(bp['counts'])};",
        html
    )
    if ok:
        patches_applied.append("bp_monthly_counts")

    # ── PATCH 3: Nocturnal Glucose Floor ─────────────────────────────────────
    floors      = nocturnal["floors"]
    labels_noct = nocturnal["labels"]
    notes       = [floor_note(f) for f in floors]

    html, ok = sub1(
        r"const nocturnalFloors = \[.*?\];",
        f"const nocturnalFloors = {json.dumps(floors)};",
        html
    )
    if ok:
        patches_applied.append("nocturnal_floors")

    _notes_json = json.dumps(notes)
    html, ok = sub1(
        r"const nocturnalNotes = \[.*?\];",
        lambda m, _nj=_notes_json: f"const nocturnalNotes = {_nj};",
        html
    )
    if ok:
        patches_applied.append("nocturnal_notes")

    # Nocturnal chart labels — anchor via nocturnalTargetLine plugin
    html, ok = sub1(
        r"(plugins: \[nocturnalTargetLine\][^;]*?labels: )\[.*?\]",
        lambda m: m.group(1) + json.dumps(labels_noct),
        html,
        flags=re.DOTALL
    )
    if ok:
        patches_applied.append("nocturnal_labels")

    # ── PATCH 4: Glucose chart (24h, 20 evenly spaced points) ────────────────
    readings_raw = glucose.get("readings", [])
    now          = datetime.datetime.now()
    parsed_all = []
    for r in readings_raw:
        try:
            ts = r[0].replace("T", " ")
            parsed_all.append((datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M"), r[1]))
        except Exception:
            pass
    anchor = max((item[0] for item in parsed_all), default=now)
    yesterday    = anchor - datetime.timedelta(hours=24)
    recent       = []
    for r in readings_raw:
        try:
            ts = r[0].replace("T", " ")  # handle both space and ISO-T separator
            t = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M")
            if t >= yesterday:
                recent.append((t, r[1]))
        except Exception:
            pass

    if recent:
        # Raw readings are newest-first; sort ascending so the chart is
        # chronological and ENDS on the most recent reading.
        recent.sort(key=lambda x: x[0])
        if len(recent) >= 20:
            step   = max(1, len(recent) // 20)
            sample = recent[::step][:19]
            if sample[-1] is not recent[-1]:
                sample.append(recent[-1])  # guarantee last point is most recent
        else:
            sample = recent

        g_labels = [r[0].strftime("%-I:%M %p") for r in sample]
        g_data   = [r[1] for r in sample]

        html, ok = sub1(
            r"(new Chart\(glucoseCtx,.*?labels: )\[.*?\]",
            lambda m: m.group(1) + json.dumps(g_labels),
            html,
            flags=re.DOTALL
        )
        if ok:
            patches_applied.append(f"glucose_labels ({len(sample)} points)")

        html, ok = sub1(
            r"(label: 'Glucose \(mg/dL\)',\s+data: )\[.*?\]",
            lambda m: m.group(1) + json.dumps(g_data),
            html,
            flags=re.DOTALL
        )
        if ok:
            patches_applied.append("glucose_data")
    else:
        gaps.append(f"glucose_raw.json had 0 readings in last 24h — glucose chart not updated")

    # ── PATCH 5: Heart Rate chart ─────────────────────────────────────────────
    if hr_data is not None:
        hr_readings = hr_data.get("readings", [])[-7:]
        if hr_readings:
            hr_labels = [r["date_label"] for r in hr_readings]
            hr_vals   = [r["heart_rate"]  for r in hr_readings]

            html, ok = sub1(
                r"(new Chart\(hrCtx,.*?labels: )\[.*?\]",
                lambda m: m.group(1) + json.dumps(hr_labels),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append("hr_labels")

            html, ok = sub1(
                r"(label: 'Resting HR \(bpm\)',\s+data: )\[.*?\]",
                lambda m: m.group(1) + json.dumps(hr_vals),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append("hr_data")
        else:
            gaps.append("hr_raw.json readings array is empty — HR chart not updated")

    # ── PATCH 6: Activity chart (steps) ──────────────────────────────────────
    if act_data is not None:
        act_days = act_data.get("days", [])[-7:]
        if act_days:
            act_labels = [r["date_label"] for r in act_days]
            act_steps  = [r["steps"]      for r in act_days]

            html, ok = sub1(
                r"(new Chart\(activityCtx,.*?labels: )\[.*?\]",
                lambda m: m.group(1) + json.dumps(act_labels),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append("activity_labels")

            html, ok = sub1(
                r"(label: 'Steps',\s+data: )\[.*?\]",
                lambda m: m.group(1) + json.dumps(act_steps),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append("activity_steps")
        else:
            gaps.append("activity_raw.json days array is empty — Activity chart not updated")

    # ── PATCH 7: Visceral Fat / Vascular Age ──────────────────────────────────
    sessions_bc = bodycomp.get("sessions", [])
    if sessions_bc:
        latest_bc = sessions_bc[0]   # most recent first
        vf = next((row.get("visceral_fat") for row in sessions_bc if row.get("visceral_fat") is not None), None)
        va = next((row.get("vascular_age") for row in sessions_bc if row.get("vascular_age") is not None), None)

        if vf is not None and va is not None:
            html, ok = sub1(
                r"(label: '7-Day Avg',\s+data: )\[[\d., ]+\]",
                lambda m: m.group(1) + json.dumps([round(float(vf), 2), round(float(va), 2)]),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append(f"visceral_vascular ({vf:.2f}, {va:.2f})")
            else:
                gaps.append("visceral/vascular regex did not match — patch skipped")
        elif vf is not None:
            # Update only visceral fat, preserve vascular age
            html, ok = sub1(
                r"(label: '7-Day Avg',\s+data: )\[([\d.]+),\s*([\d.]+)\]",
                lambda m: m.group(1) + json.dumps([round(float(vf), 2), float(m.group(3))]),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append(f"visceral_only ({vf:.2f})")
                gaps.append("vascular_age missing from bodycomp — kept existing vascular age")
        else:
            gaps.append("visceral_fat unavailable across bodycomp source sessions — patch skipped")
    else:
        gaps.append("bodycomp_raw.json sessions array is empty — visceral/vascular not updated")

    # ── PATCH 8: Training chart (last 10 lift sessions) ───────────────────────
    lift_sessions = training.get("lift_sessions", [])
    if not lift_sessions:
        # Fallback: filter sessions array manually
        lift_sessions = [s for s in training.get("sessions", []) if s.get("type") == "Lift Weights"]

    lift_sessions = lift_sessions[-10:]  # last 10 only

    if lift_sessions:
        def to_label(date_str):
            """Convert "2026-08-06" to "Aug 6"."""
            try:
                dt = datetime.datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
                return dt.strftime("%b %-d")
            except Exception:
                return str(date_str)[:6]

        t_labels    = [to_label(s.get("date", "")) for s in lift_sessions]
        t_minutes   = [s.get("minutes") or 0       for s in lift_sessions]
        t_intensity = [s.get("intensity")            for s in lift_sessions]
        t_hr        = [s.get("hr_avg")               for s in lift_sessions]

        html, ok = sub1(
            r"const trainingLabels\s*=\s*\[.*?\];",
            f"const trainingLabels    = {json.dumps(t_labels)};",
            html
        )
        if ok:
            patches_applied.append("training_labels")

        html, ok = sub1(
            r"const trainingMinutes\s*=\s*\[.*?\];",
            f"const trainingMinutes   = {json.dumps(t_minutes)};",
            html
        )
        if ok:
            patches_applied.append("training_minutes")

        html, ok = sub1(
            r"const trainingIntensity\s*=\s*\[.*?\];",
            f"const trainingIntensity = {json.dumps(t_intensity)};",
            html
        )
        if ok:
            patches_applied.append("training_intensity")

        html, ok = sub1(
            r"const trainingHr\s*=\s*\[.*?\];",
            f"const trainingHr        = {json.dumps(t_hr)};",
            html
        )
        if ok:
            patches_applied.append("training_hr")

    # ── PATCH 8b: Weekly Training Load chart (trainingLoadChart) ─────────────
    weekly = training.get("weekly", [])
    if weekly:
        w_labels = [w["week"].split("-")[-1] for w in weekly]      # W27..W33
        w_walk   = [w.get("walk_min", 0)   for w in weekly]
        w_lift   = [w.get("lift_min", 0)   for w in weekly]
        w_cardio = [w.get("cardio_min", 0) for w in weekly]
        w_ratio  = [w.get("ratio")         for w in weekly]

        # Anchor on the trainingLoadChart block and replace its labels + 4 datasets.
        def _tl_sub(pattern, repl):
            nonlocal html
            new, n = re.subn(pattern, repl, html, count=1, flags=re.DOTALL)
            if n:
                html = new
                return True
            return False

        tl_ok = False
        # labels array (first labels: after trainingLoadChart)
        m = re.search(r"getElementById\('trainingLoadChart'\).*?Aerobic to Resistance', data:\[.*?\]", html, re.DOTALL)
        if m:
            block = m.group(0)
            block2 = re.sub(r"labels:\s*\[.*?\]", "labels: " + json.dumps(w_labels), block, count=1, flags=re.DOTALL)
            block2 = re.sub(r"(label:'Walk', data:)\[.*?\]", lambda mm: mm.group(1) + json.dumps(w_walk), block2, count=1, flags=re.DOTALL)
            block2 = re.sub(r"(label:'Lift', data:)\[.*?\]", lambda mm: mm.group(1) + json.dumps(w_lift), block2, count=1, flags=re.DOTALL)
            block2 = re.sub(r"(label:'Other cardio', data:)\[.*?\]", lambda mm: mm.group(1) + json.dumps(w_cardio), block2, count=1, flags=re.DOTALL)
            block2 = re.sub(r"(label:'Aerobic to Resistance', data:)\[.*?\]", lambda mm: mm.group(1) + json.dumps(w_ratio), block2, count=1, flags=re.DOTALL)
            if block2 != block:
                html = html.replace(block, block2, 1)
                tl_ok = True
        if tl_ok:
            patches_applied.append("training_weekly_load")
        else:
            gaps.append("trainingLoadChart weekly block not patched")


    # ── PATCH 9: 3-Year Transformation chart (last data point only) ───────────
    if sessions_bc:
        latest_bc  = sessions_bc[0]
        cur_weight_raw = latest_bc.get("lbs") or latest_bc.get("weight")
        cur_fat_raw = next((row.get("fat_ratio") for row in sessions_bc if row.get("fat_ratio") is not None), None)
        cur_weight = round(float(cur_weight_raw or 0), 1)
        cur_fat    = round(float(cur_fat_raw or 0), 2)

        if cur_weight > 0:
            html, ok = sub1(
                r"(label: 'Weight \(lbs\)',\s+data: \[[\d., \n]+,\s*)([\d.]+)(\s*\],)",
                lambda m: m.group(1) + str(cur_weight) + m.group(3),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append(f"transform_weight (last point → {cur_weight})")
            else:
                gaps.append("transform weight regex did not match — patch skipped")

        if cur_fat > 0:
            html, ok = sub1(
                r"(label: 'Body Fat %',\s+data: \[[\d., \n]+,\s*)([\d.]+)(\s*\],)",
                lambda m: m.group(1) + str(cur_fat) + m.group(3),
                html,
                flags=re.DOTALL
            )
            if ok:
                patches_applied.append(f"transform_bodyfat (last point → {cur_fat})")
            else:
                gaps.append("transform body fat regex did not match — patch skipped")

    # ── PATCH 10: Narrative / HUD live actuals (glucose title, walk-block) ─────
    # Glucose card title date → newest CGM reading date
    try:
        g_readings = glucose["readings"] if isinstance(glucose, dict) else glucose
        if g_readings:
            newest_ts = g_readings[0][0].replace("T", " ")
            g_dt = datetime.datetime.strptime(newest_ts, "%Y-%m-%d %H:%M")
            g_label = g_dt.strftime("%b %-d, %Y")
            html, ok = sub1(
                r"(Stelo CGM 24h Curve \()[A-Za-z]+ \d{1,2}, \d{4}(\))",
                lambda m: m.group(1) + g_label + m.group(2),
                html
            )
            if ok:
                patches_applied.append(f"glucose_title_date ({g_label})")
    except Exception as e:
        gaps.append(f"glucose title date not patched: {e}")

    # Walk-block live actuals: 4-week rolling walk minutes + current visceral/vascular
    weekly_all = training.get("weekly", [])
    complete_wks = [w for w in weekly_all if w.get("lift_min") is not None][:-1] if len(weekly_all) > 1 else weekly_all
    last4 = complete_wks[-4:]
    if last4 and sessions_bc:
        avg_walk = round(sum(w.get("walk_min", 0) for w in last4) / len(last4))
        vf_now = next((row.get("visceral_fat") for row in sessions_bc if row.get("visceral_fat") is not None), None)
        va_now = next((row.get("vascular_age") for row in sessions_bc if row.get("vascular_age") is not None), None)
        if vf_now is not None and va_now is not None:
            # "roughly 465 minutes a week ... visceral fat at 4.5 and vascular age at 55.3"
            html, ok = sub1(
                r"(roughly )\d+( minutes a week of zone-0 NEAT and it is a primary driver of visceral fat at )[\d.]+( and vascular age at )[\d.]+",
                lambda m: m.group(1) + str(avg_walk) + m.group(2) + f"{float(vf_now):.1f}" + m.group(3) + f"{float(va_now):.1f}",
                html
            )
            if ok:
                patches_applied.append(f"walk_block_actuals (walk {avg_walk}, vf {vf_now}, va {va_now})")
            # walk-block td line: "465 min/week"
            html, ok2 = sub1(
                r"(<div class=\"td\">)\d+( min/week &middot; HR 71)",
                lambda m: m.group(1) + str(avg_walk) + m.group(2),
                html
            )
            if ok2:
                patches_applied.append(f"walk_block_td ({avg_walk} min/week)")

    with open(DASHBOARD, "w", encoding="utf-8") as fh:
        fh.write(html)

    shutil.copy(DASHBOARD, INDEX)

    # Verify today's date is present (mirrors publish.sh guard)
    with open(DASHBOARD, encoding="utf-8") as fh:
        check = fh.read(5000)  # only read the header section for date check
    if today not in check:
        # Try full file
        if today not in html:
            print(f"WARN: today's date ({today}) not found in patched HTML", file=sys.stderr)

    # ── Summary ───────────────────────────────────────────────────────────────
    summary = {
        "status":           "OK" if not gaps else "PARTIAL",
        "date":             today,
        "patches_applied":  patches_applied,
        "patch_count":      len(patches_applied),
        "gaps":             gaps,
        "generated":        datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(SUMMARY, "w") as fh:
        json.dump(summary, fh, indent=2)

    # ONLY compact summary to stdout — never the HTML
    print(json.dumps({
        "status":      summary["status"],
        "date":        today,
        "patches":     len(patches_applied),
        "gaps":        len(gaps),
        "patch_list":  patches_applied,
        "gap_list":    gaps,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Roster logic: generate, validate, suggest fixes, build views.

Status codes in a roster cell:
  W  = working        WO = weekly off      PH = public holiday off
  L  = planned leave  CO = comp-off        (L and CO come from the leaves table)
"""
import calendar
import csv
import datetime as dt
import io

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CALENDAR_NAMES = {
    "ALL_DAYS": "365 days",
    "NO_HOLIDAYS": "Every day except public holidays",
    "NO_SUN_HOL": "Closed Sundays and public holidays",
}
MIN_WEEK_DAYS = 5  # a week cut by the month edge needs a weekly off only if it has 5+ days


# ---------- small helpers ----------

def month_dates(month):
    year, mon = map(int, month.split("-"))
    return [dt.date(year, mon, d) for d in range(1, calendar.monthrange(year, mon)[1] + 1)]


def parse_overrides(text):
    """'Sat=2,Mon=3' -> {'Sat': 2, 'Mon': 3}"""
    out = {}
    for part in (text or "").split(","):
        if "=" in part:
            day, num = part.split("=", 1)
            day, num = day.strip()[:3].title(), num.strip()
            if day in DAYS and num.isdigit():
                out[day] = int(num)
    return out


def is_open(calendar_code, d, holidays):
    if calendar_code == "ALL_DAYS":
        return True
    if calendar_code == "NO_HOLIDAYS":
        return d not in holidays
    return d.weekday() != 6 and d not in holidays  # NO_SUN_HOL


def split_weeks(dates):
    """Split the month into Mon-Sun week pieces."""
    weeks = {}
    for d in dates:
        weeks.setdefault(d.isocalendar()[:2], []).append(d)
    return list(weeks.values())


# ---------- loading data ----------

def load(db, month):
    dates = month_dates(month)
    first, last = dates[0].isoformat(), dates[-1].isoformat()
    ctx = {"month": month, "dates": dates}

    ctx["holidays"] = {dt.date.fromisoformat(r["date"]): r["name"]
                       for r in db.execute("SELECT date, name FROM holidays")}

    pods = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM pods")}
    ctx["pods"] = pods
    ctx["emps"] = {r["id"]: {**dict(r), "pod": pods.get(r["pod_id"], "-")}
                   for r in db.execute("SELECT * FROM employees ORDER BY id")}

    ctx["bps"] = {}
    for r in db.execute("""SELECT bp.*, b.name AS brand, p.name AS process
                           FROM brand_processes bp
                           JOIN brands b ON b.id = bp.brand_id
                           JOIN processes p ON p.id = bp.process_id"""):
        bp = dict(r)
        bp["label"] = f"{r['brand']} / {r['process']}"
        bp["overrides"] = parse_overrides(r["day_overrides"])
        ctx["bps"][r["id"]] = bp

    ctx["assigns"] = {e: [] for e in ctx["emps"]}   # employee -> [bp ids]
    ctx["members"] = {b: [] for b in ctx["bps"]}    # bp -> [employee ids]
    ctx["alloc"] = {e: 0 for e in ctx["emps"]}      # employee -> total %
    ctx["alloc_of"] = {}                            # (employee, bp) -> %
    for r in db.execute("SELECT * FROM assignments"):
        if r["employee_id"] in ctx["emps"] and r["brand_process_id"] in ctx["bps"]:
            ctx["assigns"][r["employee_id"]].append(r["brand_process_id"])
            ctx["members"][r["brand_process_id"]].append(r["employee_id"])
            ctx["alloc"][r["employee_id"]] += r["allocation"]
            ctx["alloc_of"][(r["employee_id"], r["brand_process_id"])] = r["allocation"]

    ctx["leaves"] = {(r["employee_id"], dt.date.fromisoformat(r["date"])): r["type"]
                     for r in db.execute("SELECT * FROM leaves WHERE date BETWEEN ? AND ?",
                                         (first, last))}
    return ctx


def required(ctx, bp_id, d):
    """How many people the process needs on date d (0 = closed)."""
    bp = ctx["bps"][bp_id]
    if not is_open(bp["calendar"], d, ctx["holidays"]):
        return 0
    return bp["overrides"].get(DAYS[d.weekday()], bp["min_staff"])


def emp_open(ctx, e, d):
    """Does this employee have any open process on d?
    Someone with no assignments (for example a pod lead) follows office days."""
    bps = ctx["assigns"][e]
    if not bps:
        return is_open("NO_SUN_HOL", d, ctx["holidays"])
    return any(required(ctx, b, d) > 0 for b in bps)


def working(ctx, status, bp_id, d):
    return [m for m in ctx["members"][bp_id] if status.get((m, d)) == "W"]


def is_weekly_off(status, e, d):
    s = status.get((e, d))
    return s == "WO" or (s == "PH" and d.weekday() == 6)  # PH on a Sunday counts as the off


# ---------- generate ----------

# Each strategy is one way to trade things off. Coverage always wins (penalty 1000).
#   pref = bonus for giving the weekly off on the preferred day
#   fair = penalty per weekend off the person already got, so weekend offs rotate
STRATEGIES = {
    "balanced": {"label": "Balanced (default)", "pref": 3, "fair": 1.0},
    "coverage": {"label": "Coverage first (ignore preferences)", "pref": 0, "fair": 0.0},
    "preference": {"label": "Preference first", "pref": 40, "fair": 0.0},
    "fair": {"label": "Fair rotation of weekend offs", "pref": 1, "fair": 6.0},
}


def _off_cost(ctx, status, e, d, params, weekend_offs):
    """How bad is it to give employee e an off on date d? Lower is better."""
    cost = 0.0
    for bp_id in ctx["assigns"][e]:
        need = required(ctx, bp_id, d)
        if need == 0:
            continue
        left = len(working(ctx, status, bp_id, d)) - 1
        cost += 1000 * (need - left) if left < need else 10.0 / (left - need + 1)
    pref = ctx["emps"][e]["preferred_off"]
    if pref is not None and d.weekday() == pref:
        cost -= params["pref"]
    if d.weekday() >= 5:
        cost += params["fair"] * weekend_offs[e]
    return cost


def plan(ctx, strategy="balanced", locked=None):
    """Build a full month in memory. locked = {(employee, date): code} cells kept as they are."""
    params = STRATEGIES[strategy]
    locked = locked or {}
    status = {}
    for e in ctx["emps"]:
        for d in ctx["dates"]:
            if (e, d) in ctx["leaves"]:
                status[(e, d)] = ctx["leaves"][(e, d)]
            elif (e, d) in locked:
                status[(e, d)] = locked[(e, d)]
            elif not emp_open(ctx, e, d):
                status[(e, d)] = "PH" if d in ctx["holidays"] else "WO"
            else:
                status[(e, d)] = "W"

    # People with the fewest colleagues on their processes go first (they are the tightest).
    def colleagues(e):
        return len({m for b in ctx["assigns"][e] for m in ctx["members"][b]})

    order = sorted(ctx["emps"], key=lambda e: (colleagues(e), e))
    weekend_offs = {e: 0 for e in ctx["emps"]}

    for week in split_weeks(ctx["dates"]):
        for e in order:
            need = ctx["emps"][e]["weekly_offs"] if len(week) >= MIN_WEEK_DAYS else 0
            have = sum(1 for d in week if is_weekly_off(status, e, d))
            for _ in range(max(0, need - have)):
                options = [d for d in week if status[(e, d)] == "W" and (e, d) not in locked]
                if not options:
                    break
                best = min(options, key=lambda d: (_off_cost(ctx, status, e, d, params, weekend_offs),
                                                   (d.toordinal() + e) % 7))
                status[(e, best)] = "WO"
                if best.weekday() >= 5:
                    weekend_offs[e] += 1
    return status


def generate(db, month, strategy="balanced", keep_locked=False):
    """Build the roster and save it. keep_locked keeps cells the manager edited by hand.
    Returns the set of employees whose saved days changed."""
    ctx = load(db, month)
    first, last = ctx["dates"][0].isoformat(), ctx["dates"][-1].isoformat()
    old = {(r["employee_id"], dt.date.fromisoformat(r["date"])): (r["status"], r["locked"])
           for r in db.execute("SELECT * FROM roster WHERE date BETWEEN ? AND ?", (first, last))}
    locked = {k: v[0] for k, v in old.items() if v[1]} if keep_locked else {}

    status = plan(ctx, strategy, locked)

    db.execute("DELETE FROM roster WHERE date BETWEEN ? AND ?", (first, last))
    rows = [(e, d.isoformat(), s, 1 if (e, d) in locked else 0)
            for (e, d), s in status.items() if s in ("W", "WO", "PH")]
    db.executemany("INSERT INTO roster (employee_id, date, status, locked) VALUES (?,?,?,?)", rows)
    db.execute("INSERT OR IGNORE INTO roster_months (month, status) VALUES (?, 'draft')", (month,))
    db.commit()
    changed = {e for (e, d), s in status.items()
               if old and s in ("W", "WO", "PH") and old.get((e, d), (None,))[0] != s}
    return {"employees": len(ctx["emps"]), "days": len(ctx["dates"]), "changed": changed}


def compare(db, month):
    """Build every strategy in memory (nothing is saved) and score each one."""
    ctx = load(db, month)
    sunday_workers = [e for e in ctx["emps"]
                      if any(d.weekday() == 6 and emp_open(ctx, e, d) for d in ctx["dates"])]
    results = []
    for key, params in STRATEGIES.items():
        status = plan(ctx, key)
        issues = validate(ctx, status, with_suggestions=False)
        spare = [len(working(ctx, status, b, d)) - required(ctx, b, d)
                 for b in ctx["bps"] if ctx["members"][b]
                 for d in ctx["dates"] if required(ctx, b, d)]
        weekend = [sum(1 for d in ctx["dates"] if status[(e, d)] == "WO" and d.weekday() >= 5)
                   for e in sunday_workers]
        results.append({
            "strategy": key, "label": params["label"],
            "errors": sum(i["severity"] == "error" for i in issues),
            "unmet_pref_weeks": sum(i.get("missed", 0) for i in issues if i["type"] == "preference"),
            "avg_spare": round(sum(spare) / len(spare), 2) if spare else 0,
            "weekend_spread": (max(weekend) - min(weekend)) if weekend else 0,
        })
    return results


# ---------- read the current roster ----------

def build_status(db, month):
    """Stored roster with leaves / comp-offs laid on top (they always win)."""
    ctx = load(db, month)
    first, last = ctx["dates"][0].isoformat(), ctx["dates"][-1].isoformat()
    status = {}
    for r in db.execute("SELECT * FROM roster WHERE date BETWEEN ? AND ?", (first, last)):
        status[(r["employee_id"], dt.date.fromisoformat(r["date"]))] = r["status"]
    generated = bool(status)
    if generated:
        for key, kind in ctx["leaves"].items():
            status[key] = kind
    return ctx, status, generated


# ---------- validate ----------

def _can_be_off(ctx, status, e, d):
    for bp_id in ctx["assigns"][e]:
        need = required(ctx, bp_id, d)
        if need and len(working(ctx, status, bp_id, d)) - 1 < need:
            return False
    return True


def _suggestions(ctx, status, bp_id, d):
    out = []
    bp = ctx["bps"][bp_id]
    week = next(w for w in split_weeks(ctx["dates"]) if d in w)

    # 1. Someone assigned is on a weekly off today. Can that off move to another day?
    for m in ctx["members"][bp_id]:
        if status.get((m, d)) == "WO" and emp_open(ctx, m, d):
            for d2 in week:
                if d2 != d and status.get((m, d2)) == "W" and _can_be_off(ctx, status, m, d2):
                    out.append({"type": "move_off", "employee_id": m,
                                "from": d.isoformat(), "to": d2.isoformat(),
                                "text": f"Move {ctx['emps'][m]['name']}'s weekly off "
                                        f"from {d:%d %b} to {d2:%d %b}"})
                    break

    # 2. Add a backup person who is free on that day and has spare time.
    pods = {ctx["emps"][m]["pod"] for m in ctx["members"][bp_id]}
    backups = []
    for e, emp in ctx["emps"].items():
        spare = 100 - ctx["alloc"][e]
        if e not in ctx["members"][bp_id] and status.get((e, d)) == "W" and spare >= 20:
            backups.append((emp["pod"] not in pods, -spare, e))
    for _, _, e in sorted(backups)[:3]:
        out.append({"type": "add_backup", "employee_id": e, "brand_process_id": bp_id,
                    "text": f"Add {ctx['emps'][e]['name']} ({ctx['emps'][e]['pod']}, "
                            f"{100 - ctx['alloc'][e]}% spare) as backup for {bp['label']}"})
    return out


def validate(ctx, status, with_suggestions=True):
    """Return a list of issues. severity 'error' = must fix, 'warning' = preference."""
    issues = []

    def add(severity, kind, message, **extra):
        issues.append({"severity": severity, "type": kind, "message": message, **extra})

    # Processes nobody is assigned to
    for bp_id, bp in ctx["bps"].items():
        if not ctx["members"][bp_id]:
            add("error", "no_staff", f"{bp['label']} has no employee assigned.", brand_process_id=bp_id)

    # Employees allocated more than 100%
    for e, total in ctx["alloc"].items():
        if total > 100:
            add("error", "overallocated",
                f"{ctx['emps'][e]['name']} is allocated {total}% across brands (max 100%).",
                employee_id=e)

    # Coverage on every day each process is open
    for bp_id, bp in ctx["bps"].items():
        members = ctx["members"][bp_id]
        if not members:
            continue
        for d in ctx["dates"]:
            need = required(ctx, bp_id, d)
            if need == 0:
                continue
            have = working(ctx, status, bp_id, d)
            if len(have) >= need:
                continue
            away = [f"{ctx['emps'][m]['name']} ({status.get((m, d), '?')})"
                    for m in members if m not in have]
            if not have:
                msg = f"{bp['label']} on {d:%a %d %b}: nobody is working, {need} needed."
            else:
                msg = (f"{bp['label']} on {d:%a %d %b}: only {len(have)} working, "
                       f"{need} needed.")
            msg += f" Not available: {', '.join(away)}."
            issue = {"date": d.isoformat(), "brand_process_id": bp_id}
            if with_suggestions:
                issue["suggestions"] = _suggestions(ctx, status, bp_id, d)
            add("error", "coverage_gap", msg, **issue)

    # Weekly off rule and preferences
    for e, emp in ctx["emps"].items():
        missed_pref = 0
        for week in split_weeks(ctx["dates"]):
            if not any(status.get((e, d)) for d in week):
                continue
            need = emp["weekly_offs"] if len(week) >= MIN_WEEK_DAYS else 0
            offs = sum(1 for d in week if is_weekly_off(status, e, d))
            can_work = any(status.get((e, d)) == "W" for d in week)
            if need and offs < need and can_work:
                add("error", "no_weekly_off",
                    f"{emp['name']} has {offs} weekly off (needs {need}) in the week "
                    f"of {week[0]:%d %b}.", employee_id=e, date=week[0].isoformat())
            pref = emp["preferred_off"]
            if pref is not None and need:
                day = next((d for d in week if d.weekday() == pref), None)
                if day and status.get((e, day)) == "W":
                    missed_pref += 1
        if missed_pref:
            add("warning", "preference",
                f"{emp['name']} prefers {DAYS[emp['preferred_off']]} off but did not get it "
                f"in {missed_pref} week(s).", employee_id=e, missed=missed_pref)
    return issues


# ---------- views ----------

def _assign_text(ctx, e):
    parts = []
    for b in ctx["assigns"][e]:
        bp = ctx["bps"][b]
        pct = ctx["alloc_of"][(e, b)]
        parts.append(f"{bp['label']}" + (f" {pct}%" if pct != 100 else ""))
    return ", ".join(parts) or "No brand (pod lead / support)"


def build_view(db, month, kind, target):
    ctx, status, generated = build_status(db, month)
    emps, bps = ctx["emps"], ctx["bps"]
    lead_ids = {r["lead_id"] for r in db.execute("SELECT lead_id FROM pods") if r["lead_id"]}

    if kind == "pod":
        emp_ids = [e for e in emps if emps[e]["pod_id"] == target]
        bp_ids = sorted({b for e in emp_ids for b in ctx["assigns"][e]})
    elif kind == "brand":
        bp_ids = [b for b in bps if bps[b]["brand_id"] == target]
        emp_ids = sorted({m for b in bp_ids for m in ctx["members"][b]})
    elif kind == "process":
        bp_ids = [b for b in bps if bps[b]["process_id"] == target]
        emp_ids = sorted({m for b in bp_ids for m in ctx["members"][b]})
    elif kind == "employee":
        emp_ids = [e for e in emps if e == target]
        bp_ids = sorted({b for e in emp_ids for b in ctx["assigns"][e]})
    else:  # company
        emp_ids, bp_ids = list(emps), list(bps)

    emp_ids.sort(key=lambda e: (emps[e]["pod"], e not in lead_ids, emps[e]["name"]))
    bp_ids.sort(key=lambda b: bps[b]["label"])

    first, last = ctx["dates"][0].isoformat(), ctx["dates"][-1].isoformat()
    locked = {(r["employee_id"], r["date"]) for r in db.execute(
        "SELECT employee_id, date FROM roster WHERE locked = 1 AND date BETWEEN ? AND ?", (first, last))}

    rows = []
    for e in emp_ids:
        cells = [status.get((e, d), "") for d in ctx["dates"]]
        rows.append({"id": e, "name": emps[e]["name"], "pod": emps[e]["pod"],
                     "lead": e in lead_ids, "assign": _assign_text(ctx, e), "cells": cells,
                     "locked": [(e, d.isoformat()) in locked for d in ctx["dates"]],
                     "weekend_offs": sum(1 for d in ctx["dates"]
                                         if status.get((e, d)) == "WO" and d.weekday() >= 5),
                     "counts": {c: cells.count(c) for c in ("W", "WO", "PH", "L", "CO")}})

    coverage = []
    for b in bp_ids:
        cells = []
        for d in ctx["dates"]:
            have = working(ctx, status, b, d)
            cells.append({"need": required(ctx, b, d), "have": len(have) if generated else None,
                          "who": ", ".join(emps[m]["name"] for m in have)})
        coverage.append({"id": b, "label": bps[b]["label"],
                         "calendar": CALENDAR_NAMES[bps[b]["calendar"]], "cells": cells})

    month_row = db.execute("SELECT status FROM roster_months WHERE month = ?", (month,)).fetchone()
    return {
        "month": month,
        "dates": [{"date": d.isoformat(), "day": d.day, "dow": DAYS[d.weekday()],
                   "holiday": ctx["holidays"].get(d, "")} for d in ctx["dates"]],
        "rows": rows, "coverage": coverage, "generated": generated,
        "state": month_row["status"] if month_row else "none",
    }


def export_csv(db, month, kind, target):
    """CSV text of the current view. Opens in Excel and imports into Google Sheets."""
    view = build_view(db, month, kind, target)
    out = io.StringIO()
    w = csv.writer(out)
    days = [f"{d['day']} {d['dow']}" for d in view["dates"]]
    w.writerow(["Employee", "Pod", "Assignments"] + days +
               ["Working", "Weekly off", "Holiday", "Leave", "Comp-off"])
    for r in view["rows"]:
        c = r["counts"]
        w.writerow([r["name"], r["pod"], r["assign"]] + r["cells"] +
                   [c["W"], c["WO"], c["PH"], c["L"], c["CO"]])
    if view["coverage"]:
        w.writerow([])
        w.writerow(["Coverage (working/needed)", "Calendar", ""] + days)
        for row in view["coverage"]:
            w.writerow([row["label"], row["calendar"], ""] +
                       [("closed" if c["need"] == 0 else f"{c['have']}/{c['need']}") for c in row["cells"]])
    return out.getvalue()

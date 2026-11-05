"""Flask API + static frontend."""
import datetime as dt
import os
import sqlite3


def load_env():
    """Read KEY=value lines from .env next to this file. Real environment variables win."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.environ.get("ROSTER_NO_DOTENV") or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()  # must run before db is imported, because db reads DB_PATH

from flask import Flask, Response, g, jsonify, request, send_from_directory

from backend import db as dbm
from backend import explain, roster, seed, workflow

app = Flask(__name__, static_folder="static", static_url_path="/static")

# Tables the manager can edit from the UI, and the columns allowed for each.
TABLES = {
    "pods": ["name", "lead_id"],
    "employees": ["name", "pod_id", "weekly_offs", "preferred_off"],
    "brands": ["name"],
    "processes": ["name"],
    "brand_processes": ["brand_id", "process_id", "calendar", "min_staff", "day_overrides"],
    "assignments": ["employee_id", "brand_process_id", "allocation"],
    "leaves": ["employee_id", "date", "type", "note"],
    "holidays": ["date", "name"],
}
INT_COLUMNS = {"lead_id", "pod_id", "weekly_offs", "preferred_off", "brand_id", "process_id",
               "min_staff", "employee_id", "brand_process_id", "allocation"}


def conn():
    """One database connection per request, closed when the request ends."""
    if "db" not in g:
        g.db = dbm.get_db()
    return g.db


@app.teardown_appcontext
def close_db(_error):
    c = g.pop("db", None)
    if c is not None:
        c.close()


def clean(table, data):
    """Keep only allowed columns and convert numbers. Empty text becomes NULL."""
    out = {}
    for col in TABLES[table]:
        if col not in data:
            continue
        val = data[col]
        if val in ("", None):
            out[col] = None
        elif col in INT_COLUMNS:
            try:
                out[col] = int(val)
            except (TypeError, ValueError):
                raise ValueError(f"{col} must be a number")
        else:
            out[col] = str(val).strip()
    return out


@app.errorhandler(sqlite3.IntegrityError)
def integrity_error(e):
    return jsonify(error=f"Not saved: {e}. Check required fields and duplicates."), 400


@app.errorhandler(ValueError)
def value_error(e):
    return jsonify(error=str(e)), 400


def month_arg():
    month = request.args.get("month") or (request.get_json(silent=True) or {}).get("month")
    try:
        roster.month_dates(month)
    except Exception:
        raise ValueError("month must look like 2026-11")
    return month


# ---------- generic CRUD (add / edit / remove team data without code changes) ----------

def after_edit(c, table, action, old, new):
    """Team data changed. Re-plan or flag the rosters it touches."""
    if table in ("leaves", "holidays"):
        days = [src["date"] for src in (old, new) if src and src.get("date")]
        if new and new.get("to_date"):
            days.append(new["to_date"])
        months = {d[:7] for d in days}
    else:
        months = set(workflow.roster_months(c))
    emps = {src["employee_id"] for src in (old, new) if src and src.get("employee_id")}
    if table == "employees" and old:
        emps.add(old["id"])
    workflow.changed(c, sorted(months), f"{table.replace('_', ' ')} {action}", emps)


@app.get("/api/<table>")
def list_rows(table):
    if table not in TABLES:
        return jsonify(error="unknown table"), 404
    order = "date" if table in ("leaves", "holidays") else "id"
    rows = conn().execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/<table>")
def add_row(table):
    if table not in TABLES:
        return jsonify(error="unknown table"), 404
    c = conn()
    data = request.get_json() or {}
    # Leaves can be added as a date range: send date and to_date
    if table == "leaves" and data.get("to_date"):
        start = dt.date.fromisoformat(data["date"])
        end = dt.date.fromisoformat(data["to_date"])
        if end < start:
            return jsonify(error="to_date is before date"), 400
        for n in range((end - start).days + 1):
            row = clean(table, {**data, "date": (start + dt.timedelta(days=n)).isoformat()})
            c.execute("INSERT OR REPLACE INTO leaves (employee_id, date, type, note) "
                      "VALUES (:employee_id, :date, :type, :note)",
                      {"note": "", "type": "L", **row})
        c.commit()
        after_edit(c, table, "added", None, {**clean(table, data), "to_date": data["to_date"]})
        return jsonify(ok=True)
    row = clean(table, data)
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    cur = c.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", row)
    c.commit()
    after_edit(c, table, "added", None, row)
    return jsonify(id=cur.lastrowid)


@app.put("/api/<table>/<int:row_id>")
def update_row(table, row_id):
    if table not in TABLES:
        return jsonify(error="unknown table"), 404
    c = conn()
    old = c.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    row = clean(table, request.get_json() or {})
    sets = ", ".join(f"{k} = :{k}" for k in row)
    c.execute(f"UPDATE {table} SET {sets} WHERE id = :id", {**row, "id": row_id})
    c.commit()
    after_edit(c, table, "changed", dict(old) if old else None, row)
    return jsonify(ok=True)


@app.delete("/api/<table>/<int:row_id>")
def delete_row(table, row_id):
    if table not in TABLES:
        return jsonify(error="unknown table"), 404
    c = conn()
    old = c.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    c.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    c.commit()
    after_edit(c, table, "removed", dict(old) if old else None, None)
    return jsonify(ok=True)


# ---------- settings ----------

@app.get("/api/settings")
def get_settings():
    return jsonify(auto_regen=workflow.get_setting(conn(), "auto_regen") == "1")


@app.put("/api/settings")
def put_settings():
    data = request.get_json() or {}
    workflow.set_setting(conn(), "auto_regen", "1" if data.get("auto_regen") else "0")
    return jsonify(ok=True)


# ---------- roster ----------

@app.get("/api/months")
def months():
    rows = conn().execute("SELECT month, status FROM roster_months ORDER BY month").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/roster/generate")
def generate():
    c = conn()
    month = month_arg()
    data = request.get_json() or {}
    strategy = data.get("strategy", "balanced")
    if strategy not in roster.STRATEGIES:
        return jsonify(error="unknown plan type"), 400
    roster.generate(c, month, strategy, keep_locked=bool(data.get("keep_locked")))
    workflow.touch(c, month, "roster re-planned")
    ctx, status, _ = roster.build_status(c, month)
    found = roster.validate(ctx, status, with_suggestions=False)
    return jsonify(errors=sum(i["severity"] == "error" for i in found),
                   warnings=sum(i["severity"] == "warning" for i in found))


@app.get("/api/compare")
def compare():
    return jsonify(plans=roster.compare(conn(), month_arg()))


@app.get("/api/roster")
def roster_view():
    kind = request.args.get("view", "company")
    target = request.args.get("id", type=int)
    return jsonify(roster.build_view(conn(), month_arg(), kind, target))


@app.get("/api/roster/export")
def roster_export():
    month = month_arg()
    kind = request.args.get("view", "company")
    text = roster.export_csv(conn(), month, kind, request.args.get("id", type=int))
    return Response("﻿" + text, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=roster-{month}-{kind}.csv"})


@app.get("/api/issues")
def issues():
    ctx, status, generated = roster.build_status(conn(), month_arg())
    found = roster.validate(ctx, status) if generated else []
    return jsonify(generated=generated, issues=found,
                   errors=sum(i["severity"] == "error" for i in found),
                   warnings=sum(i["severity"] == "warning" for i in found))


@app.post("/api/issues/explain")
def explain_issue():
    month = month_arg()
    ctx, status, generated = roster.build_status(conn(), month)
    if not generated:
        return jsonify(error="Generate the roster first."), 400
    try:
        return jsonify(explain.explain(ctx, status, (request.get_json() or {}).get("issue") or {}))
    except (KeyError, ValueError):
        return jsonify(error="Cannot explain this issue (it may have been fixed already)."), 400


@app.put("/api/roster/cell")
def edit_cell():
    c = conn()
    data = request.get_json() or {}
    emp, day, new = int(data["employee_id"]), data["date"], data["status"]
    month = day[:7]
    if new not in ("W", "WO", "PH"):
        return jsonify(error="status must be W, WO or PH"), 400
    if not c.execute("SELECT 1 FROM roster_months WHERE month = ?", (month,)).fetchone():
        return jsonify(error="Generate the roster first."), 400
    if c.execute("SELECT 1 FROM leaves WHERE employee_id = ? AND date = ?", (emp, day)).fetchone():
        return jsonify(error="This day is a leave / comp-off. Change it in Leaves & Holidays."), 400
    c.execute("INSERT OR REPLACE INTO roster (employee_id, date, status, locked) VALUES (?,?,?,1)",
              (emp, day, new))
    person = c.execute("SELECT name FROM employees WHERE id = ?", (emp,)).fetchone()
    workflow.touch(c, month, f"{person['name']} on {day} set to {new}", workflow.pods_of(c, [emp]))
    c.commit()
    # Validate straight away and report what this change affects
    ctx, status, _ = roster.build_status(c, month)
    found = roster.validate(ctx, status, with_suggestions=False)
    touching = [i for i in found if i.get("employee_id") == emp or i.get("date") == day]
    return jsonify(ok=True, errors=sum(i["severity"] == "error" for i in found),
                   related=[i["message"] for i in touching])


@app.post("/api/suggestion/apply")
def apply_suggestion():
    c = conn()
    s = request.get_json() or {}
    emp = int(s["employee_id"])
    if s.get("type") == "move_off":
        for day, code in ((s["from"], "W"), (s["to"], "WO")):
            c.execute("INSERT OR REPLACE INTO roster (employee_id, date, status, locked) "
                      "VALUES (?,?,?,1)", (emp, day, code))
        workflow.touch(c, s["from"][:7], "a weekly off was moved", workflow.pods_of(c, [emp]))
        c.commit()
    elif s.get("type") == "add_backup":
        c.execute("INSERT OR IGNORE INTO assignments (employee_id, brand_process_id, allocation) "
                  "VALUES (?,?,20)", (emp, int(s["brand_process_id"])))
        c.commit()
        workflow.changed(c, workflow.roster_months(c), "a backup was assigned", [emp])
    else:
        return jsonify(error="unknown suggestion type"), 400
    return jsonify(ok=True)


# ---------- review, approval, notifications ----------

@app.get("/api/review")
def review():
    c = conn()
    month = month_arg()
    info = workflow.review_state(c, month)
    ctx, status, generated = roster.build_status(c, month)
    info["errors"] = sum(i["severity"] == "error" for i in roster.validate(
        ctx, status, with_suggestions=False)) if generated else 0
    return jsonify(info)


@app.post("/api/roster/submit")
def submit():
    workflow.submit(conn(), month_arg())
    return jsonify(ok=True)


@app.post("/api/roster/approve")
def approve():
    workflow.approve(conn(), month_arg(), int((request.get_json() or {})["pod_id"]))
    return jsonify(ok=True)


@app.post("/api/roster/publish")
def publish():
    workflow.publish(conn(), month_arg())
    return jsonify(ok=True)


@app.get("/api/notifications")
def notifications():
    pod_id = request.args.get("pod_id", type=int)
    sql = ("SELECT n.id, n.created_at, n.month, n.message, p.name AS pod FROM notifications n "
           "LEFT JOIN pods p ON p.id = n.pod_id ")
    rows = conn().execute(sql + ("WHERE n.pod_id = ? " if pod_id else "") +
                          "ORDER BY n.id DESC LIMIT 100", (pod_id,) if pod_id else ()).fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


def start():
    c = dbm.init_db()
    if not c.execute("SELECT 1 FROM employees LIMIT 1").fetchone():
        seed.seed(c)
    c.close()


start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

"""Roster life cycle: re-planning after changes, review/approval, notifications.

Month states:  draft  ->  in_review (pod leads approve)  ->  published
Any change to a roster that is in review or published sends it back to draft,
clears the approvals and notifies the affected pod leads.
"""
import datetime as dt

from . import roster


def get_setting(c, key, default="1"):
    row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(c, key, value):
    c.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    c.commit()


def roster_months(c):
    return [r["month"] for r in c.execute("SELECT month FROM roster_months ORDER BY month")]


def pods_of(c, emp_ids):
    emp_ids = list(emp_ids)
    if not emp_ids:
        return set()
    marks = ",".join("?" * len(emp_ids))
    return {r["pod_id"] for r in c.execute(
        f"SELECT pod_id FROM employees WHERE id IN ({marks}) AND pod_id IS NOT NULL", emp_ids)}


def notify(c, month, message, pod_ids):
    now = dt.datetime.now().isoformat(timespec="seconds")
    for pod_id in pod_ids:
        c.execute("INSERT INTO notifications (created_at, month, pod_id, message) VALUES (?,?,?,?)",
                  (now, month, pod_id, message))


def touch(c, month, reason, pod_ids=None):
    """Call after any change to a month's roster."""
    row = c.execute("SELECT status FROM roster_months WHERE month = ?", (month,)).fetchone()
    if not row:
        return
    c.execute("DELETE FROM approvals WHERE month = ?", (month,))
    if row["status"] != "draft":
        c.execute("UPDATE roster_months SET status = 'draft' WHERE month = ?", (month,))
        if not pod_ids:
            pod_ids = [r["id"] for r in c.execute("SELECT id FROM pods")]
        what = "Published roster" if row["status"] == "published" else "Roster in review"
        notify(c, month, f"{what} for {month} changed: {reason}. It is back to draft.", pod_ids)
    c.commit()


def changed(c, months, reason, emp_ids=()):
    """Something that affects rosters changed (leave, rule, assignment ...).
    If auto-plan is on, re-plan each affected month and keep manual edits.
    Otherwise only mark the month as changed (errors still show in Issues)."""
    auto = get_setting(c, "auto_regen") == "1"
    for month in months:
        if month not in roster_months(c):
            continue
        affected = set(emp_ids)
        if auto:
            affected |= roster.generate(c, month, keep_locked=True)["changed"]
        touch(c, month, reason, pods_of(c, affected))


# ---------- review and approval ----------

def review_state(c, month):
    row = c.execute("SELECT status FROM roster_months WHERE month = ?", (month,)).fetchone()
    state = row["status"] if row else "none"
    approved = {r["pod_id"]: r["approved_at"] for r in c.execute(
        "SELECT * FROM approvals WHERE month = ?", (month,))}
    pods = []
    for p in c.execute("""SELECT p.id, p.name, e.name AS lead,
                          (SELECT COUNT(*) FROM employees WHERE pod_id = p.id) AS size
                          FROM pods p LEFT JOIN employees e ON e.id = p.lead_id ORDER BY p.id"""):
        if p["size"]:
            pods.append({"id": p["id"], "name": p["name"], "lead": p["lead"],
                         "approved_at": approved.get(p["id"])})
    return {"state": state, "pods": pods,
            "all_approved": bool(pods) and all(p["approved_at"] for p in pods)}


def submit(c, month):
    ctx, status, generated = roster.build_status(c, month)
    if not generated:
        raise ValueError("Generate the roster first.")
    errors = [i for i in roster.validate(ctx, status, with_suggestions=False) if i["severity"] == "error"]
    if errors:
        raise ValueError(f"Cannot submit: {len(errors)} error(s) must be fixed first.")
    c.execute("DELETE FROM approvals WHERE month = ?", (month,))
    c.execute("UPDATE roster_months SET status = 'in_review' WHERE month = ?", (month,))
    notify(c, month, f"Roster for {month} is ready for your approval.",
           [p["id"] for p in review_state(c, month)["pods"]])
    c.commit()


def approve(c, month, pod_id):
    if review_state(c, month)["state"] != "in_review":
        raise ValueError("The roster is not in review. Submit it first.")
    c.execute("INSERT OR REPLACE INTO approvals (month, pod_id, approved_at) VALUES (?,?,?)",
              (month, pod_id, dt.datetime.now().isoformat(timespec="seconds")))
    c.commit()


def publish(c, month):
    info = review_state(c, month)
    if info["state"] != "in_review":
        raise ValueError("Submit the roster for review first.")
    if not info["all_approved"]:
        waiting = ", ".join(p["name"] for p in info["pods"] if not p["approved_at"])
        raise ValueError(f"Waiting for pod approval: {waiting}.")
    ctx, status, _ = roster.build_status(c, month)
    if any(i["severity"] == "error" for i in roster.validate(ctx, status, with_suggestions=False)):
        raise ValueError("The roster has errors again. Fix them and submit again.")
    c.execute("UPDATE roster_months SET status = 'published' WHERE month = ?", (month,))
    notify(c, month, f"Roster for {month} is published.", [p["id"] for p in info["pods"]])
    c.commit()

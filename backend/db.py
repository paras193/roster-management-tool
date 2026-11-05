"""Database helpers. SQLite, standard library only."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
DB_PATH = os.environ.get("DB_PATH") or os.path.join(BASE_DIR, "roster.db")

# Calendar codes for a brand's process:
#   ALL_DAYS     = open 365 days (Sundays and public holidays too)
#   NO_HOLIDAYS  = open every day except public holidays
#   NO_SUN_HOL   = closed on Sundays and public holidays
SCHEMA = """
CREATE TABLE IF NOT EXISTS pods (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    lead_id INTEGER REFERENCES employees(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    pod_id INTEGER REFERENCES pods(id) ON DELETE SET NULL,
    weekly_offs INTEGER NOT NULL DEFAULT 1,
    preferred_off INTEGER            -- 0=Mon ... 6=Sun, empty = no preference
);
CREATE TABLE IF NOT EXISTS brands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS processes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS brand_processes (
    id INTEGER PRIMARY KEY,
    brand_id INTEGER NOT NULL REFERENCES brands(id) ON DELETE CASCADE,
    process_id INTEGER NOT NULL REFERENCES processes(id) ON DELETE CASCADE,
    calendar TEXT NOT NULL DEFAULT 'NO_SUN_HOL'
        CHECK (calendar IN ('ALL_DAYS', 'NO_HOLIDAYS', 'NO_SUN_HOL')),
    min_staff INTEGER NOT NULL DEFAULT 1,
    day_overrides TEXT NOT NULL DEFAULT '',   -- example: Sat=2,Mon=2
    UNIQUE (brand_id, process_id)
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    brand_process_id INTEGER NOT NULL REFERENCES brand_processes(id) ON DELETE CASCADE,
    allocation INTEGER NOT NULL DEFAULT 100,  -- percent of the person's time
    UNIQUE (employee_id, brand_process_id)
);
CREATE TABLE IF NOT EXISTS leaves (
    id INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'L' CHECK (type IN ('L', 'CO')),  -- leave / comp-off
    note TEXT NOT NULL DEFAULT '',
    UNIQUE (employee_id, date)
);
CREATE TABLE IF NOT EXISTS holidays (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS roster (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    status TEXT NOT NULL,            -- W, WO, PH
    locked INTEGER NOT NULL DEFAULT 0,  -- 1 = edited by hand, kept when the roster is re-planned
    PRIMARY KEY (employee_id, date)
);
CREATE TABLE IF NOT EXISTS roster_months (
    month TEXT PRIMARY KEY,          -- YYYY-MM
    status TEXT NOT NULL DEFAULT 'draft'   -- draft, in_review, published
);
CREATE TABLE IF NOT EXISTS approvals (
    month TEXT NOT NULL,
    pod_id INTEGER NOT NULL REFERENCES pods(id) ON DELETE CASCADE,
    approved_at TEXT NOT NULL,
    PRIMARY KEY (month, pod_id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    month TEXT NOT NULL,
    pod_id INTEGER REFERENCES pods(id) ON DELETE CASCADE,   -- which pod lead should see it
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    # Databases made by the first version have no 'locked' column
    if "locked" not in [r["name"] for r in conn.execute("PRAGMA table_info(roster)")]:
        conn.execute("ALTER TABLE roster ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return conn

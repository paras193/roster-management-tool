"""Sample data: 32 employees, 4 pods, 25 brands.

20 brands (80%) have one dedicated employee. 5 brands have 2-3 employees.
Run `python -m backend.seed` to wipe the database and load this data again.
"""
import os

from . import db as dbm
from . import roster

PROCESSES = ["CS", "ACC", "Retention", "NDR"]

# pod name -> (lead, [5 single-brand employees], [2 shared-brand employees])
PODS = {
    "Alpha": ("Aarav Mehta", ["Rohan Sharma", "Priya Nair", "Vikram Singh", "Sneha Iyer", "Arjun Reddy"],
              ["Neha Gupta", "Karan Malhotra"]),
    "Bravo": ("Diya Kapoor", ["Ananya Joshi", "Rahul Verma", "Pooja Desai", "Siddharth Rao", "Kavya Menon"],
              ["Aditya Bose", "Tanvi Shah"]),
    "Charlie": ("Kabir Nair", ["Harsh Patel", "Simran Kaur", "Manish Yadav", "Riya Sen", "Nikhil Jain"],
                ["Shruti Das", "Varun Khanna"]),
    "Delta": ("Ishita Rao", ["Amit Chawla", "Lakshmi Pillai", "Yash Agarwal", "Divya Bhat", "Tarun Saxena"],
              ["Anjali Mishra", "Rishi Kulkarni"]),
}

# Single-resource brands, five per pod: (brand, [(process, calendar)]).
# The one employee handles every process of the brand.
SINGLE = {
    "Alpha": [
        ("Bloom Skincare", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
        ("TrailEdge Gear", [("CS", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL"), ("Retention", "NO_SUN_HOL")]),
        ("Pawsome Pets", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
        # 7-day support with one person: this brand always needs a backup (on purpose)
        ("Kiddo Toys", [("CS", "NO_HOLIDAYS"), ("NDR", "NO_SUN_HOL")]),
        ("Silk & Stone", [("CS", "NO_SUN_HOL"), ("Retention", "NO_SUN_HOL")]),
    ],
    "Bravo": [
        ("Brew Basket", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
        ("Zest Snacks", [("CS", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL")]),
        ("Luxe Candles", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL"), ("Retention", "NO_SUN_HOL")]),
        ("GreenLeaf Organics", [("CS", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL")]),
        ("Denim Lab", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
    ],
    "Charlie": [
        ("Spice Route", [("CS", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL")]),
        ("Aura Jewels", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
        ("FitPulse", [("CS", "NO_SUN_HOL"), ("Retention", "NO_SUN_HOL")]),
        ("Cozy Nest", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL")]),
        ("Mighty Mugs", [("CS", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL")]),
    ],
    "Delta": [
        ("Urban Kicks", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
        ("Velvet Home", [("CS", "NO_SUN_HOL"), ("Retention", "NO_SUN_HOL")]),
        ("Pure Pharma", [("CS", "NO_SUN_HOL"), ("NDR", "NO_SUN_HOL")]),
        ("Sunny Seeds", [("CS", "NO_SUN_HOL"), ("ACC", "NO_SUN_HOL")]),
        ("Artisan Tea", [("CS", "NO_SUN_HOL"), ("Retention", "NO_SUN_HOL")]),
    ],
}

# Brands with 2-3 employees.
# brand -> {process: (calendar, min_staff, day_overrides)}
SHARED_BRANDS = {
    "Nova Wear": {"CS": ("ALL_DAYS", 1, "Sat=2"), "ACC": ("NO_SUN_HOL", 1, ""),
                  "NDR": ("NO_HOLIDAYS", 1, "")},
    "Urban Brew": {"CS": ("ALL_DAYS", 1, ""), "NDR": ("NO_SUN_HOL", 1, "")},
    "Glow Cosmetics": {"CS": ("NO_HOLIDAYS", 1, ""), "ACC": ("NO_SUN_HOL", 1, "")},
    "FitFuel": {"ACC": ("NO_SUN_HOL", 1, ""), "Retention": ("NO_SUN_HOL", 1, "")},
    "HomeNest": {"CS": ("ALL_DAYS", 1, "")},
}

# (employee, brand, process, % of time). Some people work on two brands.
SHARED_ASSIGNMENTS = [
    ("Neha Gupta", "Nova Wear", "CS", 50), ("Neha Gupta", "Nova Wear", "NDR", 50),
    ("Karan Malhotra", "Nova Wear", "CS", 50), ("Karan Malhotra", "Nova Wear", "ACC", 50),
    ("Aditya Bose", "Nova Wear", "NDR", 50), ("Aditya Bose", "HomeNest", "CS", 50),
    ("Tanvi Shah", "Urban Brew", "CS", 100),
    ("Shruti Das", "Urban Brew", "CS", 50), ("Shruti Das", "FitFuel", "ACC", 50),
    ("Varun Khanna", "Urban Brew", "NDR", 50), ("Varun Khanna", "HomeNest", "CS", 50),
    ("Anjali Mishra", "Glow Cosmetics", "CS", 40), ("Anjali Mishra", "Glow Cosmetics", "ACC", 30),
    ("Anjali Mishra", "FitFuel", "Retention", 30),
    ("Rishi Kulkarni", "Glow Cosmetics", "CS", 40), ("Rishi Kulkarni", "Glow Cosmetics", "ACC", 30),
]

PREFERRED_OFF = {  # 0=Mon ... 6=Sun
    "Neha Gupta": 4, "Karan Malhotra": 2, "Aditya Bose": 0, "Tanvi Shah": 3,
    "Shruti Das": 6, "Varun Khanna": 0, "Anjali Mishra": 1, "Rishi Kulkarni": 2,
}

# Sample holidays (approximate dates, change them in the app)
HOLIDAYS = [("2026-10-20", "Dussehra"), ("2026-11-08", "Diwali"), ("2026-11-09", "Govardhan Puja"),
            ("2026-11-24", "Guru Nanak Jayanti"), ("2026-12-25", "Christmas")]

# (employee, first day, last day, type, note). Kept on people who share work with colleagues.
LEAVES = [
    ("Neha Gupta", "2026-11-12", "2026-11-13", "L", "Family trip"),
    ("Tanvi Shah", "2026-11-23", "2026-11-24", "L", "Personal"),
    ("Aditya Bose", "2026-12-24", "2026-12-25", "L", "Year end"),
    ("Aarav Mehta", "2026-11-25", "2026-11-26", "L", "Pod lead leave"),
    ("Rishi Kulkarni", "2026-11-20", "2026-11-20", "CO", "Comp-off for working on a holiday"),
]


def seed(conn):
    q = conn.execute
    for p in PROCESSES:
        q("INSERT INTO processes (name) VALUES (?)", (p,))
    process_id = {r["name"]: r["id"] for r in q("SELECT * FROM processes")}

    emp_id, pod_id = {}, {}
    for pod, (lead, singles, shared) in PODS.items():
        pod_id[pod] = q("INSERT INTO pods (name) VALUES (?)", (pod,)).lastrowid
        for name in [lead] + singles + shared:
            emp_id[name] = q("INSERT INTO employees (name, pod_id, preferred_off) VALUES (?,?,?)",
                             (name, pod_id[pod], PREFERRED_OFF.get(name))).lastrowid
        q("UPDATE pods SET lead_id = ? WHERE id = ?", (emp_id[lead], pod_id[pod]))

    bp_id = {}

    def add_bp(brand, proc, cal, min_staff=1, overrides=""):
        q("INSERT OR IGNORE INTO brands (name) VALUES (?)", (brand,))
        brand_id = q("SELECT id FROM brands WHERE name = ?", (brand,)).fetchone()["id"]
        bp_id[(brand, proc)] = q(
            "INSERT INTO brand_processes (brand_id, process_id, calendar, min_staff, day_overrides) "
            "VALUES (?,?,?,?,?)", (brand_id, process_id[proc], cal, min_staff, overrides)).lastrowid

    for pod, (lead, singles, shared) in PODS.items():
        for person, (brand, procs) in zip(singles, SINGLE[pod]):
            # the one employee's 100% time is split across the brand's processes
            for i, (proc, cal) in enumerate(procs):
                add_bp(brand, proc, cal)
                pct = 100 // len(procs) + (100 % len(procs) if i == 0 else 0)
                q("INSERT INTO assignments (employee_id, brand_process_id, allocation) VALUES (?,?,?)",
                  (emp_id[person], bp_id[(brand, proc)], pct))

    for brand, procs in SHARED_BRANDS.items():
        for proc, (cal, min_staff, overrides) in procs.items():
            add_bp(brand, proc, cal, min_staff, overrides)
    for person, brand, proc, pct in SHARED_ASSIGNMENTS:
        q("INSERT INTO assignments (employee_id, brand_process_id, allocation) VALUES (?,?,?)",
          (emp_id[person], bp_id[(brand, proc)], pct))

    for day, name in HOLIDAYS:
        q("INSERT INTO holidays (date, name) VALUES (?,?)", (day, name))
    for person, first, last, kind, note in LEAVES:
        start = roster.dt.date.fromisoformat(first)
        end = roster.dt.date.fromisoformat(last)
        for n in range((end - start).days + 1):
            q("INSERT INTO leaves (employee_id, date, type, note) VALUES (?,?,?,?)",
              (emp_id[person], (start + roster.dt.timedelta(days=n)).isoformat(), kind, note))
    conn.commit()
    for month in ("2026-11", "2026-12"):
        roster.generate(conn, month)


def reset():
    if os.path.exists(dbm.DB_PATH):
        os.remove(dbm.DB_PATH)
    conn = dbm.init_db()
    seed(conn)
    return conn


if __name__ == "__main__":
    reset()
    print("Sample data loaded into", dbm.DB_PATH)

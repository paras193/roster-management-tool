"""Backend tests. Run from the project root with:
  python -m unittest discover -s backend/tests -t .
Uses a throwaway database file, never your real one."""
import os
import sys
import tempfile
import unittest

os.environ["ROSTER_NO_DOTENV"] = "1"  # tests must not pick up a real .env and call a real AI service
TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")
os.environ.pop("LLM_API_KEY", None)  # tests must never call a real AI service
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # project root

import app as flask_app  # noqa: E402
from backend import db as dbm  # noqa: E402,F401
from backend import explain, roster, seed, workflow  # noqa: E402

MONTH = "2026-11"


def gaps(conn, brand=None):
    ctx, status, _ = roster.build_status(conn, MONTH)
    found = [i for i in roster.validate(ctx, status, with_suggestions=False)
             if i["type"] == "coverage_gap"]
    return [i for i in found if brand is None or i["message"].startswith(brand)]


def emp_id(conn, name):
    return conn.execute("SELECT id FROM employees WHERE name = ?", (name,)).fetchone()[0]


class RosterTests(unittest.TestCase):
    def setUp(self):
        self.conn = seed.reset()

    def tearDown(self):
        self.conn.close()  # Windows cannot delete a file that is still open

    def test_sample_size(self):
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0], 30)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM brands").fetchone()[0], 25)

    def test_only_the_known_single_resource_brand_has_gaps(self):
        names = {i["message"].split(" / ")[0] for i in gaps(self.conn)}
        self.assertEqual(names, {"Kiddo Toys"})

    def test_every_full_week_has_a_weekly_off(self):
        ctx, status, _ = roster.build_status(self.conn, MONTH)
        errors = [i for i in roster.validate(ctx, status, with_suggestions=False)
                  if i["type"] == "no_weekly_off"]
        self.assertEqual(errors, [])

    def test_leave_on_single_resource_brand_creates_gap(self):
        self.assertEqual(gaps(self.conn, "Bloom Skincare"), [])
        self.conn.execute("INSERT INTO leaves (employee_id, date, type) VALUES (?, '2026-11-18', 'L')",
                          (emp_id(self.conn, "Rohan Sharma"),))
        self.conn.commit()
        found = gaps(self.conn, "Bloom Skincare")  # Rohan runs CS and ACC alone
        self.assertEqual(len(found), 2)
        self.assertIn("nobody is working", found[0]["message"])

    def test_leave_on_shared_brand_is_absorbed(self):
        # Neha has leave on 12-13 Nov but Karan covers Nova Wear CS
        self.assertEqual(gaps(self.conn, "Nova Wear"), [])

    def test_backup_suggestion_is_offered_for_gap(self):
        ctx, status, _ = roster.build_status(self.conn, MONTH)
        gap = next(i for i in roster.validate(ctx, status) if i["type"] == "coverage_gap")
        self.assertTrue(any(s["type"] == "add_backup" for s in gap["suggestions"]))

    def test_manual_edit_is_validated(self):
        emp = emp_id(self.conn, "Vikram Singh")  # runs Pawsome Pets alone
        self.conn.execute("UPDATE roster SET status = 'WO' WHERE employee_id = ? AND date = '2026-11-10'", (emp,))
        self.conn.commit()
        self.assertEqual(len(gaps(self.conn, "Pawsome Pets")), 2)  # CS and ACC


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.conn = seed.reset()

    def tearDown(self):
        self.conn.close()

    def test_compare_scores_every_strategy_without_saving(self):
        before = self.conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0]
        plans = {p["strategy"]: p for p in roster.compare(self.conn, MONTH)}
        self.assertEqual(set(plans), set(roster.STRATEGIES))
        self.assertLessEqual(plans["preference"]["unmet_pref_weeks"], plans["coverage"]["unmet_pref_weeks"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0], before)

    def test_fair_plan_spreads_weekend_offs_no_worse_than_coverage_plan(self):
        plans = {p["strategy"]: p for p in roster.compare(self.conn, MONTH)}
        self.assertLessEqual(plans["fair"]["weekend_spread"], plans["coverage"]["weekend_spread"])

    def test_manual_edits_survive_replanning(self):
        emp = emp_id(self.conn, "Tanvi Shah")
        self.conn.execute("UPDATE roster SET status = 'WO', locked = 1 "
                          "WHERE employee_id = ? AND date = '2026-11-04'", (emp,))
        self.conn.commit()
        roster.generate(self.conn, MONTH, keep_locked=True)
        row = self.conn.execute("SELECT status, locked FROM roster WHERE employee_id = ? "
                                "AND date = '2026-11-04'", (emp,)).fetchone()
        self.assertEqual((row["status"], row["locked"]), ("WO", 1))
        roster.generate(self.conn, MONTH)  # a fresh plan clears the lock
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM roster WHERE locked = 1").fetchone()[0], 0)

    def test_csv_export_has_a_row_per_employee_and_coverage(self):
        lines = roster.export_csv(self.conn, MONTH, "company", None).splitlines()
        self.assertTrue(lines[0].startswith("Employee,Pod,Assignments,1 Sun"))
        self.assertEqual(sum(1 for l in lines if ",Alpha," in l or ",Bravo," in l
                             or ",Charlie," in l or ",Delta," in l), 32)
        self.assertTrue(any(l.startswith("Coverage") for l in lines))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.conn = seed.reset()
        # Drop the deliberately broken brand so the roster can be approved
        self.conn.execute("DELETE FROM brands WHERE name = 'Kiddo Toys'")
        self.conn.commit()
        roster.generate(self.conn, MONTH)

    def tearDown(self):
        self.conn.close()

    def approve_all(self):
        workflow.submit(self.conn, MONTH)
        for pod in workflow.review_state(self.conn, MONTH)["pods"]:
            workflow.approve(self.conn, MONTH, pod["id"])

    def test_submit_is_blocked_while_errors_exist(self):
        self.conn.execute("INSERT INTO leaves (employee_id, date) VALUES (?, '2026-11-18')",
                          (emp_id(self.conn, "Rohan Sharma"),))
        self.conn.commit()
        with self.assertRaises(ValueError):
            workflow.submit(self.conn, MONTH)

    def test_publish_needs_every_pod_to_approve(self):
        workflow.submit(self.conn, MONTH)
        first = workflow.review_state(self.conn, MONTH)["pods"][0]["id"]
        workflow.approve(self.conn, MONTH, first)
        with self.assertRaises(ValueError):
            workflow.publish(self.conn, MONTH)
        self.approve_all()
        workflow.publish(self.conn, MONTH)
        self.assertEqual(workflow.review_state(self.conn, MONTH)["state"], "published")

    def test_change_to_published_roster_notifies_pod_and_resets_to_draft(self):
        self.approve_all()
        workflow.publish(self.conn, MONTH)
        tanvi = emp_id(self.conn, "Tanvi Shah")
        pod = self.conn.execute("SELECT pod_id FROM employees WHERE id = ?", (tanvi,)).fetchone()[0]
        workflow.touch(self.conn, MONTH, "test change", {pod})
        self.assertEqual(workflow.review_state(self.conn, MONTH)["state"], "draft")
        notes = self.conn.execute("SELECT * FROM notifications WHERE message LIKE '%test change%'").fetchall()
        self.assertEqual([n["pod_id"] for n in notes], [pod])

    def test_auto_replan_after_leave_keeps_manual_edits_and_fills_new_leave(self):
        emp = emp_id(self.conn, "Tanvi Shah")
        self.conn.execute("UPDATE roster SET status = 'WO', locked = 1 "
                          "WHERE employee_id = ? AND date = '2026-11-04'", (emp,))
        neha = emp_id(self.conn, "Neha Gupta")
        self.conn.execute("INSERT INTO leaves (employee_id, date) VALUES (?, '2026-11-03')", (neha,))
        self.conn.commit()
        workflow.changed(self.conn, [MONTH], "leave added", [neha])
        status = self.conn.execute("SELECT status FROM roster WHERE employee_id = ? "
                                   "AND date = '2026-11-04'", (emp,)).fetchone()[0]
        self.assertEqual(status, "WO")
        self.assertEqual(gaps(self.conn), [])  # the others cover Neha's leave

    def test_auto_replan_can_be_switched_off(self):
        workflow.set_setting(self.conn, "auto_regen", "0")
        before = self.conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0]
        self.conn.execute("DELETE FROM roster WHERE employee_id = ?", (emp_id(self.conn, "Tanvi Shah"),))
        self.conn.commit()
        workflow.changed(self.conn, [MONTH], "something", [])
        self.assertLess(self.conn.execute("SELECT COUNT(*) FROM roster").fetchone()[0], before)


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.conn = seed.reset()

    def tearDown(self):
        self.conn.close()

    def test_rule_based_explanation_without_api_key(self):
        ctx, status, _ = roster.build_status(self.conn, MONTH)
        gap = next(i for i in roster.validate(ctx, status) if i["type"] == "coverage_gap")
        result = explain.explain(ctx, status, gap)
        self.assertEqual(result["source"], "rules")
        self.assertIn("Kiddo Toys / CS", result["text"])
        self.assertIn("weekly off", result["text"])

    def test_ai_reply_is_used_when_the_api_answers(self):
        ctx, status, _ = roster.build_status(self.conn, MONTH)
        gap = next(i for i in roster.validate(ctx, status) if i["type"] == "coverage_gap")
        original = explain.ask_llm
        explain.ask_llm = lambda lines: "AI says: add a backup."
        try:
            self.assertEqual(explain.explain(ctx, status, gap),
                             {"text": "AI says: add a backup.", "source": "ai"})
        finally:
            explain.ask_llm = original


class LlmClientTests(unittest.TestCase):
    """explain.ask_llm talks to any OpenAI-compatible server (OpenRouter, OpenAI, local)."""

    def test_request_shape_and_fallback(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = {}

        class Fake(BaseHTTPRequestHandler):
            def do_POST(self):
                seen["path"] = self.path
                seen["auth"] = self.headers["Authorization"]
                seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                out = json.dumps({"choices": [{"message": {"content": " Fake answer. "}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        saved = {k: os.environ.get(k) for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}
        try:
            os.environ.update(LLM_API_KEY="k", LLM_MODEL="some/model",
                              LLM_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1")
            self.assertEqual(explain.ask_llm(["fact one"]), "Fake answer.")
            self.assertEqual(seen["path"], "/v1/chat/completions")
            self.assertEqual(seen["auth"], "Bearer k")
            self.assertEqual(seen["body"]["model"], "some/model")
            self.assertEqual(seen["body"]["messages"][-1]["content"], "fact one")
            os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"  # nothing listens here
            self.assertIsNone(explain.ask_llm(["x"]))  # caller falls back to rule-based text
        finally:
            server.shutdown()
            server.server_close()
            for key, val in saved.items():
                os.environ.pop(key, None) if val is None else os.environ.__setitem__(key, val)


class ApiTests(unittest.TestCase):
    def setUp(self):
        seed.reset().close()
        self.client = flask_app.app.test_client()

    def test_page_and_data_endpoints(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(len(self.client.get("/api/employees").json), 32)

    def test_generate_with_each_strategy(self):
        for key in roster.STRATEGIES:
            res = self.client.post("/api/roster/generate", json={"month": MONTH, "strategy": key})
            self.assertEqual(res.status_code, 200, key)
        bad = self.client.post("/api/roster/generate", json={"month": MONTH, "strategy": "nope"})
        self.assertEqual(bad.status_code, 400)

    def test_compare_and_export_endpoints(self):
        self.assertEqual(len(self.client.get(f"/api/compare?month={MONTH}").json["plans"]), 4)
        res = self.client.get(f"/api/roster/export?month={MONTH}&view=company")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.content_type)
        self.assertIn("attachment", res.headers["Content-Disposition"])

    def test_review_flow_blocks_on_errors_then_adding_backup_clears_them(self):
        self.assertEqual(self.client.post("/api/roster/submit", json={"month": MONTH}).status_code, 400)
        gap = next(i for i in self.client.get(f"/api/issues?month={MONTH}").json["issues"]
                   if i["type"] == "coverage_gap")
        backup = next(s for s in gap["suggestions"] if s["type"] == "add_backup")
        self.client.post("/api/suggestion/apply", json=backup)  # auto re-plan is on by default
        self.assertEqual(self.client.get(f"/api/issues?month={MONTH}").json["errors"], 0)
        self.assertEqual(self.client.post("/api/roster/submit", json={"month": MONTH}).status_code, 200)

    def test_adding_a_leave_replans_and_notifies_when_in_review(self):
        gap = next(i for i in self.client.get(f"/api/issues?month={MONTH}").json["issues"]
                   if i["type"] == "coverage_gap")
        self.client.post("/api/suggestion/apply",
                         json=next(s for s in gap["suggestions"] if s["type"] == "add_backup"))
        self.client.post("/api/roster/submit", json={"month": MONTH})
        self.client.post("/api/leaves", json={"employee_id": 3, "date": "2026-11-18", "type": "L"})
        self.assertEqual(self.client.get(f"/api/review?month={MONTH}").json["state"], "draft")
        self.assertTrue(any("changed" in n["message"] for n in self.client.get("/api/notifications").json))

    def test_explain_endpoint_and_settings(self):
        gap = next(i for i in self.client.get(f"/api/issues?month={MONTH}").json["issues"]
                   if i["type"] == "coverage_gap")
        res = self.client.post("/api/issues/explain", json={"month": MONTH, "issue": gap})
        self.assertEqual(res.json["source"], "rules")
        self.client.put("/api/settings", json={"auto_regen": False})
        self.assertFalse(self.client.get("/api/settings").json["auto_regen"])


if __name__ == "__main__":
    unittest.main()

# Smart Roster Manager

A web tool that helps an operations manager plan a monthly roster for many employees across brands, processes and pods. It builds the roster, finds coverage gaps, helps fix them, and runs the review and approval steps.

- Backend: Python, Flask, SQLite
- Frontend: plain HTML, CSS and JavaScript (no build step)

## What it does

**Core**

- Keeps a database of employees, pods, pod leads, brands, processes and who works on what.
- Each brand process has its own calendar (365 days, every day except holidays, or closed Sundays and holidays) and a minimum staff count. You can raise the minimum for specific days (example `Sat=2`).
- Stores leaves, comp-offs and public holidays.
- Generates the roster for any month.
- Shows the roster by company, pod, brand, process or employee, plus a coverage table (people working / people needed).
- Lists errors and warnings. Click a cell to edit it, and the roster is checked again straight away.
- Suggests fixes for gaps: move a weekly off to another day, or add a backup person.

**Optional features (all built)**

| Feature | Where to find it |
| --- | --- |
| Fair distribution of weekly offs | The "Fair rotation" plan spreads weekend offs; the roster has a "Wknd offs" column. |
| Preference-based scheduling | Each employee has a preferred off day. Unmet ones show as warnings. |
| Automatic re-planning after changes | Tick "Auto re-plan on changes" in the header. Adding a leave, holiday, assignment or rule re-plans the month and keeps cells you edited by hand (marked with a dark border). |
| Backup resource recommendations | Issues tab: "Add X as backup". |
| Comparison of alternative plans | "Compare plans" tab scores four plans side by side. Click "Use this plan" to save one. |
| Approval workflow | "Review & notifications" tab: manager submits, each pod lead approves, manager publishes. |
| Notifications | If a roster in review or published changes, the roster goes back to draft and the affected pods get a notification. |
| Export | "Export CSV" in the header. Opens in Excel and imports into Google Sheets. |
| AI explanations | "Explain" button on each issue. Uses any OpenAI-compatible API (OpenRouter by default). Falls back to built-in text when no key is set. |
| New brands, pods, processes without code | "Team & Rules" tab. |

## Run it on your machine

You need Python 3.11 or newer. This project uses [uv](https://docs.astral.sh/uv/).

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt   # Windows
# Mac/Linux: uv pip install --python .venv/bin/python -r requirements.txt

.venv/Scripts/python.exe app.py        # Windows (Mac/Linux: .venv/bin/python app.py)
```

Open <http://localhost:5000>. On the first start, the app creates `roster.db` and loads the sample data (Nov and Dec 2026 rosters are already generated).

To reset the sample data: `python -m backend.seed`.

## Project layout

```text
app.py               Flask app: routes, .env loader (the only Python file in the root)
backend/
  db.py              SQLite tables and connection
  seed.py            Sample data
  roster.py          Generate, plans, validate, suggest fixes, views, CSV
  workflow.py        Auto re-plan, review and approval, notifications
  explain.py         Conflict explanations (rules, or any OpenAI-compatible API)
  tests/             Backend tests
static/              The web page (index.html, app.js, style.css)
frontend-tests/      Page tests (jsdom)
.github/workflows/   CI: one workflow for backend, one for frontend
```

`backend/` is a Python package. `app.py` imports it with `from backend import ...`, so always start the app from the project root.

### AI explanations (optional)

Copy `.env.example` to `.env` (it is git-ignored; the app reads it on start, and real environment variables win). Set `LLM_API_KEY` to an [OpenRouter](https://openrouter.ai) key (or any OpenAI-compatible provider, with its `LLM_BASE_URL` and `LLM_MODEL`). Without a key the Explain button still works with rule-based text. Only the fictional names and rules of this app are sent.

**How it works**

1. Open the **Issues** tab and click **Explain** under any issue.
2. The server rebuilds the facts for that issue from the database: the process rules, who is assigned and their status that day, and the possible fixes. It does not trust the text sent by the browser.
3. `backend/explain.py` posts those facts to `<LLM_BASE_URL>/chat/completions` (OpenRouter by default) with your key, and the answer shows under the issue tagged "(AI explanation)".
4. If there is no key, or the call fails, you get built-in text tagged "(rule-based explanation)", plus a note with the reason, for example `AI call failed (HTTP 401 from openrouter.ai)`.

**Example (real run, OpenRouter, `openai/gpt-4o-mini`, November 2026 sample data)**

The issue in the list:

```text
ERROR: Kiddo Toys / CS on Sun 15 Nov: nobody is working, 1 needed. Not available: Sneha Iyer (WO).
Possible fix: Add Rishi Kulkarni (Delta, 30% spare) as backup for Kiddo Toys / CS
```

After clicking **Explain**:

```text
On Sunday, November 15, 2026, Kiddo Toys needs one staff member, but Sneha Iyer is off.
The best solution is to add Rishi Kulkarni as a backup since he has 30% availability.
This will ensure that there is enough coverage for customer service that day.   (AI explanation)
```

The same call works from the command line (the app must be running):

```bash
curl -s -X POST http://localhost:5000/api/issues/explain \
  -H "Content-Type: application/json" \
  -d '{"month": "2026-11", "issue": {"type": "coverage_gap", "brand_process_id": 8, "date": "2026-11-15"}}'
# {"source": "ai", "text": "On Sunday, November 15, 2026, Kiddo Toys needs ..."}
```

Take `brand_process_id` and `date` from `GET /api/issues?month=2026-11`. The wording changes from run to run, and a small model can be loose. For example, on a preference warning it once suggested a day off "every four weeks", which is not the rule. Use a stronger `LLM_MODEL` if you need better answers.

## Tests and CI

```bash
python -m unittest discover -s backend/tests -t .   # backend (25 tests), run from the project root
npm ci && npm test                         # frontend (8 tests, jsdom). npm is only for tests.
```

GitHub Actions runs them in two separate workflows:

- `.github/workflows/backend.yml`: Python 3.11 and 3.13, runs when `app.py`, `backend/` or `requirements.txt` change.
- `.github/workflows/frontend.yml`: Node 22, JS syntax check and page tests, runs when `static/` or the frontend tests change.

## Deploy

The `Procfile` runs `gunicorn app:app`. On Render, Railway or similar: connect the repo, build command `pip install -r requirements.txt`, start command `gunicorn app:app`. Set `LLM_API_KEY` there if you want AI explanations. SQLite is stored in a file. On free hosts that file may reset on restart, which just reloads the sample data.

## Sample data

- 32 employees in 4 pods (Alpha, Bravo, Charlie, Delta), each with a pod lead.
- 25 brands, 4 processes (CS, ACC, Retention, NDR).
- 20 brands (80%) have one employee who handles every process.
- 5 brands have 2 or 3 employees. Some share one process (example: two people on CS), some have different processes. Some employees work on two brands.
- Sample holidays, leaves and a comp-off for Nov and Dec 2026.
- **Kiddo Toys is on purpose a problem case:** one person, support open 7 days. The roster shows a gap every week. Use the suggested backup to fix it.

## Assumptions

- Every employee gets 1 weekly off in each week (Mon to Sun). This can be changed per employee. A week cut short by the month edge needs a weekly off only if it has 5 or more days in the month.
- A person is off automatically on days when all of their processes are closed (Sundays, holidays). A Sunday closure counts as their weekly off. A holiday on a Sunday also counts as the weekly off.
- People with no brand (pod leads) follow office days: closed on Sundays and holidays.
- Leave and comp-off always win over the stored roster. Comp-offs are entered by hand.
- "% of time" on an assignment is a person's share for that brand process. A person over 100% in total is an error.
- Fair rotation looks at weekend offs inside one month only, not across months.
- Re-planning keeps hand-edited cells. A fresh "Generate" removes them.
- There is no login. Anyone can click "Approve as pod lead". This is a demo for one operations manager.
- Holiday dates in the sample are approximate and can be edited in the app.

## Tell us how you think

**1. How did you model employees, brands, processes, pods and their relationships?**
Small tables. `employees` belong to a `pod`; a pod has a lead (an employee). A `brand_process` is one process run for one brand, and it holds the rules: calendar, minimum staff, day overrides. `assignments` link employees to brand processes with a % of time, so a person can work on many brands and a brand process can have many people. Leaves, holidays and the roster are separate tables.

**2. How does the system generate a roster and check it?**
For each employee it first fills the fixed days: leave, comp-off, hand-edited cells, closed days. Then for each week it picks the weekly off day with the lowest "cost". The cost is huge if the day would leave a process short of its minimum staff, small if there is spare cover, and adjusted by the plan type (preference bonus, weekend fairness). People with the fewest colleagues are handled first because they have the least room. A separate validator then counts, for every process and every open day, how many people are working versus needed. The validator is also used after manual edits.

**3. Single-resource brands versus brands with multiple employees?**
Same logic for both. A single-resource brand has only one person who can cover it, so the cost of taking any open day off is very high. The generator uses a closed day (like Sunday) when there is one. If none exists, the gap is reported instead of hidden. Multi-employee brands spread the offs so someone is always present.

**4. What if one person runs all processes for a brand and is unavailable on a required day?**
A coverage error appears for each affected process and day: "nobody is working, 1 needed. Not available: Name (L)". The Issues tab then offers fixes: add a free colleague as backup, or move a weekly off. The Explain button describes it in plain English. Submitting for review is blocked until the error is gone.

**5. How are conflicting requirements or insufficient staffing handled?**
Rules are split into errors (mandatory: coverage, weekly off, over-allocation) and warnings (preferences). Coverage always beats preferences. When the rules cannot all be met, the roster is still produced, but every unmet requirement is listed with the reason, never silently dropped.

**6. What is automated and what is human controlled?**
Automated: filling the roster, checking rules, re-planning after a change (if switched on), finding fixes, notifying pods. Human: approving leaves, applying a fix, manual edits, pod lead approval, and publishing. A backup person changes someone's work, so the manager must click Apply. Auto re-plan can be switched off and never touches hand-edited cells.

**7. If the team and brand count doubled?**
The generator and validator loop over every employee and day, which is fine at this size. I would move to PostgreSQL, add indexes, load only the data needed per view, run generation as a background job, and replace the greedy search with a constraint solver (like OR-Tools) so the whole month is solved together. I would also add login and roles.

**8. One more week?**
Real login and roles for the approval flow. Fairness across months. Excel (.xlsx) export and email or chat delivery for notifications. A "why did this plan change" diff when the roster is re-planned.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask routes (API and page), `.env` loader |
| `backend/roster.py` | Generate, plans, validate, suggest fixes, views, CSV |
| `backend/workflow.py` | Auto re-plan, review and approval, notifications |
| `backend/explain.py` | Conflict explanations (rules, or any OpenAI-compatible API) |
| `backend/db.py` | SQLite tables |
| `backend/seed.py` | Sample data |
| `backend/tests/` | Backend tests |
| `static/` | The web page (`index.html`, `app.js`, `style.css`) |
| `frontend-tests/` | Page tests (jsdom) |
| `.github/workflows/` | CI: one workflow for backend, one for frontend |

// Frontend tests: load the real page (index.html + style.css + app.js) in jsdom
// with a fake API, then click around like a user.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..", "static");
const read = (f) => fs.readFileSync(path.join(root, f), "utf8");

const NAME_WITH_HTML = '<img src=x onerror="window.hacked=1">';

// Fake answers for every endpoint the page calls
const fixtures = {
  "/api/pods": [{ id: 1, name: "Alpha", lead_id: 1 }],
  "/api/employees": [
    { id: 1, name: "Aarav", pod_id: 1, weekly_offs: 1, preferred_off: null },
    { id: 2, name: NAME_WITH_HTML, pod_id: 1, weekly_offs: 1, preferred_off: 2 },
  ],
  "/api/brands": [{ id: 1, name: "Bloom" }],
  "/api/processes": [{ id: 1, name: "CS" }],
  "/api/brand_processes": [{ id: 1, brand_id: 1, process_id: 1, calendar: "NO_SUN_HOL", min_staff: 1, day_overrides: "" }],
  "/api/assignments": [{ id: 1, employee_id: 2, brand_process_id: 1, allocation: 100 }],
  "/api/leaves": [],
  "/api/holidays": [],
  "/api/months": [{ month: "2026-11", status: "draft" }],
  "/api/settings": { auto_regen: true },
  "/api/issues": {
    generated: true, errors: 1, warnings: 0,
    issues: [{
      severity: "error", type: "coverage_gap", date: "2026-11-02", brand_process_id: 1,
      message: "Bloom / CS on Mon 02 Nov: nobody is working, 1 needed.",
      suggestions: [{ type: "add_backup", employee_id: 1, brand_process_id: 1, text: "Add Aarav as backup" }],
    }],
  },
  "/api/issues/explain": { text: "Rohan is on leave and nobody else knows this brand.", source: "rules" },
  "/api/roster": {
    month: "2026-11", generated: true, state: "draft",
    dates: [
      { date: "2026-11-02", day: 2, dow: "Mon", holiday: "" },
      { date: "2026-11-03", day: 3, dow: "Tue", holiday: "" },
    ],
    rows: [
      { id: 1, name: "Aarav", pod: "Alpha", lead: true, assign: "No brand", cells: ["W", "WO"], locked: [false, true],
        weekend_offs: 0, counts: { W: 1, WO: 1, PH: 0, L: 0, CO: 0 } },
      { id: 2, name: NAME_WITH_HTML, pod: "Alpha", lead: false, assign: "Bloom / CS", cells: ["L", "W"], locked: [false, false],
        weekend_offs: 0, counts: { W: 1, WO: 0, PH: 0, L: 1, CO: 0 } },
    ],
    coverage: [{
      id: 1, label: "Bloom / CS", calendar: "Closed Sundays and holidays",
      cells: [{ need: 1, have: 0, who: "" }, { need: 1, have: 1, who: "x" }],
    }],
  },
  "/api/roster/cell": { ok: true, errors: 0, related: [] },
  "/api/roster/generate": { errors: 0, warnings: 0 },
  "/api/roster/approve": { ok: true },
  "/api/suggestion/apply": { ok: true },
  "/api/compare": { plans: [
    { strategy: "balanced", label: "Balanced (default)", errors: 0, unmet_pref_weeks: 3, avg_spare: 0.8, weekend_spread: 1 },
    { strategy: "fair", label: "Fair rotation of weekend offs", errors: 0, unmet_pref_weeks: 5, avg_spare: 0.7, weekend_spread: 0 },
  ] },
  "/api/review": {
    state: "in_review", all_approved: false, errors: 0,
    pods: [{ id: 1, name: "Alpha", lead: "Aarav", approved_at: null }],
  },
  "/api/notifications": [{ id: 1, created_at: "2026-11-05T10:00:00", month: "2026-11", pod: "Alpha", message: "Roster changed" }],
};

async function openPage() {
  const html = read("index.html").replace(/<script[^>]*><\/script>/, "");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/", pretendToBeVisual: true });
  const { window } = dom;
  const style = window.document.createElement("style");
  style.textContent = read("style.css");
  window.document.head.append(style);

  const calls = [];
  window.fetch = async (url, opts = {}) => {
    const route = url.split("?")[0];
    calls.push({ method: opts.method || "GET", route, body: opts.body ? JSON.parse(opts.body) : null });
    if (!(route in fixtures)) return { ok: false, statusText: "Not found", json: async () => ({ error: "no fixture for " + route }) };
    return { ok: true, json: async () => fixtures[route] };
  };
  window.eval(read("app.js"));
  const wait = async (check, label) => {
    for (let i = 0; i < 100; i++) {
      if (check()) return;
      await new Promise((r) => setTimeout(r, 10));
    }
    assert.fail("timed out waiting for " + label);
  };
  const click = (el) => el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, clientX: 20, clientY: 20 }));
  const doc = window.document;
  await wait(() => doc.querySelector("table.grid td.cell"), "roster grid");
  return { window, doc, calls, wait, click };
}

test("roster grid shows people, statuses, hand-edited cells and red coverage", async () => {
  const { doc } = await openPage();
  const tables = doc.querySelectorAll("#roster-box table.grid");
  const rows = tables[0].querySelectorAll("tbody tr");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].querySelectorAll("td.cell")[1].textContent, "WO");
  assert.ok(rows[0].querySelectorAll("td.cell")[1].classList.contains("locked"), "edited cell is marked");
  assert.ok(rows[1].querySelector("td.cell").classList.contains("c-L"));
  const cov = tables[1].querySelectorAll("tbody td");
  assert.ok(cov[1].classList.contains("c-bad"), "0 of 1 working is red");
  assert.equal(cov[1].textContent, "0/1");
  assert.ok(cov[2].classList.contains("c-ok"));
});

test("names are shown as text, never as HTML", async () => {
  const { doc, window } = await openPage();
  assert.equal(doc.querySelector("#roster-box img"), null);
  assert.equal(window.hacked, undefined);
  assert.ok(doc.querySelector("#roster-box").textContent.includes("<img"));
});

test("cell menu opens, and closes after choosing a status (regression)", async () => {
  const { doc, window, calls, wait, click } = await openPage();
  const menu = doc.getElementById("menu");
  assert.equal(window.getComputedStyle(menu).display, "none", "menu starts hidden");

  click(doc.querySelectorAll("td.cell")[3]); // second employee, second day
  assert.notEqual(window.getComputedStyle(menu).display, "none", "menu opens");
  assert.deepEqual([...menu.querySelectorAll("button")].map((b) => b.textContent), ["W", "WO", "PH"]);

  click([...menu.querySelectorAll("button")].find((b) => b.textContent === "WO"));
  assert.equal(window.getComputedStyle(menu).display, "none", "menu closes after a choice");
  await wait(() => calls.some((c) => c.route === "/api/roster/cell"), "cell save");
  const save = calls.find((c) => c.route === "/api/roster/cell");
  assert.deepEqual(save.body, { employee_id: 2, date: "2026-11-03", status: "WO" });
});

test("clicking elsewhere closes the menu", async () => {
  const { doc, window, click } = await openPage();
  click(doc.querySelector("td.cell"));
  assert.notEqual(window.getComputedStyle(doc.getElementById("menu")).display, "none");
  click(doc.body);
  assert.equal(window.getComputedStyle(doc.getElementById("menu")).display, "none");
});

test("issues tab lists the gap, explains it and applies a fix", async () => {
  const { doc, calls, wait, click } = await openPage();
  click(doc.querySelector('#tabs button[data-tab="issues"]'));
  await wait(() => doc.querySelector("#issues-box .issue"), "issue list");
  const box = doc.querySelector("#issues-box .issue");
  assert.ok(box.textContent.includes("nobody is working"));

  click([...box.querySelectorAll("button")].find((b) => b.textContent === "Explain"));
  await wait(() => box.querySelector(".explain").textContent.includes("rule-based"), "explanation");
  assert.ok(box.querySelector(".explain").textContent.includes("on leave"));

  click([...box.querySelectorAll("button")].find((b) => b.textContent.startsWith("Apply")));
  await wait(() => calls.some((c) => c.route === "/api/suggestion/apply"), "apply call");
  assert.equal(calls.find((c) => c.route === "/api/suggestion/apply").body.type, "add_backup");
});

test("compare tab scores plans and 'Use this plan' sends the strategy", async () => {
  const { doc, calls, wait, click } = await openPage();
  click(doc.querySelector('#tabs button[data-tab="compare"]'));
  await wait(() => doc.querySelectorAll("#compare-box tbody tr").length === 2, "plan table");
  click(doc.querySelectorAll("#compare-box tbody button")[1]);
  await wait(() => calls.some((c) => c.route === "/api/roster/generate"), "generate call");
  assert.equal(calls.find((c) => c.route === "/api/roster/generate").body.strategy, "fair");
});

test("review tab: approve is on while in review, publish stays off until all approve", async () => {
  const { doc, calls, wait, click } = await openPage();
  click(doc.querySelector('#tabs button[data-tab="review"]'));
  await wait(() => doc.querySelector("#review-box table"), "review table");
  const buttons = [...doc.querySelectorAll("#review-box button")];
  const approve = buttons.find((b) => b.textContent.includes("Approve"));
  const publish = buttons.find((b) => b.textContent === "Publish roster");
  assert.equal(approve.disabled, false);
  assert.equal(publish.disabled, true);
  await wait(() => doc.querySelector("#notes .note"), "notifications");
  assert.ok(doc.querySelector("#notes").textContent.includes("Roster changed"));
  click(approve);
  await wait(() => calls.some((c) => c.route === "/api/roster/approve"), "approve call");
  assert.equal(calls.find((c) => c.route === "/api/roster/approve").body.pod_id, 1);
});

test("auto re-plan checkbox is saved", async () => {
  const { doc, window, calls, wait } = await openPage();
  const box = doc.getElementById("auto");
  assert.equal(box.checked, true);
  box.checked = false;
  box.dispatchEvent(new window.Event("change", { bubbles: true }));
  await wait(() => calls.some((c) => c.method === "PUT" && c.route === "/api/settings"), "settings call");
  assert.deepEqual(calls.find((c) => c.method === "PUT" && c.route === "/api/settings").body, { auto_regen: false });
});

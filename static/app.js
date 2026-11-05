// Plain JavaScript, no build step. Talks to the Flask API in app.py.

const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CAL_OPTIONS = [
  ["ALL_DAYS", "365 days (incl. Sundays and holidays)"],
  ["NO_HOLIDAYS", "Every day except public holidays"],
  ["NO_SUN_HOL", "Closed Sundays and public holidays"],
];

const state = { month: "", tab: "roster", setupTable: "employees", data: {} };

// ---------- helpers ----------

// Small element builder. Uses textContent so names can never inject HTML.
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}

const $ = (id) => document.getElementById(id);

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer;
function toast(text, isError = false) {
  const t = $("toast");
  t.textContent = text;
  t.className = isError ? "error" : "";
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 6000 : 3500);
}

async function guard(fn) {
  try { return await fn(); } catch (e) { toast(e.message, true); }
}

async function loadTables() {
  for (const t of ["pods", "employees", "brands", "processes", "brand_processes",
                   "assignments", "leaves", "holidays"]) {
    state.data[t] = await api("GET", `/api/${t}`);
  }
}

function nameOf(table, id) {
  const row = state.data[table].find((r) => r.id === id);
  if (!row) return "";
  if (table === "brand_processes") return bpLabel(row);
  return row.name;
}

function bpLabel(bp) {
  const brand = state.data.brands.find((b) => b.id === bp.brand_id);
  const proc = state.data.processes.find((p) => p.id === bp.process_id);
  return `${brand ? brand.name : "?"} / ${proc ? proc.name : "?"}`;
}

// ---------- header ----------

async function refreshHeader() {
  const months = await api("GET", "/api/months");
  const info = months.find((m) => m.month === state.month);
  const badge = $("state-badge");
  badge.textContent = info ? `Roster: ${info.status}` : "Roster: not generated";
  badge.className = "badge " + (info && info.status === "published" ? "ok" : "");
  const res = await api("GET", `/api/issues?month=${state.month}`);
  const ib = $("issue-badge");
  if (!res.generated) { ib.textContent = ""; ib.className = "badge"; return; }
  ib.textContent = `${res.errors} errors, ${res.warnings} warnings`;
  ib.className = "badge " + (res.errors ? "bad" : res.warnings ? "warn" : "ok");
}

function showTab(tab) {
  state.tab = tab;
  for (const b of document.querySelectorAll("#tabs button")) b.classList.toggle("active", b.dataset.tab === tab);
  for (const name of ["roster", "issues", "compare", "review", "setup", "leaves"]) $("tab-" + name).hidden = name !== tab;
  renderTab();
}

function renderTab() {
  guard(async () => {
    await refreshHeader();
    if (state.tab === "roster") await renderRoster();
    if (state.tab === "issues") await renderIssues();
    if (state.tab === "compare") await renderCompare();
    if (state.tab === "review") await renderReview();
    if (state.tab === "setup") renderSetup();
    if (state.tab === "leaves") renderLeaves();
  });
}

// ---------- roster tab ----------

function fillTargets() {
  const view = $("view").value;
  const sel = $("target");
  const source = { pod: "pods", brand: "brands", process: "processes", employee: "employees" }[view];
  sel.replaceChildren();
  sel.hidden = !source;
  if (!source) return;
  for (const row of state.data[source]) sel.append(h("option", { value: row.id }, row.name));
}

async function renderRoster() {
  const view = $("view").value;
  const id = $("target").hidden ? "" : $("target").value;
  const data = await api("GET", `/api/roster?month=${state.month}&view=${view}&id=${id}`);
  const box = $("roster-box");
  box.replaceChildren();
  if (!data.generated) {
    box.append(h("p", { class: "hint" }, "No roster for this month yet. Click “Generate roster”."));
    return;
  }

  const head = h("tr", {}, h("th", { class: "name" }, "Employee"),
    data.dates.map((d) => h("th", { class: d.holiday ? "holiday" : "", title: d.holiday || "" },
      d.day, h("small", {}, d.dow))), h("th", { title: "Saturday/Sunday weekly offs" }, "Wknd offs"));
  const body = data.rows.map((r) => h("tr", {},
    h("td", { class: "name" }, (r.lead ? "★ " : "") + r.name, h("small", {}, `${r.pod} • ${r.assign}`)),
    r.cells.map((c, i) => h("td", {
      class: `cell c-${c}` + (r.locked[i] ? " locked" : ""),
      title: `${r.name}, ${data.dates[i].date}` + (r.locked[i] ? " (edited by hand, kept on re-plan)" : ""),
      onclick: (ev) => openMenu(ev, r.id, data.dates[i].date),
    }, c)), h("td", {}, r.weekend_offs)));
  box.append(h("div", { class: "grid-wrap" }, h("table", { class: "grid" }, h("thead", {}, head), h("tbody", {}, body))));

  if (view === "employee" && data.rows[0]) {
    const c = data.rows[0].counts;
    box.append(h("p", { class: "summary" },
      h("span", {}, `Working days: ${c.W}`), h("span", {}, `Weekly offs: ${c.WO}`),
      h("span", {}, `Holidays: ${c.PH}`), h("span", {}, `Leaves: ${c.L}`), h("span", {}, `Comp-offs: ${c.CO}`)));
  }

  if (data.coverage.length) {
    box.append(h("h2", { class: "cov-title" }, "Coverage (people working / people needed). Red = not enough."));
    const covHead = h("tr", {}, h("th", { class: "name" }, "Brand / process"),
      data.dates.map((d) => h("th", { class: d.holiday ? "holiday" : "" }, d.day, h("small", {}, d.dow))));
    const covBody = data.coverage.map((row) => h("tr", {},
      h("td", { class: "name" }, row.label, h("small", {}, row.calendar)),
      row.cells.map((c) => {
        if (c.need === 0) return h("td", { class: "c-closed", title: "Closed" }, "–");
        const bad = c.have < c.need;
        return h("td", { class: bad ? "c-bad" : "c-ok", title: c.who || "Nobody working" }, `${c.have}/${c.need}`);
      })));
    box.append(h("div", { class: "grid-wrap" }, h("table", { class: "grid" }, h("thead", {}, covHead), h("tbody", {}, covBody))));
  }
}

function openMenu(ev, employeeId, date) {
  const menu = $("menu");
  menu.replaceChildren(...["W", "WO", "PH"].map((s) => h("button", {
    class: `c-${s}`, onclick: () => { menu.hidden = true; changeCell(employeeId, date, s); },
  }, s)));
  menu.style.left = Math.min(ev.clientX, window.innerWidth - 150) + "px";
  menu.style.top = ev.clientY + 8 + "px";
  menu.hidden = false;
  ev.stopPropagation();
}
document.addEventListener("click", () => ($("menu").hidden = true));

function changeCell(employeeId, date, status) {
  guard(async () => {
    const res = await api("PUT", "/api/roster/cell", { employee_id: employeeId, date, status });
    toast(res.related.length ? "Change saved. Check: " + res.related.slice(0, 3).join(" | ")
                             : "Change saved. No new problems.");
    renderTab();
  });
}

// ---------- issues tab ----------

async function renderIssues() {
  const res = await api("GET", `/api/issues?month=${state.month}`);
  const box = $("issues-box");
  box.replaceChildren();
  if (!res.generated) { box.append(h("p", { class: "hint" }, "Generate the roster first.")); return; }
  if (!res.issues.length) { box.append(h("p", {}, "No issues. The roster can be published.")); return; }
  box.append(h("p", { class: "hint" },
    "Errors are mandatory rules (coverage, weekly off, allocation) and block publishing. Warnings are preferences."));
  for (const issue of [...res.issues].sort((a, b) => (a.severity === "error" ? 0 : 1) - (b.severity === "error" ? 0 : 1))) {
    const fixes = (issue.suggestions || []).map((s) =>
      h("button", { onclick: () => applySuggestion(s) }, "Apply: " + s.text));
    const answer = h("div", { class: "explain", hidden: true });
    box.append(h("div", { class: `issue ${issue.severity}` },
      h("strong", {}, issue.severity.toUpperCase() + ": "), issue.message, " ",
      h("button", { onclick: () => explainIssue(issue, answer) }, "Explain"), answer,
      fixes.length ? h("div", { class: "fix" }, "Possible fixes: ", fixes) : null,
      issue.type === "coverage_gap" && !fixes.length
        ? h("div", { class: "fix hint" }, "No automatic fix found. Add a backup employee or change the leave.") : null));
  }
}

function explainIssue(issue, box) {
  box.hidden = false;
  box.textContent = "Thinking...";
  api("POST", "/api/issues/explain", { month: state.month, issue })
    .then((res) => box.replaceChildren(res.text, " ",
      h("small", {}, res.source === "ai" ? "(AI explanation)" : "(rule-based explanation)"),
      res.note ? h("div", {}, h("small", {}, res.note)) : null))
    .catch((e) => { box.hidden = true; toast(e.message, true); });
}

function applySuggestion(s) {
  guard(async () => {
    await api("POST", "/api/suggestion/apply", s);
    await loadTables();
    toast("Applied.");
    renderTab();
  });
}

// ---------- compare plans ----------

async function renderCompare() {
  const res = await api("GET", `/api/compare?month=${state.month}`);
  const box = $("compare-box");
  box.replaceChildren(
    h("p", { class: "hint" }, "Each plan is built in memory and scored. Nothing is saved until you click “Use this plan”. " +
      "Lower is better in every column except spare cover."),
    h("table", { class: "edit" },
      h("thead", {}, h("tr", {}, ["Plan", "Coverage errors", "Preference weeks missed", "Average spare cover",
        "Weekend-off spread (max - min)", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, res.plans.map((p) => h("tr", {},
        h("td", {}, p.label), h("td", {}, p.errors), h("td", {}, p.unmet_pref_weeks),
        h("td", {}, p.avg_spare), h("td", {}, p.weekend_spread),
        h("td", {}, h("button", { class: "primary", onclick: () => usePlan(p) }, "Use this plan")))))));
}

function usePlan(p) {
  guard(async () => {
    await api("POST", "/api/roster/generate", { month: state.month, strategy: p.strategy });
    toast(`Roster rebuilt with: ${p.label}.`);
    renderTab();
  });
}

// ---------- review, approval, notifications ----------

async function renderReview() {
  const info = await api("GET", `/api/review?month=${state.month}`);
  const box = $("review-box");
  const label = { none: "Not generated", draft: "Draft", in_review: "In review", published: "Published" }[info.state];
  const act = (path, body) => guard(async () => { await api("POST", path, { month: state.month, ...body }); renderTab(); });
  box.replaceChildren(
    h("h2", {}, `Roster ${state.month}: ${label}`),
    h("p", { class: "hint" }, "Flow: manager submits, each pod lead approves, manager publishes. " +
      "Any later change sends the roster back to draft and notifies the pods. (No login in this demo: click Approve as the pod lead.)"),
    h("p", {},
      h("button", { class: "primary", disabled: info.state === "none" ? "disabled" : null,
        onclick: () => act("/api/roster/submit") }, "Submit for review"), " ",
      info.errors ? h("span", { class: "badge bad" }, `${info.errors} errors must be fixed first`) : null),
    h("table", { class: "edit" },
      h("thead", {}, h("tr", {}, ["Pod", "Pod lead", "Approval", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, info.pods.map((p) => h("tr", {},
        h("td", {}, p.name), h("td", {}, p.lead || "-"),
        h("td", {}, p.approved_at ? "Approved " + p.approved_at.replace("T", " ") : "Waiting"),
        h("td", {}, h("button", { disabled: info.state !== "in_review" ? "disabled" : null,
          onclick: () => act("/api/roster/approve", { pod_id: p.id }) }, "Approve as pod lead")))))),
    h("p", {}, h("button", { class: "primary", disabled: info.state === "in_review" && info.all_approved ? null : "disabled",
      onclick: () => act("/api/roster/publish") }, "Publish roster")),
    h("h2", {}, "Notifications"),
    h("div", {}, h("label", {}, "Show for ", h("select", { id: "note-pod", onchange: loadNotes },
      h("option", { value: "" }, "all pods"), state.data.pods.map((p) => h("option", { value: p.id }, p.name))))),
    h("div", { id: "notes" }));
  await loadNotes();
}

async function loadNotes() {
  const pod = $("note-pod") ? $("note-pod").value : "";
  const notes = await api("GET", "/api/notifications" + (pod ? `?pod_id=${pod}` : ""));
  $("notes").replaceChildren(...(notes.length ? notes.map((n) => h("div", { class: "note" }, n.message,
    h("small", {}, `${n.pod || "all"} • ${n.created_at.replace("T", " ")}`))) : [h("p", { class: "hint" }, "No notifications yet.")]));
}

// ---------- editable tables (team, rules, leaves, holidays) ----------

const days = [["", "none"], ...DOW.map((d, i) => [String(i), d])];
const fk = (table) => ({ type: "fk", table });
const FIELDS = {
  employees: [
    { k: "name", label: "Name" }, { k: "pod_id", label: "Pod", ...fk("pods") },
    { k: "weekly_offs", label: "Weekly offs per week", type: "number" },
    { k: "preferred_off", label: "Preferred off day", type: "select", options: days },
  ],
  pods: [{ k: "name", label: "Pod name" }, { k: "lead_id", label: "Pod lead", ...fk("employees") }],
  brands: [{ k: "name", label: "Brand name" }],
  processes: [{ k: "name", label: "Process name" }],
  brand_processes: [
    { k: "brand_id", label: "Brand", ...fk("brands") }, { k: "process_id", label: "Process", ...fk("processes") },
    { k: "calendar", label: "Operating calendar", type: "select", options: CAL_OPTIONS },
    { k: "min_staff", label: "Min staff per day", type: "number" },
    { k: "day_overrides", label: "Min staff on specific days (example Sat=2,Mon=3)", placeholder: "Sat=2" },
  ],
  assignments: [
    { k: "employee_id", label: "Employee", ...fk("employees") },
    { k: "brand_process_id", label: "Brand / process", ...fk("brand_processes") },
    { k: "allocation", label: "% of time", type: "number" },
  ],
  leaves: [
    { k: "employee_id", label: "Employee", ...fk("employees") },
    { k: "date", label: "Date", type: "date" }, { k: "to_date", label: "To date (optional, add only)", type: "date", addOnly: true },
    { k: "type", label: "Type", type: "select", options: [["L", "Leave"], ["CO", "Comp-off"]] },
    { k: "note", label: "Note" },
  ],
  holidays: [{ k: "date", label: "Date", type: "date" }, { k: "name", label: "Holiday name" }],
};
const DEFAULTS = {
  employees: { weekly_offs: 1 }, brand_processes: { calendar: "NO_SUN_HOL", min_staff: 1 },
  assignments: { allocation: 100 }, leaves: { type: "L" },
};
const TITLES = {
  employees: "Employees", pods: "Pods", brands: "Brands", processes: "Processes",
  brand_processes: "Brand rules (calendar and coverage)", assignments: "Assignments",
};

function inputFor(field, value) {
  let el;
  if (field.type === "fk") {
    el = h("select", {}, h("option", { value: "" }, "-"),
      state.data[field.table].map((r) => h("option", { value: r.id }, nameOf(field.table, r.id))));
  } else if (field.type === "select") {
    el = h("select", {}, field.options.map(([v, l]) => h("option", { value: v }, l)));
  } else {
    el = h("input", { type: field.type || "text", placeholder: field.placeholder || "" });
  }
  el.value = value === null || value === undefined ? "" : value;
  el.dataset.k = field.k;
  return el;
}

function editor(table, onChange) {
  const fields = FIELDS[table];
  const readRow = (tr) => Object.fromEntries([...tr.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value]));
  const rowFor = (row) => h("tr", {},
    fields.filter((f) => !f.addOnly).map((f) => h("td", {}, inputFor(f, row[f.k]))),
    h("td", {},
      h("button", { onclick: (ev) => guard(async () => {
        await api("PUT", `/api/${table}/${row.id}`, readRow(ev.target.closest("tr")));
        await onChange("Saved.");
      }) }, "Save"), " ",
      h("button", { class: "danger", onclick: () => {
        if (!confirm("Delete this row? Related assignments or leaves are removed too.")) return;
        guard(async () => { await api("DELETE", `/api/${table}/${row.id}`); await onChange("Deleted."); });
      } }, "Delete")));
  const newRow = h("tr", { class: "new" },
    fields.map((f) => h("td", {}, inputFor(f, (DEFAULTS[table] || {})[f.k]))),
    h("td", {}, h("button", { class: "primary", onclick: (ev) => guard(async () => {
      await api("POST", `/api/${table}`, readRow(ev.target.closest("tr")));
      await onChange("Added.");
    }) }, "Add new")));
  return h("table", { class: "edit" },
    h("thead", {}, h("tr", {}, fields.map((f) => h("th", {}, f.label)), h("th", {}, ""))),
    h("tbody", {}, newRow, state.data[table].map(rowFor)));
}

async function afterEdit(msg) {
  await loadTables();
  toast(msg);
  renderTab();
}

function renderSetup() {
  const nav = $("setup-nav");
  nav.replaceChildren(...Object.keys(TITLES).map((t) =>
    h("button", { class: t === state.setupTable ? "primary" : "", onclick: () => { state.setupTable = t; renderSetup(); } }, TITLES[t])));
  const box = $("setup-box");
  box.replaceChildren(
    h("p", { class: "hint" }, "Change anything here, then click “Generate roster” again. No code changes needed."),
    editor(state.setupTable, afterEdit));
}

function renderLeaves() {
  $("leaves-box").replaceChildren(editor("leaves", afterEdit));
  $("holidays-box").replaceChildren(editor("holidays", afterEdit));
}

// ---------- start ----------

async function init() {
  await loadTables();
  const months = await api("GET", "/api/months");
  const now = new Date().toISOString().slice(0, 7);
  state.month = months.length ? (months.find((m) => m.month === now) || months[0]).month : now;
  $("month").value = state.month;
  fillTargets();

  $("month").addEventListener("change", (e) => { if (e.target.value) { state.month = e.target.value; renderTab(); } });
  $("view").addEventListener("change", () => { fillTargets(); renderTab(); });
  $("target").addEventListener("change", renderTab);
  const build = (keep) => guard(async () => {
    const res = await api("POST", "/api/roster/generate", { month: state.month, keep_locked: keep });
    toast(`Roster built: ${res.errors} errors, ${res.warnings} warnings.`);
    renderTab();
  });
  $("btn-generate").addEventListener("click", () => build(false));
  $("btn-replan").addEventListener("click", () => build(true));
  $("btn-export").addEventListener("click", () => {
    const id = $("target").hidden ? "" : $("target").value;
    window.location.href = `/api/roster/export?month=${state.month}&view=${$("view").value}&id=${id}`;
  });
  const settings = await api("GET", "/api/settings");
  $("auto").checked = settings.auto_regen;
  $("auto").addEventListener("change", (e) => guard(async () => {
    await api("PUT", "/api/settings", { auto_regen: e.target.checked });
    toast(e.target.checked ? "Auto re-plan is on." : "Auto re-plan is off. Changes only show up as issues.");
  }));
  for (const b of document.querySelectorAll("#tabs button")) b.addEventListener("click", () => showTab(b.dataset.tab));
  renderTab();
}

init();

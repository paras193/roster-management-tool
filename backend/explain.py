"""Plain-English explanations of roster issues.

Always works with built-in rule-based text. If an OpenAI-compatible API is configured
(OpenRouter, OpenAI, a local server ...), the same facts are sent to it and the reply is
used instead. Settings (all optional except the key):
  LLM_API_KEY    key for the API (no key = rule-based text only)
  LLM_BASE_URL   default https://openrouter.ai/api/v1
  LLM_MODEL      default openai/gpt-4o-mini
Only fictional names and rules from this app are sent, nothing else.
"""
import json
import os
import urllib.error
import urllib.request

from . import roster


def facts(ctx, status, issue):
    """Build the facts for one issue from the database, not from what the browser sent."""
    kind = issue.get("type")
    emps = ctx["emps"]
    if kind == "coverage_gap":
        bp_id = int(issue["brand_process_id"])
        d = roster.dt.date.fromisoformat(issue["date"])
        bp = ctx["bps"][bp_id]
        need = roster.required(ctx, bp_id, d)
        lines = [f"{bp['label']} on {d:%A %d %B %Y} needs {need} person(s) working.",
                 f"Its calendar is: {roster.CALENDAR_NAMES[bp['calendar']]}.",
                 "Assigned people that day:"]
        meaning = {"W": "working", "WO": "weekly off", "PH": "public holiday", "L": "on leave",
                   "CO": "comp-off"}
        for m in ctx["members"][bp_id]:
            lines.append(f"- {emps[m]['name']}: {meaning.get(status.get((m, d)), 'unknown')}")
        fixes = roster._suggestions(ctx, status, bp_id, d)
        lines.append("Possible fixes: " + ("; ".join(f["text"] for f in fixes) or "none found"))
        return lines
    if kind in ("no_weekly_off", "preference", "overallocated"):
        e = int(issue["employee_id"])
        return [issue.get("message", ""), f"Employee: {emps[e]['name']}, pod {emps[e]['pod']}.",
                f"Assigned to: {roster._assign_text(ctx, e)}."]
    return [issue.get("message", "")]


def rule_text(lines, issue):
    kind = issue.get("type")
    if kind == "coverage_gap":
        return (" ".join(lines[:2]) + " " + " ".join(l.lstrip("- ") + "." for l in lines[3:-1]) +
                " That leaves the process short. " + lines[-1] + ".")
    return " ".join(lines)


last_error = None  # why the last AI call failed (never contains the key)


def ask_llm(lines):
    global last_error
    key = os.environ.get("LLM_API_KEY")
    if not key:
        return None
    url = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/") + "/chat/completions"
    body = {
        "model": os.environ.get("LLM_MODEL", "openai/gpt-4o-mini"),
        "max_tokens": 300,
        "messages": [
            {"role": "system", "content": "You help an operations manager fix a staff roster. "
             "Explain the problem and the best fix in plain English, under 90 words, no markdown. "
             "Use only the facts given."},
            {"role": "user", "content": "\n".join(lines)},
        ],
    }
    req = urllib.request.Request(url, json.dumps(body).encode(), {
        "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            text = json.load(res)["choices"][0]["message"]["content"]
        return text.strip() or None
    except urllib.error.HTTPError as e:
        last_error = f"HTTP {e.code} from {url.split('/')[2]}"
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
        last_error = type(e).__name__
    return None  # fall back to the rule-based text


def explain(ctx, status, issue):
    global last_error
    lines = facts(ctx, status, issue)
    last_error = None
    text = ask_llm(lines)
    if text:
        return {"text": text, "source": "ai"}
    result = {"text": rule_text(lines, issue), "source": "rules"}
    if last_error:
        result["note"] = f"AI call failed ({last_error}), showing rule-based text."
    return result

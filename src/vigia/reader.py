"""An LLM reads each new job against the candidate's CV and scores it, so the best come first.

The rules in `flags` decide what is in or out; this reads what rules miss (where the company
really hires, years, hard requirements the CV lacks) and gives a 1-10 match with one line of
why. The answer is stored in `job_readings` and is what orders `new_jobs` and what triggers a
phone notification (eligible, or abroad with a visa, and a match at or above `notify_min_match`).

Readers, in order (config `reader.readers`), each one of:
  - claude-p:  the Claude Code CLI in print mode (`claude -p`), on the subscription it is logged
               in with (CLAUDE_CODE_OAUTH_TOKEN). No tools, no MCP servers, no settings: the
               prompt carries untrusted text (job postings).
  - anthropic / openai: the API through LangChain (ANTHROPIC_API_KEY / OPENAI_API_KEY).
If the first fails, the next one reads the job and a notification says so (at most every
`alert_every_hours`). API spend is tracked and capped at `api_budget_usd` per month.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vigia import config, db, notify

log = logging.getLogger("vigia")

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["is_job_posting", "ecuador_eligible", "eligibility_quote", "visa_sponsorship", "years_required",
                 "level", "blockers", "match", "why"],
    "properties": {
        "is_job_posting": {"type": "boolean",
                           "description": "True if the text offers one specific open role (one employer or one client, "
                                          "one position, even if it has several openings). False for promotional or "
                                          "blog-style posts, lists of many different roles, generic 'we are always "
                                          "hiring' or talent-pool calls, and articles."},
        "ecuador_eligible": {"type": "string", "enum": ["yes", "no", "unclear"],
                             "description": "Can the candidate be hired for this role as written, from where they live? "
                                            "'no' if it requires living/working in another country without visa "
                                            "sponsorship, or on-site work somewhere they cannot go."},
        "eligibility_quote": {"type": "string",
                              "description": "Shortest quote from the posting that decides eligibility, or '' if nothing says"},
        "visa_sponsorship": {"type": "string", "enum": ["yes", "no", "not_stated"]},
        "years_required": {"anyOf": [{"type": "integer"}, {"type": "null"}],
                           "description": "Minimum years of experience required, null if not stated"},
        "level": {"type": "string", "enum": ["junior", "mid", "senior", "staff_or_above", "unstated"]},
        "blockers": {"type": "array", "items": {"type": "string"},
                     "description": "Hard requirements the candidate clearly lacks according to the CV (a must-have "
                                    "language, framework, domain, degree or years). Nice-to-haves are not blockers."},
        "match": {"type": "integer", "minimum": 1, "maximum": 10,
                  "description": "How well the candidate fits what the role needs, 1-10, ignoring location"},
        "why": {"type": "string", "description": "One sentence"},
    },
}
SYSTEM = ("You screen job postings for one candidate. Be literal: use only what the posting says. {candidate}"
          "\n\nCandidate CV:\n{cv}")
FAMILY_OF = {"ai": "ai", "backend": "backend"}  # flags category -> CV file key; anything else: fullstack


def cfg() -> dict:
    return config.load().get("reader", {})


def cv_for(category: str | None) -> str:
    c = cfg()
    files = c.get("cvs", {})
    name = files.get(FAMILY_OF.get(category or "", "fullstack")) or next(iter(files.values()), "")
    return (Path(c.get("cv_dir", "/app/secrets/cv")) / name).read_text() if name else ""


def messages(job: dict) -> tuple[str, str]:
    category = json.loads(job.get("flags") or "{}").get("category")
    system = SYSTEM.format(candidate=cfg().get("candidate", ""), cv=cv_for(category))
    user = (f"Job posting\nTitle: {job['title']}\nCompany: {job['company']}\nLocation field: {job.get('location') or ''}\n"
            f"Salary: {job.get('salary') or ''}\n\n{(job.get('description') or '')[:12000]}")
    return system, user


# --- readers -------------------------------------------------------------------------

_EMPTY_DIR = None


def read_claude_p(system: str, user: str, model: str) -> tuple[dict, dict]:
    """One `claude -p` run, locked down. Returns (reading, usage)."""
    global _EMPTY_DIR
    _EMPTY_DIR = _EMPTY_DIR or tempfile.mkdtemp(prefix="vigia-reader-")
    args = ["claude", "-p", "--model", model, "--system-prompt", system, "--json-schema", json.dumps(SCHEMA),
            "--output-format", "json", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
            "--no-session-persistence"]
    # Claude Code prefers ANTHROPIC_API_KEY over the subscription token when both are set, which
    # would bill the API (seen 2026-10-22). This reader is the subscription one: drop the API keys.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    p = subprocess.run(args, input=user, capture_output=True, text=True, cwd=_EMPTY_DIR, env=env,
                       timeout=int(cfg().get("timeout_seconds", 300)))
    try:
        out = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(f"claude -p exited {p.returncode}: {(p.stderr or p.stdout)[:300]}") from None
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude -p: {str(out.get('result') or out.get('subtype'))[:300]}")
    mu = out.get("modelUsage") or {}
    return out["structured_output"], {
        "input_tokens": sum(v.get("inputTokens", 0) + v.get("cacheCreationInputTokens", 0) for v in mu.values()),
        "output_tokens": sum(v.get("outputTokens", 0) for v in mu.values()), "cost_usd": 0.0}


def read_api(provider: str, system: str, user: str, model: str, price: list | None) -> tuple[dict, dict]:
    from langchain_core.messages import HumanMessage, SystemMessage
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model=model, max_tokens=8000, timeout=180, max_retries=2)
    elif provider == "openai":
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model, timeout=180, max_retries=2)
    else:
        raise ValueError(f"unknown reader provider {provider!r}")
    chain = llm.with_structured_output({"title": "Reading", "description": "Screening of one job posting", **SCHEMA},
                                       method="json_schema", include_raw=True)
    r = chain.invoke([SystemMessage(system), HumanMessage(user)])
    if r.get("parsed") is None:
        raise RuntimeError(f"{provider}: no structured answer ({r.get('parsing_error')})")
    u = r["raw"].usage_metadata or {}
    tin, tout = u.get("input_tokens", 0), u.get("output_tokens", 0)
    cost = (tin * price[0] + tout * price[1]) / 1e6 if price else 0.0
    return r["parsed"], {"input_tokens": tin, "output_tokens": tout, "cost_usd": cost}


def read(job: dict) -> tuple[dict, dict, str]:
    """Try each configured reader in order. Returns (reading, usage, 'provider:model')."""
    system, user = messages(job)
    errors = []
    for i, r in enumerate(cfg().get("readers") or [{"provider": "claude-p", "model": "sonnet"}]):
        name = f"{r['provider']}:{r['model']}"
        try:
            if r["provider"] == "claude-p":
                reading, usage = read_claude_p(system, user, r["model"])
            else:
                if api_spent_this_month() >= float(cfg().get("api_budget_usd", 3)):
                    raise RuntimeError("monthly API budget reached")
                reading, usage = read_api(r["provider"], system, user, r["model"], r.get("price"))
            if errors:
                alert(f"{errors[0]} — now reading with {name}")
            return reading, usage, name
        except Exception as e:  # noqa: BLE001 — try the next reader
            log.warning("reader %s failed: %s", name, e)
            errors.append(f"{name} failed: {str(e)[:200]}")
    alert("; ".join(errors) + " — jobs are not being scored")
    raise RuntimeError("; ".join(errors))


def alert(text: str) -> None:
    """Tell the phone a reader failed, at most every alert_every_hours (quiet hours included: it matters)."""
    last = db.get_state("reader.alerted_at")
    every = timedelta(hours=float(cfg().get("alert_every_hours", 6)))
    if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < every:
        return
    if notify.send("Vigía: job reader problem", text[:900], tags="warning"):
        db.set_state("reader.alerted_at", db.now())


def api_spent_this_month() -> float:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    row = db.conn().execute("SELECT COALESCE(SUM(cost_usd), 0) FROM job_readings WHERE substr(read_at, 1, 7) = ?",
                            (month,)).fetchone()
    return float(row[0])


# --- the loop --------------------------------------------------------------------------

def pending(limit: int) -> list[dict]:
    """Jobs worth reading that have no reading yet: fits, then reviews, then extras; newest first."""
    days = int(cfg().get("max_age_days", 14))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.conn().execute(
        """SELECT j.* FROM jobs j LEFT JOIN job_readings r ON r.job_id = j.id
           WHERE r.job_id IS NULL AND j.verdict IN ('fit', 'review', 'extra') AND j.first_seen >= ?
           AND length(coalesce(j.description, '')) >= 200
           ORDER BY CASE j.verdict WHEN 'fit' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, j.first_seen DESC
           LIMIT ?""", (since, limit)).fetchall()
    return [dict(r) for r in rows]


def worth_notifying(reading: dict, job: dict | None = None) -> bool:
    # Only a clear yes (or a visa) reaches the phone; "unclear" stays in new_jobs, below the clear ones.
    open_to_me = reading["ecuador_eligible"] == "yes" or reading["visa_sponsorship"] == "yes"
    good = (open_to_me and reading.get("is_job_posting", True)
            and reading["match"] >= int(cfg().get("notify_min_match", 7)))
    if not good or job is None:
        return good
    now = datetime.now(timezone.utc)
    # A backlog read (reader just switched on, or catching up) is scored but not pushed.
    seen = datetime.fromisoformat(job["first_seen"])
    if now - seen > timedelta(hours=float(cfg().get("notify_max_age_hours", 12))):
        return False
    # New to Vigía is not new to the world: a company just added brings its whole open board.
    # With a posting date, it must be recent; without one, the board must have been watched
    # since before the job appeared.
    if job.get("posted_at"):
        posted = datetime.fromisoformat(job["posted_at"])
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return now - posted <= timedelta(days=float(cfg().get("notify_max_posted_days", 3)))
    return not db.board_added_recently(job["source"], job["url"], seen,
                                       timedelta(hours=float(cfg().get("new_board_grace_hours", 36))))


def run() -> dict:
    """Read up to `per_run` pending jobs; notify the good ones. Returns a summary."""
    jobs = pending(int(cfg().get("per_run", 10)))
    read_n = good = 0
    for job in jobs:
        try:
            reading, usage, model = read(job)
        except Exception:  # noqa: BLE001 — every reader failed; try again next run
            break
        db.save_reading(job["id"], model, reading, usage)
        read_n += 1
        if worth_notifying(reading, job):
            good += 1
            if not notify.quiet():
                notify.send(f"{reading['match']}/10 · {job['title'][:70]}",
                            f"{job['company']} · {job.get('location') or 'location n/a'}\n{reading['why'][:300]}",
                            job["url"], tags="star")
    return {"read": read_n, "notified": good, "pending": len(jobs) - read_n}


def due(now: datetime | None = None) -> bool:
    if not cfg().get("enabled"):
        return False
    last = db.get_state("reader.last_run")
    every = timedelta(minutes=float(cfg().get("every_minutes", 5)))
    return not last or (now or datetime.now(timezone.utc)) - datetime.fromisoformat(last) >= every

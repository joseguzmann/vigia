"""Data access for the web UI.

The web process reads `jobs`, `runs`, `requests` and `state`, and writes only to `actions`:
what the person did with each job. Every write is one short statement and an immediate commit,
so it never holds the lock the collector needs.

actions
  id        INTEGER PRIMARY KEY
  job_id    INTEGER   jobs.id
  ts        TEXT      UTC ISO timestamp of the action
  action    TEXT      "applied" | "skipped" | "saved"
  variant   TEXT      applied: the experiment arm (A1 = AI CV, A2 = CV for the role type; or free text)
  reason    TEXT      skipped: years | location | stack | closed | level | other
  outcome   TEXT      applied: none | ack | rejected | screen | technical | final | offer | ghost
  note      TEXT      free text
  age_days  INTEGER   age of the posting in days when the action was taken (freshness at
                      apply time); NULL when the posting has no date
  extra     INTEGER   1 = role with visa/relocation, tracked apart from the 2x2

A job can have several actions (saved, then applied); the latest one is its current state.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone

from vigia import db

ACTIONS = ("applied", "skipped", "saved")
VARIANTS = ("A1", "A2")
SKIP_REASONS = ("years", "location", "stack", "closed", "level", "other")
OUTCOMES = ("none", "ack", "rejected", "screen", "technical", "final", "offer", "ghost")
POSITIVE = frozenset({"screen", "technical", "final", "offer"})
VERDICTS = ("fit", "extra", "review", "out")
DEFAULT_VERDICTS = ("fit", "extra", "review")
VERDICT_LABELS = {"fit": "fit", "extra": "Extra: abroad with visa/relocation",
                  "review": "review", "out": "out"}
CATEGORY_ORDER = ("ai", "backend", "other")  # display order only; new ones are read from the DB
LEVELS = ("junior", "mid", "senior", "lead", "principal", "unstated")
FRESHNESS = ("0-2 days", "3-7 days", "8-14 days", "15+ days", "no date")

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id       INTEGER PRIMARY KEY,
    job_id   INTEGER NOT NULL,
    ts       TEXT NOT NULL,
    action   TEXT NOT NULL,
    variant  TEXT,
    reason   TEXT,
    outcome  TEXT,
    note     TEXT,
    age_days INTEGER,
    extra    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS actions_job ON actions(job_id, id);
"""

_ready: set[str] = set()
_ready_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    """A connection for one request. Same file, WAL and busy timeout as the collector."""
    p = db.path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    with _ready_lock:
        if str(p) not in _ready:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(db.SCHEMA)  # idempotent; a fresh install may open the UI first
            c.executescript(SCHEMA)
            _ready.add(str(p))
    return c


# --- jobs ----------------------------------------------------------------------

def age_days(row) -> int | None:
    """Age of the posting now. `flags.age_days` is frozen at judge time, so prefer posted_at."""
    if row["posted_at"]:
        try:
            posted = datetime.fromisoformat(row["posted_at"])
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            return max(0, (datetime.now(timezone.utc) - posted).days)
        except ValueError:
            pass
    return json.loads(row["flags"] or "{}").get("age_days")


def freshness_bucket(age: int | None) -> str:
    if age is None:
        return FRESHNESS[4]
    return FRESHNESS[0] if age <= 2 else FRESHNESS[1] if age <= 7 else FRESHNESS[2] if age <= 14 else FRESHNESS[3]


def view(row, last: sqlite3.Row | None = None) -> dict:
    flags = json.loads(row["flags"] or "{}")
    seen = json.loads(row["seen_in"] or "[]")
    age = age_days(row)
    return {
        "id": row["id"], "title": row["title"], "company": row["company"], "url": row["url"],
        "location": row["location"], "salary": row["salary"], "score": row["score"],
        "verdict": row["verdict"], "posted_at": row["posted_at"], "first_seen": row["first_seen"],
        "last_seen": row["last_seen"], "description": row["description"], "source": row["source"],
        "remote": row["remote"], "tags": json.loads(row["tags"] or "[]"),
        "flags": flags, "seen_in": seen, "sources": sorted({s["source"] for s in seen}),
        "age": age, "fresh": age is not None and age <= 2,
        "years": flags.get("years"), "level": flags.get("level"), "category": flags.get("category"),
        "reasons": flags.get("reasons") or [], "stack": flags.get("stack") or [],
        "last_action": dict(last) if last else None,
    }


def latest_actions(c: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    rows = c.execute("""SELECT a.* FROM actions a
                        JOIN (SELECT job_id, MAX(id) id FROM actions GROUP BY job_id) m ON a.id = m.id""")
    return {r["job_id"]: r for r in rows}


def list_jobs(c: sqlite3.Connection, verdicts=DEFAULT_VERDICTS, source: str = "", category: str = "",
              level: str = "", max_age: int | None = None, q: str = "", only_open: bool = False,
              limit: int = 500) -> tuple[list[dict], int]:
    """Filtered jobs, freshest first, then by score. Returns (page, total matching)."""
    verdicts = [v for v in verdicts if v in VERDICTS] or list(VERDICTS)
    sql = f"SELECT * FROM jobs WHERE verdict IN ({','.join('?' * len(verdicts))})"
    args: list = list(verdicts)
    if q.strip():
        sql += " AND (title LIKE ? OR company LIKE ?)"
        args += [f"%{q.strip()}%"] * 2
    latest = latest_actions(c)
    out = []
    for r in c.execute(sql, args):
        j = view(r, latest.get(r["id"]))
        if source and source not in j["sources"]:
            continue
        if category and j["category"] != category:
            continue
        if level and (j["level"] or "unstated") != level:
            continue
        if max_age is not None and (j["age"] is None or j["age"] > max_age):
            continue
        if only_open and j["last_action"]:
            continue
        out.append(j)
    out.sort(key=lambda j: (j["age"] is None, j["age"] or 0, -(j["score"] or 0), -j["id"]))
    return out[:limit], len(out)


def get_job(c: sqlite3.Connection, job_id: int) -> dict | None:
    row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    j = view(row)
    j["actions"] = [dict(a) for a in c.execute("SELECT * FROM actions WHERE job_id = ? ORDER BY id DESC",
                                                (job_id,))]
    j["last_action"] = j["actions"][0] if j["actions"] else None
    return j


def known_sources(c: sqlite3.Connection) -> list[str]:
    return [r[0] for r in c.execute("SELECT DISTINCT source FROM jobs ORDER BY source")]


def known_categories(c: sqlite3.Connection) -> list[str]:
    """Categories present in the data (the judge may add new ones), known ones first."""
    found = {r[0] for r in c.execute("SELECT DISTINCT json_extract(flags, '$.category') FROM jobs") if r[0]}
    rank = {v: i for i, v in enumerate(CATEGORY_ORDER)}
    return sorted(found, key=lambda k: (rank.get(k, len(rank)), k))


# --- actions -------------------------------------------------------------------

def record_action(c: sqlite3.Connection, job_id: int, action: str, variant: str = "", reason: str = "",
                  note: str = "", extra: bool = False) -> int:
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError(job_id)
    variant = variant.strip() if action == "applied" else ""
    if action == "applied" and not variant:
        raise ValueError("an application needs a variant")
    if action == "skipped" and reason not in SKIP_REASONS:
        raise ValueError("a skip needs a reason")
    cur = c.execute(
        """INSERT INTO actions(job_id, ts, action, variant, reason, outcome, note, age_days, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, db.now(), action, variant or None, reason if action == "skipped" else None,
         "none" if action == "applied" else None, note.strip() or None, age_days(row),
         int(bool(extra)) if action == "applied" else 0))
    c.commit()
    return cur.lastrowid


def set_outcome(c: sqlite3.Connection, action_id: int, outcome: str) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome: {outcome}")
    n = c.execute("UPDATE actions SET outcome = ? WHERE id = ? AND action = 'applied'",
                  (outcome, action_id)).rowcount
    c.commit()
    if not n:
        raise LookupError(action_id)


def delete_action(c: sqlite3.Connection, action_id: int) -> int | None:
    row = c.execute("SELECT job_id FROM actions WHERE id = ?", (action_id,)).fetchone()
    c.execute("DELETE FROM actions WHERE id = ?", (action_id,))
    c.commit()
    return row["job_id"] if row else None


# --- experiment ----------------------------------------------------------------

def applications(c: sqlite3.Connection) -> list[dict]:
    """One row per applied job (its latest application), newest first."""
    rows = c.execute("""
        SELECT a.id action_id, a.ts, a.variant, a.outcome, a.note, a.age_days, a.extra, j.*
        FROM actions a
        JOIN (SELECT job_id, MAX(id) id FROM actions WHERE action = 'applied' GROUP BY job_id) m
          ON a.id = m.id
        JOIN jobs j ON j.id = a.job_id
        ORDER BY a.id DESC""").fetchall()
    out = []
    for r in rows:
        flags = json.loads(r["flags"] or "{}")
        out.append({
            "action_id": r["action_id"], "job_id": r["id"], "ts": r["ts"], "title": r["title"],
            "company": r["company"], "variant": r["variant"], "outcome": r["outcome"] or "none",
            "note": r["note"], "extra": bool(r["extra"]), "age_at_apply": r["age_days"],
            "freshness": freshness_bucket(r["age_days"]), "source": r["source"],
            "category": flags.get("category") or "?", "level": flags.get("level") or "unstated",
            "positive": (r["outcome"] or "none") in POSITIVE,
        })
    return out


def summarize(apps: list[dict], key: str, order: tuple = ()) -> list[dict]:
    """Applied, positive signals (screen or further) and rate per value of `key`."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in apps:
        groups[str(a[key])].append(a)
    rank = {v: i for i, v in enumerate(order)}
    rows = []
    for k in sorted(groups, key=lambda k: (rank.get(k, len(rank)), k)):
        n = len(groups[k])
        pos = sum(a["positive"] for a in groups[k])
        rows.append({"key": k, "applied": n, "positive": pos, "rate": pos / n if n else 0.0})
    return rows


def skip_reasons(c: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = c.execute("""SELECT a.reason, COUNT(*) n FROM actions a
                        JOIN (SELECT job_id, MAX(id) id FROM actions GROUP BY job_id) m ON a.id = m.id
                        WHERE a.action = 'skipped' GROUP BY a.reason ORDER BY n DESC""")
    return [(r["reason"], r["n"]) for r in rows]

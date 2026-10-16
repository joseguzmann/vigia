"""Data access for the web UI.

The web process reads `jobs`, `runs`, `requests` and `state`, and writes to `actions` (jobs
skipped or saved for later) and, through `vigia.applications`, to `applications`. Every write is one short statement and an immediate commit,
so it never holds the lock the collector needs.

actions
  id        INTEGER PRIMARY KEY
  job_id    INTEGER   jobs.id
  ts        TEXT      UTC ISO timestamp of the action
  action    TEXT      "skipped" | "saved"  (applications have their own table now; old
                      "applied" rows are moved there by `migrate_applied_actions`)
  variant   TEXT      legacy: experiment arm of an old "applied" row
  reason    TEXT      skipped: years | location | stack | closed | level | other
  outcome   TEXT      legacy
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

from vigia import applications as apps
from vigia import db

ACTIONS = ("skipped", "saved")
SKIP_REASONS = ("years", "location", "stack", "closed", "level", "other")
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
            c.executescript(apps.SCHEMA)
            migrate_applied_actions(c)
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


def view(row, last: dict | None = None) -> dict:
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


def latest_actions(c: sqlite3.Connection) -> dict[int, dict]:
    """What was last done with each job. An application wins over a skip or a save."""
    rows = c.execute("""SELECT a.* FROM actions a
                        JOIN (SELECT job_id, MAX(id) id FROM actions GROUP BY job_id) m ON a.id = m.id""")
    out = {r["job_id"]: dict(r) for r in rows}
    for r in c.execute("SELECT job_id, number, status, variant FROM applications WHERE job_id IS NOT NULL"):
        out[r["job_id"]] = {"action": "applied", "variant": f"#{r['number']} {r['status']}"}
    return out


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
    j["applications"] = apps.for_job(job_id, c)
    j["last_action"] = latest_actions(c).get(job_id)
    return j


def known_sources(c: sqlite3.Connection) -> list[str]:
    return [r[0] for r in c.execute("SELECT DISTINCT source FROM jobs ORDER BY source")]


def known_categories(c: sqlite3.Connection) -> list[str]:
    """Categories present in the data (the judge may add new ones), known ones first."""
    found = {r[0] for r in c.execute("SELECT DISTINCT json_extract(flags, '$.category') FROM jobs") if r[0]}
    rank = {v: i for i, v in enumerate(CATEGORY_ORDER)}
    return sorted(found, key=lambda k: (rank.get(k, len(rank)), k))


# --- actions -------------------------------------------------------------------

def record_action(c: sqlite3.Connection, job_id: int, action: str, reason: str = "", note: str = "") -> int:
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")
    row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError(job_id)
    if action == "skipped" and reason not in SKIP_REASONS:
        raise ValueError("a skip needs a reason")
    cur = c.execute(
        """INSERT INTO actions(job_id, ts, action, variant, reason, outcome, note, age_days, extra)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, db.now(), action, None, reason if action == "skipped" else None,
         None, note.strip() or None, age_days(row), 0))
    c.commit()
    return cur.lastrowid


def delete_action(c: sqlite3.Connection, action_id: int) -> int | None:
    row = c.execute("SELECT job_id FROM actions WHERE id = ?", (action_id,)).fetchone()
    c.execute("DELETE FROM actions WHERE id = ?", (action_id,))
    c.commit()
    return row["job_id"] if row else None


# --- applications ----------------------------------------------------------------

def migrate_applied_actions(c: sqlite3.Connection) -> int:
    """Move "applied" rows of the old actions table into applications (once)."""
    old = c.execute("SELECT * FROM actions WHERE action = 'applied' ORDER BY id").fetchall()
    for a in old:
        if not c.execute("SELECT 1 FROM applications WHERE job_id = ?", (a["job_id"],)).fetchone():
            if c.execute("SELECT 1 FROM jobs WHERE id = ?", (a["job_id"],)).fetchone():
                apps.add(job_id=a["job_id"], applied_at=a["ts"][:10], origin="import",
                         variant=a["variant"] if a["variant"] in apps.VARIANTS else None,
                         extra=bool(a["extra"]), age_days=a["age_days"], notes=a["note"], c=c)
        c.execute("DELETE FROM actions WHERE id = ?", (a["id"],))
    c.commit()
    return len(old)


def skip_reasons(c: sqlite3.Connection) -> list[tuple[str, int]]:
    rows = c.execute("""SELECT a.reason, COUNT(*) n FROM actions a
                        JOIN (SELECT job_id, MAX(id) id FROM actions GROUP BY job_id) m ON a.id = m.id
                        WHERE a.action = 'skipped' GROUP BY a.reason ORDER BY n DESC""")
    return [(r["reason"], r["n"]) for r in rows]


# --- sources -------------------------------------------------------------------

SOURCE_INFO = {
    "getonbrd": ("Get on Board", "LATAM tech job board", "public API"),
    "multitrabajos": ("Multitrabajos", "Ecuador's main job board (Bumeran network), whole IT area", "web app API"),
    "computrabajo": ("Computrabajo Ecuador", "local job board, searched by role", "HTML + JSON-LD"),
    "greenhouse": ("Greenhouse", "company job boards on this applicant-tracking system", "public API"),
    "ashby": ("Ashby", "company job boards on this applicant-tracking system", "public API"),
    "lever": ("Lever", "company job boards on this applicant-tracking system", "public API"),
    "workday": ("Workday", "career sites of large companies; searched, not read whole", "public JSON"),
    "teamtailor": ("Teamtailor", "company career sites on this applicant-tracking system", "RSS"),
    "workable": ("Workable", "company job boards on this applicant-tracking system", "public widget API"),
    "smartrecruiters": ("SmartRecruiters", "company job boards on this applicant-tracking system", "public API"),
    "breezy": ("Breezy HR", "company job boards on this applicant-tracking system", "JSON + job page"),
    "recruitee": ("Recruitee", "company job boards on this applicant-tracking system", "public API"),
    "remoteok": ("RemoteOK", "remote job board", "public JSON feed"),
    "remotive": ("Remotive", "remote job board", "public API"),
    "himalayas": ("Himalayas", "remote job board", "public API"),
    "jobicy": ("Jobicy", "remote job board", "public API"),
    "hn_whoishiring": ("Hacker News: Who is hiring?", "the monthly thread, one comment per posting", "Algolia API"),
    "strider": ("Strider", "LATAM engineers for US companies", "HTML + JSON-LD"),
    "workingnomads": ("Working Nomads", "remote job board", "public API"),
    "weworkremotely": ("We Work Remotely", "remote job board", "RSS"),
    "linkedin": ("LinkedIn jobs", "logged-out job search, slow and rotating", "public pages"),
    "linkedin_posts": ("LinkedIn hiring posts", "posts found through Serper (Google's index)", "Serper API + public pages"),
}


def sources_overview(c: sqlite3.Connection, registry, cfg_for) -> list[dict]:
    """One row per source: config, last run, jobs it has seen and applications they led to."""
    seen: dict[str, dict] = defaultdict(lambda: {"jobs": 0, "fit": 0, "extra": 0, "review": 0, "out": 0, "fresh": 0})
    job_sources: dict[int, list[str]] = {}
    for r in c.execute("SELECT id, verdict, seen_in, posted_at, flags FROM jobs"):
        names = sorted({s["source"] for s in json.loads(r["seen_in"] or "[]")})
        job_sources[r["id"]] = names
        age = age_days(r)
        for n in names:
            seen[n]["jobs"] += 1
            seen[n][r["verdict"] or "out"] += 1
            if age is not None and age <= 7 and r["verdict"] in ("fit", "extra"):
                seen[n]["fresh"] += 1
    applied: dict[str, int] = defaultdict(int)
    for r in c.execute("SELECT job_id FROM applications WHERE job_id IS NOT NULL"):
        for n in job_sources.get(r["job_id"], []):
            applied[n] += 1
    week = c.execute("""SELECT source, COUNT(*) runs, SUM(new) new, SUM(error IS NOT NULL AND error != '') errors
                        FROM runs WHERE started_at >= datetime('now', '-7 days') GROUP BY source""")
    week = {r["source"]: dict(r) for r in week}
    out = []
    for name in registry:
        cfg = cfg_for(name)
        last = c.execute("SELECT * FROM runs WHERE source = ? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        label, what, how = SOURCE_INFO.get(name, (name, "", ""))
        searches = cfg.get("boards") or cfg.get("queries") or cfg.get("phrases") or []
        searches = [q.get("keywords", "") + (f" · {q['location']}" if q.get("location") else "")
                    if isinstance(q, dict) else str(q) for q in searches]
        out.append({
            "name": name, "label": label, "what": what, "how": how,
            "enabled": bool(cfg.get("enabled")), "every": cfg.get("every_minutes"),
            "boards": bool(cfg.get("boards")), "searches": searches,
            "last": dict(last) if last else None, "week": week.get(name, {"runs": 0, "new": 0, "errors": 0}),
            **seen[name], "applied": applied.get(name, 0),
        })
    out.sort(key=lambda s: (not s["enabled"], -(s["fit"] + s["extra"]), s["name"]))
    return out

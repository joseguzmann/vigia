"""SQLite storage. One file, on a Docker volume in production.

Tables:
  jobs      every posting seen, deduplicated across sources, with its flags and score
  runs      one row per source run: when, how many fetched, how many new, error if any
  requests  one row per HTTP request: status and latency (this is how we study throttling)
  state     small key/value store: rotation cursors, the "last reviewed" marker
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    dedupe_key  TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    url         TEXT NOT NULL,
    location    TEXT,
    remote      INTEGER,
    posted_at   TEXT,
    description TEXT,
    salary      TEXT,
    tags        TEXT,
    seen_in     TEXT,            -- JSON list of {"source", "url"}: every board it appeared on
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    verdict     TEXT,            -- "fit" | "review" | "out"
    score       INTEGER,
    flags       TEXT             -- JSON: years, region, ml, category, reasons...
);
CREATE INDEX IF NOT EXISTS jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS jobs_verdict ON jobs(verdict);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    fetched     INTEGER DEFAULT 0,
    new         INTEGER DEFAULT 0,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS runs_source ON runs(source, started_at);

CREATE TABLE IF NOT EXISTS requests (
    ts      TEXT NOT NULL,
    source  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    status  INTEGER NOT NULL,     -- 0 = network error
    ms      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_source ON requests(source, ts);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Company boards found by the `discover` source. 'watching' boards are read by their ATS
-- source like the ones in the config; 'rejected' ones had nothing in scope when checked.
CREATE TABLE IF NOT EXISTS job_readings (
    job_id        INTEGER PRIMARY KEY,
    read_at       TEXT NOT NULL,
    model         TEXT NOT NULL,
    eligible      TEXT,
    quote         TEXT,
    visa          TEXT,
    years         INTEGER,
    level         TEXT,
    blockers      TEXT,
    match         INTEGER,
    why           TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL
);

CREATE TABLE IF NOT EXISTS discovered_boards (
    ats        TEXT NOT NULL,
    slug       TEXT NOT NULL,
    status     TEXT NOT NULL,
    found_at   TEXT NOT NULL,
    query      TEXT,
    example    TEXT,
    in_scope   INTEGER,
    PRIMARY KEY (ats, slug)
);
"""

_conn: sqlite3.Connection | None = None
RANK = {"out": 0, "extra": 1, "review": 1, "fit": 2}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path() -> Path:
    return Path(os.environ.get("VIGIA_DB", "data/vigia.db"))


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # timeout: wait for another writer (a manual `vigia run` / `reflag` while `serve`
        # is running) instead of failing with "database is locked".
        _conn = sqlite3.connect(p, timeout=120)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=120000")
        _conn.executescript(SCHEMA)
        from vigia import applications, mail  # their tables live in the same file
        _conn.executescript(applications.SCHEMA)
        _conn.executescript(mail.SCHEMA)
    return _conn


def use(db_path: str | Path) -> None:
    """Point the module at another file (tests use a temp one)."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None
    os.environ["VIGIA_DB"] = str(db_path)


# --- state ---------------------------------------------------------------------

def get_state(key: str, default: str | None = None) -> str | None:
    row = conn().execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    conn().execute("INSERT INTO state(key, value) VALUES (?, ?) "
                   "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn().commit()


# --- requests and runs ---------------------------------------------------------

def log_request(source: str, kind: str, status: int, ms: int) -> None:
    conn().execute("INSERT INTO requests(ts, source, kind, status, ms) VALUES (?, ?, ?, ?, ?)",
                   (now(), source, kind, status, ms))
    conn().commit()


def start_run(source: str) -> int:
    cur = conn().execute("INSERT INTO runs(source, started_at) VALUES (?, ?)", (source, now()))
    conn().commit()
    return cur.lastrowid


def finish_run(run_id: int, fetched: int, new: int, error: str | None = None) -> None:
    conn().execute("UPDATE runs SET finished_at = ?, fetched = ?, new = ?, error = ? WHERE id = ?",
                   (now(), fetched, new, error, run_id))
    conn().commit()


def last_run(source: str) -> sqlite3.Row | None:
    return conn().execute("SELECT * FROM runs WHERE source = ? ORDER BY id DESC LIMIT 1",
                          (source,)).fetchone()


# --- jobs ----------------------------------------------------------------------

def upsert_job(job, verdict: str, score: int, flags: dict) -> bool:
    """Insert a new job or refresh a known one. Returns True if it is new."""
    c = conn()
    ts = now()
    posted = job.posted_at.isoformat(timespec="seconds") if job.posted_at else None
    row = c.execute("SELECT id, seen_in, description, verdict, url FROM jobs WHERE dedupe_key = ?",
                    (job.dedupe_key,)).fetchone()
    if row is None:
        c.execute(
            """INSERT INTO jobs(dedupe_key, source, source_id, title, company, url, location,
                   remote, posted_at, description, salary, tags, seen_in, first_seen, last_seen,
                   verdict, score, flags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job.dedupe_key, job.source, job.source_id, job.title, job.company, job.url,
             job.location, _bool(job.remote), posted, job.description, job.salary,
             json.dumps(job.tags), json.dumps([{"source": job.source, "url": job.url}]),
             ts, ts, verdict, score, json.dumps(flags)))
        c.commit()
        return True

    if job.tags:  # sources may add tags later (e.g. Himalayas restrictions, country lists)
        c.execute("UPDATE jobs SET tags = ? WHERE id = ?", (json.dumps(job.tags), row["id"]))
    if any(t.startswith("countries:") for t in job.tags or []):
        c.execute("UPDATE jobs SET location = ?, verdict = ?, score = ?, flags = ? WHERE id = ?",
                  (job.location, verdict, score, json.dumps(flags), row["id"]))
    seen_in = json.loads(row["seen_in"] or "[]")
    if not any(s["url"] == job.url for s in seen_in):
        seen_in.append({"source": job.source, "url": job.url})
    # The same role can arrive again from another board, or as another country's copy of
    # the posting. Keep the version that is best for the candidate (e.g. the "LATAM" copy
    # over the "Brazil" one); otherwise keep the richer description.
    # A copy without text cannot win: judged blind it often looks "review", above a stored
    # "out" that was judged on the full description (seen 2026-10-16 with Workday re-runs).
    better = (RANK.get(verdict, 0) > RANK.get(row["verdict"], 0)
              and (bool(job.description) or not row["description"]))
    longer = len(job.description or "") > len(row["description"] or "")
    if better:
        c.execute("""UPDATE jobs SET last_seen = ?, seen_in = ?, url = ?, location = ?, remote = ?,
                         description = ?, salary = ?, verdict = ?, score = ?, flags = ? WHERE id = ?""",
                  (ts, json.dumps(seen_in), job.url, job.location, _bool(job.remote),
                   job.description or row["description"], job.salary, verdict, score,
                   json.dumps(flags), row["id"]))
    elif longer and row["url"] == job.url:
        # The same posting came back with its full text (e.g. a LinkedIn card that now has
        # a description): judge it again, even if the new verdict is worse.
        c.execute("""UPDATE jobs SET last_seen = ?, seen_in = ?, description = ?, verdict = ?,
                         score = ?, flags = ? WHERE id = ?""",
                  (ts, json.dumps(seen_in), job.description, verdict, score,
                   json.dumps(flags), row["id"]))
    else:
        c.execute("UPDATE jobs SET last_seen = ?, seen_in = ? WHERE id = ?",
                  (ts, json.dumps(seen_in), row["id"]))
    c.commit()
    return False


def missing_description(source: str, days: int, limit: int) -> list[sqlite3.Row]:
    """Jobs of a source stored without their text (the description request was throttled or
    out of budget), not ruled out, newest first. Jobs someone already applied to or skipped
    are left alone: their text would change nothing."""
    c = conn()
    has_actions = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='actions'").fetchone()
    acted = ("AND id NOT IN (SELECT job_id FROM actions) " if has_actions else "") + \
            "AND id NOT IN (SELECT job_id FROM applications WHERE job_id IS NOT NULL)"
    return c.execute(
        f"""SELECT * FROM jobs WHERE source = ? AND (description IS NULL OR description = '')
            AND verdict != 'out' AND first_seen >= datetime('now', ?) {acted}
            ORDER BY first_seen DESC LIMIT ?""", (source, f"-{int(days)} days", limit)).fetchall()


def known_keys(keys: list[str]) -> set[str]:
    if not keys:
        return set()
    marks = ",".join("?" * len(keys))
    rows = conn().execute(f"SELECT dedupe_key FROM jobs WHERE dedupe_key IN ({marks})", keys)
    return {r["dedupe_key"] for r in rows}


def discovered_slugs(ats: str, status: str | None = "watching") -> list[str]:
    q = "SELECT slug FROM discovered_boards WHERE ats = ?" + (" AND status = ?" if status else "")
    return [r["slug"] for r in conn().execute(q, (ats, status) if status else (ats,))]


def discovered_board(ats: str, slug: str):
    """The discovered_boards row for this board (found_at is when it was last checked), or None."""
    return conn().execute("SELECT * FROM discovered_boards WHERE ats = ? AND slug = ?", (ats, slug.lower())).fetchone()


def save_reading(job_id: int, model: str, r: dict, usage: dict) -> None:
    conn().execute("INSERT OR REPLACE INTO job_readings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (job_id, now(), model, r["ecuador_eligible"], r.get("eligibility_quote", ""), r["visa_sponsorship"],
                    r.get("years_required"), r["level"], json.dumps(r.get("blockers") or []), r["match"], r["why"],
                    usage.get("input_tokens", 0), usage.get("output_tokens", 0), usage.get("cost_usd", 0.0)))
    conn().commit()


def readings(job_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not job_ids:
        return {}
    marks = ",".join("?" * len(job_ids))
    return {r["job_id"]: r for r in conn().execute(f"SELECT * FROM job_readings WHERE job_id IN ({marks})", job_ids)}


def add_discovered(ats: str, slug: str, status: str, query: str, example: str, in_scope: int) -> None:
    conn().execute("INSERT OR REPLACE INTO discovered_boards VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (ats, slug, status, now(), query, example, in_scope))
    conn().commit()


def known_source_ids(source: str, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    rows = conn().execute(f"SELECT source_id FROM jobs WHERE source = ? AND source_id IN ({marks})",
                          [source, *ids])
    return {r["source_id"] for r in rows}


def keys_needing_countries() -> set[str]:
    """Get on Board roles stored before we read their country list."""
    rows = conn().execute("SELECT dedupe_key FROM jobs WHERE location LIKE '%unspecified%'")
    return {r["dedupe_key"] for r in rows}


def jobs_since(since: str) -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM jobs WHERE first_seen > ? ORDER BY score DESC, first_seen DESC",
                          (since,)).fetchall()


def all_jobs() -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM jobs").fetchall()


def merge_duplicates(key_for) -> int:
    """Recompute dedupe keys (after the rule changed) and fold duplicates into one row.
    The kept row is the one with the best verdict; it inherits every board it was seen on."""
    c = conn()
    rows = sorted(all_jobs(), key=lambda r: (-RANK.get(r["verdict"], 0), -(r["score"] or 0), r["id"]))
    keep: dict[str, sqlite3.Row] = {}
    merged = 0
    for r in rows:
        key = key_for(r)
        if key not in keep:
            keep[key] = r
            continue
        k = keep[key]
        seen = json.loads(k["seen_in"] or "[]")
        for s in json.loads(r["seen_in"] or "[]"):
            if not any(x["url"] == s["url"] for x in seen):
                seen.append(s)
        c.execute("UPDATE jobs SET seen_in = ?, first_seen = MIN(first_seen, ?) WHERE id = ?",
                  (json.dumps(seen), r["first_seen"], k["id"]))
        c.execute("DELETE FROM jobs WHERE id = ?", (r["id"],))
        # What the person did with the dropped copy now points at the kept one.
        c.execute("UPDATE applications SET job_id = ? WHERE job_id = ?", (k["id"], r["id"]))
        if c.execute("SELECT 1 FROM sqlite_master WHERE name = 'actions'").fetchone():
            c.execute("UPDATE actions SET job_id = ? WHERE job_id = ?", (k["id"], r["id"]))
        merged += 1
    # Now that every key is unique, store the new keys (two passes avoid UNIQUE clashes).
    for key, r in keep.items():
        c.execute("UPDATE jobs SET dedupe_key = ? WHERE id = ?", (f"tmp:{r['id']}", r["id"]))
    for key, r in keep.items():
        c.execute("UPDATE jobs SET dedupe_key = ? WHERE id = ?", (key, r["id"]))
    c.commit()
    return merged


def update_flags(job_id: int, verdict: str, score: int, flags: dict) -> None:
    conn().execute("UPDATE jobs SET verdict = ?, score = ?, flags = ? WHERE id = ?",
                   (verdict, score, json.dumps(flags), job_id))


def commit() -> None:
    conn().commit()


def _bool(v):
    return None if v is None else int(bool(v))

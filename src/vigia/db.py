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
"""

_conn: sqlite3.Connection | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def path() -> Path:
    return Path(os.environ.get("VIGIA_DB", "data/vigia.db"))


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(p)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
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
    row = c.execute("SELECT id, seen_in, description FROM jobs WHERE dedupe_key = ?",
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

    seen_in = json.loads(row["seen_in"] or "[]")
    if not any(s["source"] == job.source for s in seen_in):
        seen_in.append({"source": job.source, "url": job.url})
    # A richer description from a second source re-flags the job; otherwise keep what we had.
    longer = len(job.description or "") > len(row["description"] or "")
    if longer:
        c.execute("""UPDATE jobs SET last_seen = ?, seen_in = ?, description = ?, verdict = ?,
                         score = ?, flags = ? WHERE id = ?""",
                  (ts, json.dumps(seen_in), job.description, verdict, score,
                   json.dumps(flags), row["id"]))
    else:
        c.execute("UPDATE jobs SET last_seen = ?, seen_in = ? WHERE id = ?",
                  (ts, json.dumps(seen_in), row["id"]))
    c.commit()
    return False


def known_keys(keys: list[str]) -> set[str]:
    if not keys:
        return set()
    marks = ",".join("?" * len(keys))
    rows = conn().execute(f"SELECT dedupe_key FROM jobs WHERE dedupe_key IN ({marks})", keys)
    return {r["dedupe_key"] for r in rows}


def jobs_since(since: str) -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM jobs WHERE first_seen > ? ORDER BY score DESC, first_seen DESC",
                          (since,)).fetchall()


def all_jobs() -> list[sqlite3.Row]:
    return conn().execute("SELECT * FROM jobs").fetchall()


def update_flags(job_id: int, verdict: str, score: int, flags: dict) -> None:
    conn().execute("UPDATE jobs SET verdict = ?, score = ?, flags = ? WHERE id = ?",
                   (verdict, score, json.dumps(flags), job_id))


def commit() -> None:
    conn().commit()


def _bool(v):
    return None if v is None else int(bool(v))

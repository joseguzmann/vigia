"""Applications: the record of every job applied to, and what happened after.

This is the source of truth for the job search. The CLI, the MCP server and the web UI all
read and write through this module.

applications
  id          INTEGER PRIMARY KEY
  number      INTEGER   the person's own running number (#1, #2, ...); assigned when missing
  job_id      INTEGER   jobs.id when Vigía found the posting; NULL for one entered by hand
  company     TEXT      always set, even with a job_id (a job row can be re-titled or merged)
  title       TEXT
  url         TEXT
  applied_at  TEXT      YYYY-MM-DD; NULL while preparing or when unknown
  status      TEXT      see STATUSES
  status_at   TEXT      UTC ISO timestamp of the last status change
  channel     TEXT      where it was sent: company (own board) | linkedin | getonbrd | platform |
                        email | referral | other
  inbound     INTEGER   1 = a recruiter reached out first (does not count as a cold application)
  cv          TEXT      the CV file that was sent
  variant     TEXT      experiment arm: A1 | A2 | baseline | NULL (not part of the experiment)
  family      TEXT      ai | backend | fullstack | frontend | mobile
  level       TEXT      junior | mid | senior | lead | unstated
  extra       INTEGER   1 = role abroad with visa or relocation
  age_days    INTEGER   age of the posting when applying
  material    TEXT      what was written for it: letter, questions, ... (free text)
  docs        TEXT      where the drafts live (a path in the person's own notes)
  notes       TEXT      free text
  created_at  TEXT

application_events
  id, application_id, ts, status, note, origin (email | user | assistant | import)
  Every status change, so the funnel and "a person replied within N days" can be measured.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from vigia import db

STATUSES = ("preparing", "applied", "ack", "screen", "technical", "final", "offer",
            "rejected", "ghost", "closed")
OPEN = frozenset({"preparing", "applied", "ack", "screen", "technical", "final"})
POSITIVE = frozenset({"screen", "technical", "final", "offer"})
VARIANTS = ("A1", "A2", "baseline")
FAMILIES = ("ai", "backend", "fullstack", "frontend", "mobile")
CHANNELS = ("company", "linkedin", "getonbrd", "platform", "email", "referral", "other")
ORIGINS = ("email", "user", "assistant", "import")
SIGNAL_WINDOW_DAYS = 14

FIELDS = ("number", "job_id", "company", "title", "url", "applied_at", "status", "channel",
          "inbound", "cv", "variant", "family", "level", "extra", "age_days", "material",
          "docs", "notes")
EDITABLE = tuple(f for f in FIELDS if f != "status")

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id         INTEGER PRIMARY KEY,
    number     INTEGER,
    job_id     INTEGER,
    company    TEXT NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT,
    applied_at TEXT,
    status     TEXT NOT NULL,
    status_at  TEXT NOT NULL,
    channel    TEXT,
    inbound    INTEGER NOT NULL DEFAULT 0,
    cv         TEXT,
    variant    TEXT,
    family     TEXT,
    level      TEXT,
    extra      INTEGER NOT NULL DEFAULT 0,
    age_days   INTEGER,
    material   TEXT,
    docs       TEXT,
    notes      TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS applications_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS applications_number ON applications(number);

CREATE TABLE IF NOT EXISTS application_events (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL,
    ts             TEXT NOT NULL,
    status         TEXT NOT NULL,
    note           TEXT,
    origin         TEXT
);
CREATE INDEX IF NOT EXISTS application_events_app ON application_events(application_id, ts);
"""


def _c(c: sqlite3.Connection | None) -> sqlite3.Connection:
    return c if c is not None else db.conn()


def _check(field: str, value, allowed) -> None:
    if value not in (None, "") and value not in allowed:
        raise ValueError(f"{field} must be one of {', '.join(allowed)} (got {value!r})")


def _date(value) -> str | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10]).isoformat()


def _posting_age(job: sqlite3.Row, on: str | None) -> int | None:
    if not job["posted_at"]:
        return json.loads(job["flags"] or "{}").get("age_days")
    try:
        posted = datetime.fromisoformat(job["posted_at"])
    except ValueError:
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    when = datetime.fromisoformat(on).replace(tzinfo=timezone.utc) if on else datetime.now(timezone.utc)
    return max(0, (when.date() - posted.date()).days)


def find_job(url: str, c: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    """The stored job for a posting URL: its main URL or any board it was seen on."""
    c = _c(c)
    url = (url or "").strip().split("?")[0].rstrip("/")
    if not url:
        return None
    row = c.execute("SELECT * FROM jobs WHERE rtrim(url, '/') = ? OR url LIKE ?",
                    (url, url + "?%")).fetchone()
    if row:
        return row
    return c.execute("SELECT * FROM jobs WHERE seen_in LIKE ?", (f'%"{url}%',)).fetchone()


def next_number(c: sqlite3.Connection | None = None) -> int:
    row = _c(c).execute("SELECT MAX(number) FROM applications").fetchone()
    return (row[0] or 0) + 1


def next_variant(c: sqlite3.Connection | None = None) -> dict:
    """The experiment rotation: even numbers get A2 (CV for the role's family), odd get A1."""
    n = next_number(c)
    return {"number": n, "variant": "A2" if n % 2 == 0 else "A1"}


def add(company: str = "", title: str = "", url: str = "", job_id: int | None = None,
        status: str = "applied", applied_at: str | None = None, origin: str = "assistant",
        note: str = "", c: sqlite3.Connection | None = None, **fields) -> dict:
    """Record an application. With `job_id` or a `url` Vigía knows, company, title and the
    posting's age are filled from the stored job. Returns the stored application."""
    c = _c(c)
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")
    _check("status", status, STATUSES)
    _check("variant", fields.get("variant"), VARIANTS)
    _check("family", fields.get("family"), FAMILIES)
    _check("channel", fields.get("channel"), CHANNELS)
    _check("origin", origin, ORIGINS)

    job = None
    if job_id is not None:
        job = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise LookupError(f"no job {job_id}")
    elif url:
        job = find_job(url, c)
    if job is not None:
        flags = json.loads(job["flags"] or "{}")
        company = company or job["company"]
        title = title or job["title"]
        url = url or job["url"]
        fields.setdefault("level", flags.get("level"))
        if fields.get("extra") is None and job["verdict"] == "extra":
            fields["extra"] = 1
    if not company or not title:
        raise ValueError("company and title are required when the job is not in Vigía")

    if applied_at is None and status != "preparing" and origin != "import":
        applied_at = date.today().isoformat()  # an imported row may honestly not know its date
    applied_at = _date(applied_at)
    if job is not None and fields.get("age_days") is None:
        fields["age_days"] = _posting_age(job, applied_at)
    if fields.get("number") is None:
        fields["number"] = next_number(c)

    ts = db.now()
    row = {**{f: None for f in EDITABLE}, **fields, "job_id": job["id"] if job else None,
           "company": company.strip(), "title": title.strip(), "url": url or None,
           "applied_at": applied_at}
    row["inbound"] = int(bool(row["inbound"]))
    row["extra"] = int(bool(row["extra"]))
    cols = list(EDITABLE) + ["status", "status_at", "created_at"]
    cur = c.execute(f"INSERT INTO applications({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    [row[f] for f in EDITABLE] + [status, ts, ts])
    app_id = cur.lastrowid
    c.execute("INSERT INTO application_events(application_id, ts, status, note, origin) VALUES (?, ?, ?, ?, ?)",
              (app_id, f"{applied_at}T12:00:00+00:00" if applied_at else ts, status, note or None, origin))
    c.commit()
    return get(app_id=app_id, c=c)


def _resolve(number: int | None, app_id: int | None, c: sqlite3.Connection) -> sqlite3.Row:
    if app_id is not None:
        row = c.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    elif number is not None:
        rows = c.execute("SELECT * FROM applications WHERE number = ? ORDER BY id", (number,)).fetchall()
        if len(rows) > 1:
            raise ValueError(f"#{number} is used by {len(rows)} applications "
                             f"(ids {', '.join(str(r['id']) for r in rows)}): pass app_id")
        row = rows[0] if rows else None
    else:
        raise ValueError("pass number or app_id")
    if row is None:
        raise LookupError(f"no application {'#' + str(number) if number is not None else app_id}")
    return row


def get(number: int | None = None, app_id: int | None = None, c: sqlite3.Connection | None = None) -> dict:
    c = _c(c)
    row = _resolve(number, app_id, c)
    out = dict(row)
    out["events"] = [dict(e) for e in c.execute(
        "SELECT ts, status, note, origin FROM application_events WHERE application_id = ? ORDER BY ts, id",
        (row["id"],))]
    out["signal_days"] = _signal_days(out)
    return out


def set_status(status: str, number: int | None = None, app_id: int | None = None, note: str = "",
               origin: str = "assistant", on: str | None = None, c: sqlite3.Connection | None = None) -> dict:
    """Move an application to a new status and log the event. `on` backdates it (YYYY-MM-DD)."""
    c = _c(c)
    _check("status", status, STATUSES)
    _check("origin", origin, ORIGINS)
    row = _resolve(number, app_id, c)
    ts = f"{_date(on)}T12:00:00+00:00" if on else db.now()
    c.execute("INSERT INTO application_events(application_id, ts, status, note, origin) VALUES (?, ?, ?, ?, ?)",
              (row["id"], ts, status, note or None, origin))
    sets = "status = ?, status_at = ?"
    args: list = [status, ts]
    if status != "preparing" and not row["applied_at"] and origin != "import":
        sets += ", applied_at = ?"
        args.append(ts[:10])
    c.execute(f"UPDATE applications SET {sets} WHERE id = ?", args + [row["id"]])
    c.commit()
    return get(app_id=row["id"], c=c)


def update(number: int | None = None, app_id: int | None = None, c: sqlite3.Connection | None = None,
           **fields) -> dict:
    """Change recorded details (not the status: use set_status, so the change is logged)."""
    c = _c(c)
    bad = set(fields) - set(EDITABLE)
    if bad:
        raise ValueError(f"cannot update: {', '.join(sorted(bad))}")
    _check("variant", fields.get("variant"), VARIANTS)
    _check("family", fields.get("family"), FAMILIES)
    _check("channel", fields.get("channel"), CHANNELS)
    row = _resolve(number, app_id, c)
    if "applied_at" in fields:
        fields["applied_at"] = _date(fields["applied_at"])
    for b in ("inbound", "extra"):
        if b in fields:
            fields[b] = int(bool(fields[b]))
    if fields:
        c.execute(f"UPDATE applications SET {', '.join(f + ' = ?' for f in fields)} WHERE id = ?",
                  list(fields.values()) + [row["id"]])
        c.commit()
    return get(app_id=row["id"], c=c)


def delete(number: int | None = None, app_id: int | None = None, c: sqlite3.Connection | None = None) -> None:
    c = _c(c)
    row = _resolve(number, app_id, c)
    c.execute("DELETE FROM application_events WHERE application_id = ?", (row["id"],))
    c.execute("DELETE FROM applications WHERE id = ?", (row["id"],))
    c.commit()


def list_apps(status: str = "", open_only: bool = False, q: str = "", limit: int = 500,
              c: sqlite3.Connection | None = None) -> list[dict]:
    """Applications, newest number first."""
    c = _c(c)
    sql, args = "SELECT * FROM applications WHERE 1=1", []
    if status:
        _check("status", status, STATUSES)
        sql += " AND status = ?"
        args.append(status)
    if open_only:
        sql += f" AND status IN ({','.join('?' * len(OPEN))})"
        args += sorted(OPEN)
    if q.strip():
        sql += " AND (company LIKE ? OR title LIKE ? OR notes LIKE ?)"
        args += [f"%{q.strip()}%"] * 3
    sql += " ORDER BY COALESCE(number, 0) DESC, id DESC LIMIT ?"
    rows = [dict(r) for r in c.execute(sql, args + [limit])]
    firsts = _first_positive(c)
    for r in rows:
        r["signal_days"] = _signal_days(r, firsts.get(r["id"]))
    return rows


def for_job(job_id: int, c: sqlite3.Connection | None = None) -> list[dict]:
    return [dict(r) for r in _c(c).execute("SELECT * FROM applications WHERE job_id = ? ORDER BY id", (job_id,))]


def applied_job_ids(c: sqlite3.Connection | None = None) -> set[int]:
    return {r[0] for r in _c(c).execute("SELECT job_id FROM applications WHERE job_id IS NOT NULL")}


# --- the experiment ---------------------------------------------------------------

def _first_positive(c: sqlite3.Connection) -> dict[int, str]:
    q = f"""SELECT application_id, MIN(ts) FROM application_events
            WHERE status IN ({','.join('?' * len(POSITIVE))}) GROUP BY application_id"""
    return dict(c.execute(q, sorted(POSITIVE)).fetchall())


def _signal_days(app: dict, first_positive: str | None = None) -> int | None:
    """Days from applying to the first step with a person (screen or further)."""
    if first_positive is None and "events" in app:
        hits = [e["ts"] for e in app["events"] if e["status"] in POSITIVE]
        first_positive = min(hits) if hits else None
    if not first_positive or not app.get("applied_at"):
        return None
    return max(0, (date.fromisoformat(first_positive[:10]) - date.fromisoformat(app["applied_at"])).days)


def _bucket(age) -> str:
    if age is None:
        return "no date"
    return "0-2 days" if age <= 2 else "3-7 days" if age <= 7 else "8-14 days" if age <= 14 else "15+ days"


def stats(c: sqlite3.Connection | None = None) -> dict:
    """Cold applications only (inbound and still-preparing ones are left out).

    A success is a step with a person within SIGNAL_WINDOW_DAYS of applying. `pending` are
    applications younger than the window with no signal yet: they can still turn positive.
    """
    c = _c(c)
    today = date.today()
    apps = [a for a in list_apps(c=c) if not a["inbound"] and a["status"] != "preparing"]

    def row(group: list[dict]) -> dict:
        ok = [a for a in group if a["signal_days"] is not None and a["signal_days"] <= SIGNAL_WINDOW_DAYS]
        pending = [a for a in group if a not in ok and a["applied_at"]
                   and (today - date.fromisoformat(a["applied_at"])).days < SIGNAL_WINDOW_DAYS
                   and a["status"] in OPEN]
        decided = len(group) - len(pending)
        return {"applied": len(group), "signals": len(ok), "pending": len(pending),
                "rate": len(ok) / decided if decided else None}

    def by(key, order=()):
        groups: dict[str, list[dict]] = {}
        for a in apps:
            groups.setdefault(str(key(a)), []).append(a)
        rank = {v: i for i, v in enumerate(order)}
        return [{"key": k, **row(groups[k])} for k in sorted(groups, key=lambda k: (rank.get(k, len(rank)), k))]

    regular = [a for a in apps if not a["extra"]]
    statuses: dict[str, int] = {}
    for a in list_apps(c=c):
        statuses[a["status"]] = statuses.get(a["status"], 0) + 1
    return {
        "window_days": SIGNAL_WINDOW_DAYS,
        "total": row(apps),
        "status_counts": [{"key": s, "n": statuses[s]} for s in STATUSES if s in statuses],
        "groups": {
            "variant (visa roles left out)": [{"key": k, **row([a for a in regular if (a["variant"] or "none") == k])}
                                              for k in [*VARIANTS, "none"]
                                              if any((a["variant"] or "none") == k for a in regular)],
            "visa / relocation": by(lambda a: "visa/relocation" if a["extra"] else "regular", ("regular",)),
            "family": by(lambda a: a["family"] or "?", FAMILIES),
            "level": by(lambda a: a["level"] or "?", ("junior", "mid", "senior", "lead", "unstated")),
            "channel": by(lambda a: a["channel"] or "?", CHANNELS),
            "freshness at apply time": by(lambda a: _bucket(a["age_days"]),
                                          ("0-2 days", "3-7 days", "8-14 days", "15+ days", "no date")),
        },
    }


# --- import / export ----------------------------------------------------------------

def import_rows(rows: list[dict], c: sqlite3.Connection | None = None) -> int:
    """Bulk load (e.g. from an older spreadsheet). Each row: the FIELDS, `status`, and optionally
    `events` [{ts|on, status, note}]. Rows whose number+company already exist are skipped."""
    c = _c(c)
    n = 0
    for r in rows:
        r = dict(r)
        if r.get("number") is not None and c.execute(
                "SELECT 1 FROM applications WHERE number = ? AND company = ?",
                (r["number"], r.get("company"))).fetchone():
            continue
        events = r.pop("events", None) or []
        status = r.pop("status", "applied")
        first = events[0]["status"] if events else status
        app = add(status=first, origin="import", note=(events[0].get("note") if events else "") or "",
                  c=c, **r)
        for e in events[1:]:
            set_status(e["status"], app_id=app["id"], note=e.get("note") or "", origin="import",
                       on=e.get("on"), c=c)
        if get(app_id=app["id"], c=c)["status"] != status:
            set_status(status, app_id=app["id"], origin="import", c=c)
        n += 1
    return n


def export_markdown(c: sqlite3.Connection | None = None) -> str:
    """A readable snapshot of every application, for keeping in a notes repository."""
    c = _c(c)
    apps = sorted(list_apps(c=c), key=lambda a: (a["number"] or 0, a["id"]))
    s = stats(c)
    t = s["total"]
    lines = [
        "# Applications",
        "",
        f"Exported from Vigía on {datetime.now(timezone.utc).date().isoformat()}. The database is the "
        "source of truth: change things there (MCP, CLI or web UI), not in this file.",
        "",
        f"Cold applications: {t['applied']} · step with a person within {s['window_days']} days: "
        f"{t['signals']} · still inside the window: {t['pending']}",
        "",
        "| # | applied | company | role | status | channel | cv | variant | family | level | extra | age | notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def cell(v) -> str:
        return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")

    for a in apps:
        role = f"[{cell(a['title'])}]({a['url']})" if a["url"] else cell(a["title"])
        flags = ("visa" if a["extra"] else "") + (" inbound" if a["inbound"] else "")
        notes = cell(a["notes"]) + (f" Drafts: `{a['docs']}`." if a["docs"] else "")
        lines.append(f"| {cell(a['number'])} | {cell(a['applied_at'])} | {cell(a['company'])} | {role} | "
                     f"{a['status']} | {cell(a['channel'])} | {cell(a['cv'])} | {cell(a['variant'])} | "
                     f"{cell(a['family'])} | {cell(a['level'])} | {flags.strip()} | "
                     f"{cell(a['age_days'])} | {notes.strip()} |")
    lines += ["", "## Status history", ""]
    for a in apps:
        ev = c.execute("SELECT ts, status, note, origin FROM application_events WHERE application_id = ? "
                       "ORDER BY ts, id", (a["id"],)).fetchall()
        steps = " → ".join(f"{e['status']} ({e['ts'][:10]})" for e in ev)
        lines.append(f"- **#{a['number']} {cell(a['company'])}**: {steps}")
    return "\n".join(lines) + "\n"

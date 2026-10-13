"""What a person reads: new jobs since the last review, and the health of the collector."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from vigia import config, db
from vigia.sources import REGISTRY

VERDICT_TITLES = {"fit": "Worth applying", "review": "Worth a look", "out": "Filtered out"}


def last_review() -> str:
    default = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    return db.get_state("review.last", default)


def mark_reviewed() -> None:
    db.set_state("review.last", db.now())


def new_jobs(since: str | None = None, include_out: bool = False, limit: int = 60) -> str:
    since = since or last_review()
    rows = db.jobs_since(since)
    groups = defaultdict(list)
    for r in rows:
        groups[r["verdict"]].append(r)

    lines = [f"# Vigía — new since {_local(since)}", "",
             f"{len(rows)} new jobs: " + ", ".join(
                 f"{len(groups[v])} {VERDICT_TITLES[v].lower()}" for v in ("fit", "review", "out")), ""]
    for verdict in ("fit", "review"):
        items = groups[verdict][:limit]
        if not items:
            continue
        lines += [f"## {VERDICT_TITLES[verdict]} ({len(groups[verdict])})", ""]
        lines += [_job_block(r) for r in items]

    out_rows = groups["out"]
    if out_rows:
        reasons = Counter()
        for r in out_rows:
            for reason in json.loads(r["flags"] or "{}").get("reasons", []) or ["(no reason)"]:
                reasons[_reason_kind(reason)] += 1
        lines += [f"## {VERDICT_TITLES['out']} ({len(out_rows)})", "",
                  "By reason: " + ", ".join(f"{k} {n}" for k, n in reasons.most_common()), ""]
        if include_out:
            lines += [_job_block(r) for r in out_rows[:limit]]
    return "\n".join(lines)


def _job_block(r) -> str:
    f = json.loads(r["flags"] or "{}")
    seen = json.loads(r["seen_in"] or "[]")
    meta = [r["location"] or "location n/a"]
    if r["salary"]:
        meta.append(r["salary"])
    if f.get("years") is not None:
        meta.append(f"{f['years']}+ yrs")
    meta.append(f.get("category", ""))
    if r["posted_at"]:
        meta.append("posted " + _local(r["posted_at"])[:10])
    meta.append("via " + ", ".join(s["source"] for s in seen))
    block = [f"### {r['title']} — {r['company']}  `{r['score']}`",
             " · ".join(m for m in meta if m)]
    if f.get("stack"):
        block.append("stack: " + ", ".join(f["stack"]))
    if f.get("reasons"):
        block.append("note: " + "; ".join(f["reasons"]))
    block += [f"<{r['url']}>", ""]
    return "\n".join(block)


def health() -> str:
    lines = ["# Vigía — health", ""]
    hb = db.get_state("heartbeat")
    lines.append(f"Last loop: {_local(hb) if hb else 'never'}  ·  config: {config.load()['_path']}")
    lines += ["", "| source | enabled | last run | fetched | new | error |", "|---|---|---|---|---|---|"]
    for name in REGISTRY:
        last = db.last_run(name)
        on = "yes" if config.source(name).get("enabled") else "no"
        if last is None:
            lines.append(f"| {name} | {on} | never | | | |")
        else:
            lines.append(f"| {name} | {on} | {_local(last['started_at'])} | {last['fetched']} | "
                         f"{last['new']} | {last['error'] or ''} |")
    total = db.conn().execute("SELECT verdict, COUNT(*) n FROM jobs GROUP BY verdict").fetchall()
    lines += ["", "Jobs stored: " + ", ".join(f"{r['verdict']} {r['n']}" for r in total)]
    return "\n".join(lines)


def is_healthy(max_age_minutes: int = 30) -> bool:
    hb = db.get_state("heartbeat")
    if not hb:
        return False
    return datetime.now(timezone.utc) - datetime.fromisoformat(hb) < timedelta(minutes=max_age_minutes)


def throttling(source: str = "linkedin", hours: int = 72) -> str:
    """Request outcomes per hour, and the 429 episodes with how long they lasted."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = db.conn().execute("SELECT ts, status FROM requests WHERE source = ? AND ts > ? ORDER BY ts",
                             (source, since)).fetchall()
    if not rows:
        return f"No {source} requests in the last {hours} h."
    per_hour = defaultdict(Counter)
    for r in rows:
        per_hour[_local(r["ts"])[:13]][_status_kind(r["status"])] += 1
    lines = [f"# {source} requests, last {hours} h", "",
             "| hour | ok | 429 | other |", "|---|---|---|---|"]
    for hour, c in sorted(per_hour.items()):
        lines.append(f"| {hour}h | {c['ok']} | {c['429']} | {c['other']} |")

    episodes, start, last_bad = [], None, None
    for r in rows:
        if r["status"] == 429:
            start = start or r["ts"]
            last_bad = r["ts"]
        elif r["status"] == 200 and start:
            episodes.append((start, last_bad, r["ts"]))
            start = None
    lines += ["", "429 episodes (first 429 → first success after it):"]
    if not episodes and not start:
        lines.append("none")
    for first, _, recovered in episodes:
        minutes = (datetime.fromisoformat(recovered) - datetime.fromisoformat(first)).total_seconds() / 60
        lines.append(f"- {_local(first)} → {_local(recovered)}  ({minutes:.0f} min)")
    if start:
        lines.append(f"- {_local(start)} → still throttled at last request")
    return "\n".join(lines)


def _status_kind(status: int) -> str:
    return "ok" if status == 200 else "429" if status == 429 else "other"


def _reason_kind(reason: str) -> str:
    if reason.startswith("asks "):
        return "too many years"
    return reason.split(":")[0].split("(")[0].strip()


def _local(iso: str) -> str:
    """UTC ISO → the container's local time (set TZ), short format."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso

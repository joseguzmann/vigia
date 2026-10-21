"""Run sources: fetch → flag → store. And the scheduler loop that runs them on their own clocks."""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, timedelta, timezone

from vigia import config, db, flags, http
from vigia.linkedin_guest import Blocked
from vigia.sources import REGISTRY

log = logging.getLogger("vigia")
TICK_SECONDS = 60


def run_source(name: str, src=None) -> dict:
    """Fetch one source, flag every job, store it. Returns a small summary.
    `src` is a source already built with its own settings (a manual run); default: the config's."""
    cfg = config.source(name)
    run_id = db.start_run(name)
    fetched = new = 0
    error = None
    try:
        jobs = (src or REGISTRY[name](cfg)).fetch()
        fetched = len(jobs)
        profile = config.profile()
        for job in jobs:
            if not job.title or not job.url:
                continue
            verdict, score, f = flags.evaluate(job, profile)
            if db.upsert_job(job, verdict, score, f):
                new += 1
    except http.Throttled as e:
        error = f"throttled: {e}"
    except Blocked as e:
        error = f"skipped: {e}"
    except Exception as e:  # one broken source must not stop the others
        log.exception("source %s failed", name)
        error = f"{type(e).__name__}: {e}"
    db.finish_run(run_id, fetched, new, error)
    summary = {"source": name, "fetched": fetched, "new": new, "error": error}
    log.info("%s", summary)
    return summary


def reflag() -> dict:
    """Re-judge every stored job with the current config. Collection is never repeated:
    change the profile, run this, and the whole history is re-ranked."""
    from vigia.models import Job
    from vigia.sources.base import parse_date

    profile = config.profile()
    changed = 0
    rows = db.all_jobs()
    for r in rows:
        job = Job(source=r["source"], source_id=r["source_id"], title=r["title"],
                  company=r["company"], url=r["url"], location=r["location"] or "",
                  remote=None if r["remote"] is None else bool(r["remote"]),
                  posted_at=parse_date(r["posted_at"]), description=r["description"] or "",
                  salary=r["salary"] or "", tags=json.loads(r["tags"] or "[]"))
        verdict, score, f = flags.evaluate(job, profile)
        if verdict != r["verdict"] or score != r["score"]:
            changed += 1
        db.update_flags(r["id"], verdict, score, f)
        if r["id"] % 500 == 0:
            db.commit()  # short transactions: never hold the write lock for long
    db.commit()
    from vigia.models import dedupe_key
    merged = db.merge_duplicates(lambda r: dedupe_key(r["company"], r["title"]))
    return {"jobs": len(rows), "changed": changed, "merged_duplicates": merged}


def enabled_sources() -> list[str]:
    return [n for n in REGISTRY if config.source(n).get("enabled")]


def due(name: str, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    cfg = config.source(name)
    src_cls = REGISTRY.get(name)
    if src_cls is not None and hasattr(src_cls, "due_at"):  # sources that pace themselves
        return src_cls(cfg).due_at(now)
    window = cfg.get("hours_utc")  # [start, end): run only inside this UTC hour range
    if window and not (window[0] <= now.hour < window[1]):
        return False
    last = db.last_run(name)
    if last is None:
        return True
    every = int(cfg.get("every_minutes", 240))
    started = datetime.fromisoformat(last["started_at"])
    return now - started >= timedelta(minutes=every)


def serve() -> None:
    """Long-running loop for the container: run each enabled source when it is due."""
    stop = {"now": False}

    def _stop(*_):
        stop["now"] = True
        log.info("stopping after the current source")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("vigia serving with config %s; sources: %s",
             config.load()["_path"], ", ".join(enabled_sources()))
    while not stop["now"]:
        for name in enabled_sources():
            if stop["now"]:
                break
            if due(name):
                run_source(name)
        db.set_state("heartbeat", db.now())
        for _ in range(TICK_SECONDS):
            if stop["now"]:
                break
            time.sleep(1)

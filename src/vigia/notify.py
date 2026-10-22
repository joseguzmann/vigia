"""Push a phone notification for each new "fit" job, through an ntfy server.

Configured by environment: NTFY_URL (the topic URL, e.g. https://ntfy.example.com/vigia) and
NTFY_TOKEN (an access token allowed to publish there). Without them nothing is sent.
Quiet hours (config `notify.quiet_hours`, local time) send nothing: the jobs are still in
new_jobs the next morning. At most `notify.max_per_run` messages per source run; the rest go
into one summary line, so a big discovery run cannot flood the phone.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from vigia import config, http

log = logging.getLogger("vigia")


def quiet(now: datetime | None = None) -> bool:
    cfg = config.load().get("notify", {})
    start, end = cfg.get("quiet_hours", [22, 8])
    hour = (now or datetime.now(ZoneInfo(cfg.get("timezone", "America/Guayaquil")))).hour
    return hour >= start or hour < end if start > end else start <= hour < end


def send(title: str, body: str, url: str = "", tags: str = "briefcase") -> bool:
    topic, token = os.environ.get("NTFY_URL"), os.environ.get("NTFY_TOKEN")
    if not topic or not token:
        return False
    headers = {"Authorization": f"Bearer {token}", "Title": title.encode("utf-8"), "Tags": tags}
    if url:
        headers["Click"] = url
    try:
        resp = http._session.post(topic, data=body.encode("utf-8"), headers=headers, timeout=15)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 — a missed notification must not break a run
        log.warning("ntfy: could not send %r", title)
        return False


def new_fits(source: str, jobs: list[tuple]) -> int:
    """jobs: (Job, score) pairs that are new and judged "fit". Returns messages sent."""
    if not jobs or quiet():
        return 0
    cap = int(config.load().get("notify", {}).get("max_per_run", 5))
    jobs = sorted(jobs, key=lambda js: -js[1])
    sent = 0
    for job, score in jobs[:cap]:
        sent += send(f"New fit: {job.title[:80]}", f"{job.company} · {job.location or 'location n/a'} · score {score} · {source}",
                     job.url)
    if len(jobs) > cap:
        sent += send(f"+{len(jobs) - cap} more new fits", f"from {source}; see new_jobs", tags="mag")
    return sent

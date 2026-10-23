"""freehire.me: an open index of jobs read from 100+ ATS, aggregators and Telegram channels.

Its public API (no key, rate-limited per address, personal use) already normalizes where a job
can be done from: `countries` (ISO codes it accepts), `regions` (latam, global, ...) and
`work_mode`. Each query in `searches` is a set of those filters; the source pages through the
jobs posted in the last `open_within_days` days. Closed postings are left out by the API.
"""

from __future__ import annotations

import re

from vigia import http
from vigia.models import Job
from vigia.sources.base import Source, parse_date

API = "https://freehire.me/api/v1/jobs/search"
PAGE = 100
TECH = "ai_engineering,ml_ai,backend,fullstack,frontend,software_engineering"
DEFAULT_SEARCHES = [
    {"countries": "ec", "work_mode": "remote"},          # names Ecuador among the eligible countries
    {"regions": "latam", "work_mode": "remote", "category": TECH},
    {"regions": "global", "work_mode": "remote", "category": TECH},
]


def location_of(j: dict) -> str:
    """The posting's own location text, with what freehire knows about eligibility made explicit,
    so the rules (and the reader) see "Ecuador" or "Worldwide" when that is what it accepts."""
    loc = (j.get("location") or "").strip()
    countries, regions = j.get("countries") or [], j.get("regions") or []
    if "ec" in countries and "ecuador" not in loc.lower():
        loc = f"{loc} (eligible: Ecuador)".strip()
    elif not countries and "global" in regions and not re.search(r"worldwide|anywhere", loc, re.I):
        loc = f"{loc} (Worldwide)".strip()
    return loc


def to_job(j: dict, source: str) -> Job:
    e = j.get("enrichment") or {}
    salary = ""
    if e.get("salary_min"):
        salary = (f"{e.get('salary_currency') or ''} {e['salary_min']:,}–{e.get('salary_max') or e['salary_min']:,}"
                  f"/{e.get('salary_period') or 'year'}").strip()
    reality = j.get("reality") or {}
    tags = [f"via:{j.get('source', '')}"] + ([f"reality:{reality['class']}"] if reality.get("class") else [])
    if reality.get("repost_count", 0) > 1:
        tags.append(f"reposted:{reality['repost_count']}")
    return Job(
        source=source, source_id=j["public_slug"], title=j.get("title", ""), company=j.get("company", ""),
        url=re.sub(r"[?&]utm_source=freehire\.me$", "", j.get("url", "")), location=location_of(j),
        remote=j.get("work_mode") == "remote", posted_at=parse_date(j.get("posted_at")),
        description=j.get("description") or "", salary=salary, tags=tags,
    )


class FreeHire(Source):
    name = "freehire"

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        days = int(self.cfg.get("open_within_days", 2))
        for search in self.cfg.get("searches") or DEFAULT_SEARCHES:
            offset = 0
            while offset < int(self.cfg.get("max_per_search", 500)):
                data = http.get_json(self.name, "search", API,
                                     params={**search, "open_within_days": days, "limit": PAGE, "offset": offset})
                rows = (data or {}).get("data") or []
                for j in rows:
                    if j.get("public_slug"):
                        jobs[j["public_slug"]] = to_job(j, self.name)
                if len(rows) < PAGE:
                    break
                offset += PAGE
        return list(jobs.values())

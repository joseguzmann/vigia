"""RemoteYeah: remote developer jobs, many of them open to Latin America.

No feed or API. Its listing pages (by region and by skill: /remote-jobs-in-latin-america,
/remote-python-jobs, ...) are server-rendered with the 25 newest jobs as cards, and every job
page embeds a schema.org JobPosting with the countries the role accepts. So each run reads
the listings, keeps the titles in scope that are not stored yet, and reads only those pages.
"""

from __future__ import annotations

import html
import re

from vigia import config, db, http
from vigia.flags import title_in_scope
from vigia.models import Job
from vigia.sources.base import Source, job_posting_ld, ld_to_job

BASE = "https://remoteyeah.com"
CARD_RE = re.compile(r'<a href="(https://remoteyeah\.com/jobs/([a-z0-9-]+))"\s+class="job-card-title"[^>]*>(.*?)</a>', re.S)


def cards(page: str) -> list[tuple[str, str, str]]:
    """(slug, url, title) of every job card on a listing page."""
    return [(slug, url, html.unescape(re.sub(r"<[^>]+>", "", title)).strip())
            for url, slug, title in CARD_RE.findall(page)]


class RemoteYeah(Source):
    name = "remoteyeah"

    def fetch(self) -> list[Job]:
        profile = config.profile()
        found: dict[str, tuple[str, str]] = {}
        for listing in self.cfg.get("listings", ["remote-jobs-in-latin-america"]):
            resp = http.get(self.name, "listing", f"{BASE}/{listing}")
            for slug, url, title in cards(resp.text if resp is not None else ""):
                if title_in_scope(title, profile)[0]:
                    found.setdefault(slug, (url, title))

        known = db.known_source_ids(self.name, list(found))
        out = []
        for slug, (url, _) in list(found.items())[: int(self.cfg.get("max_details", 40))]:
            if slug in known:
                continue
            resp = http.get(self.name, "job", url)
            ld = job_posting_ld(resp.text) if resp is not None else None
            if ld:
                out.append(ld_to_job(self.name, slug, url, ld))
        return out

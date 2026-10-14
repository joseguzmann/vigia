"""Strider (onstrider.com) — places LATAM engineers with US companies. Every role is for LATAM.

The list page links each role; each role page embeds a schema.org JobPosting with the exact
countries it accepts (`applicantLocationRequirements`), which becomes the job's location.
"""

from __future__ import annotations

import re

from vigia import db, http
from vigia.models import Job
from vigia.sources.base import Source, job_posting_ld, parse_date, strip_html

BASE = "https://www.onstrider.com"


class Strider(Source):
    name = "strider"

    def fetch(self) -> list[Job]:
        resp = http.get(self.name, "list", f"{BASE}/jobs")
        if resp is None:
            return []
        slugs = list(dict.fromkeys(re.findall(r'href="/jobs/([a-z0-9-]+)"', resp.text)))
        out = []
        for slug in slugs:
            url = f"{BASE}/jobs/{slug}"
            page = http.get(self.name, "job", url)
            if page is None:
                continue
            ld = job_posting_ld(page.text)
            if not ld:
                continue
            countries = ld.get("_countries") or []
            out.append(Job(
                source=self.name, source_id=slug, title=ld.get("title", ""), company="Strider (client)",
                url=url,
                location=("LATAM: " + ", ".join(countries)) if countries else "LATAM",
                remote=ld.get("jobLocationType") == "TELECOMMUTE" or None,
                posted_at=parse_date(ld.get("datePosted")),
                description=strip_html(ld.get("description")),
                tags=["countries:" + ",".join(countries)] if countries else [],
            ))
        return out

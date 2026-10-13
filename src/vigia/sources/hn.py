"""Hacker News "Ask HN: Who is hiring?" — the monthly thread, via the public Algolia API.

Each top-level comment is one posting. By convention the first line reads
"Company | Role | Location | Remote | ...", which we split into fields; the whole
comment becomes the description.
"""

from __future__ import annotations

import re

from vigia import http
from vigia.models import Job
from vigia.sources.base import Source, parse_date, strip_html

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://hn.algolia.com/api/v1/items/{}"
ROLE_RE = re.compile(r"engineer|developer|scientist|architect|lead|founding|sre|devops|"
                     r"programmer|cto|staff|backend|frontend|full.?stack|ml|ai", re.I)
PLACE_RE = re.compile(r"remote|onsite|on-site|hybrid|\b[A-Z][a-z]+,\s*[A-Z]{2}\b|"
                      r"usa|us\b|europe|uk|canada|worldwide|anywhere|americas|latam", re.I)


class HNWhoIsHiring(Source):
    name = "hn_whoishiring"

    def fetch(self) -> list[Job]:
        hits = (http.get_json(self.name, "thread", SEARCH, params={
            "tags": "story,author_whoishiring", "hitsPerPage": 6}) or {}).get("hits", [])
        thread = next((h for h in hits if h.get("title", "").startswith("Ask HN: Who is hiring?")), None)
        if not thread:
            return []
        item = http.get_json(self.name, "comments", ITEM.format(thread["objectID"])) or {}
        out = []
        for c in item.get("children", []):
            job = self.to_job(c)
            if job:
                out.append(job)
        return out

    def to_job(self, comment: dict) -> Job | None:
        text = strip_html(comment.get("text"))
        if not text:
            return None
        first = text.split("\n", 1)[0]
        parts = [p.strip() for p in first.split("|") if p.strip()]
        if len(parts) < 2:
            return None
        company = parts[0][:80]
        role = next((p for p in parts[1:] if ROLE_RE.search(p)), parts[1])
        places = [p for p in parts[1:] if p is not role and len(p) < 80 and PLACE_RE.search(p)]
        location = "; ".join(places)
        return Job(
            source=self.name, source_id=str(comment.get("id")),
            title=role[:150], company=company,
            url=f"https://news.ycombinator.com/item?id={comment.get('id')}",
            location=location,
            remote=True if re.search(r"remote", first, re.I) else None,
            posted_at=parse_date(comment.get("created_at")),
            description=text,
        )

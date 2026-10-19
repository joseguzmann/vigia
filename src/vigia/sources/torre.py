"""Torre (torre.ai): postings read from its public sitemaps and posting pages.

Torre's search API needs the web app's session, and its robots.txt disallows `/api/` for
bots, so this source does not use it. What robots.txt allows is enough:

  1. the sitemap index lists numbered sitemaps; the highest numbers hold the newest postings,
     each as `torre.ai/post/<id>-<company>-<title>` with its last-modified date;
  2. the slug carries company and title, so postings out of the profile's scope are skipped
     without being fetched;
  3. each posting page embeds a schema.org JobPosting with the full text and, often,
     `applicantLocationRequirements`: the exact countries the role accepts (passed on as a
     `countries:` tag, which the region rules treat as authoritative).

Pages are large (~500 KB), so a run fetches at most `max_posts` new ones.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from vigia import config, db, flags, http
from vigia.models import Job
from vigia.sources.base import Source, job_posting_ld, ld_to_job, parse_date

INDEX = "https://torre.ai/sitemap-index.xml"
MAP_RE = re.compile(r"<loc>(https://torre\.ai/sitemap-(en|es)-(\d+)\.xml)</loc>")
POST_RE = re.compile(r"<loc>(https://torre\.ai/post/([A-Za-z0-9]+)(?:-([^<]*))?)</loc>\s*<lastmod>([^<]+)</lastmod>")


class Torre(Source):
    name = "torre"

    def fetch(self) -> list[Job]:
        profile = config.profile()
        index = http.get(self.name, "index", INDEX)
        if index is None:
            return []
        maps = MAP_RE.findall(index.text)
        newest = []
        for lang, n in (("en", int(self.cfg.get("sitemaps_en", 3))), ("es", int(self.cfg.get("sitemaps_es", 1)))):
            newest += sorted((m for m in maps if m[1] == lang), key=lambda m: -int(m[2]))[:n]
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(self.cfg.get("days", 30)))
        candidates: dict[str, tuple[str, datetime]] = {}
        for url, _, _ in newest:
            resp = http.get(self.name, "sitemap", url)
            for loc, pid, slug, lastmod in POST_RE.findall(resp.text if resp is not None else ""):
                when = parse_date(lastmod)
                if when and when >= cutoff and flags.title_in_scope((slug or "").replace("-", " "), profile)[0]:
                    candidates[pid] = (loc, when)
        known = db.known_source_ids(self.name, list(candidates))
        fresh = sorted((p for p in candidates if p not in known), key=lambda p: candidates[p][1], reverse=True)
        out = []
        for pid in fresh[:int(self.cfg.get("max_posts", 60))]:
            url = candidates[pid][0]
            resp = http.get(self.name, "post", url)
            ld = job_posting_ld(resp.text) if resp is not None else None
            if ld:
                out.append(self.job(pid, url, ld))
        return out

    def job(self, pid: str, url: str, ld: dict) -> Job:
        return ld_to_job(self.name, pid, url, ld)

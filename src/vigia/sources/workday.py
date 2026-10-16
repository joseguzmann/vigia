"""Workday career sites. Public JSON behind every `<tenant>.wd<N>.myworkdayjobs.com/<site>`.

Workday is the ATS of large companies: thousands of postings per site, most tied to one
office. So this source does not read whole boards. It runs a few searches per site
(`queries`), keeps the postings whose title is in the profile's scope, and fetches the full
text only for those it has not stored yet.

Sites are configured as "tenant/wdN/site", e.g. "stord/wd503/Stord_External_Career". They were
found on 2026-10-16 by searching Google (Serper) for Workday postings that mention Ecuador or
LATAM, then keeping the sites with development roles there (docs/sources-research.md).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from vigia import config, db, flags, http
from vigia.http import Throttled
from vigia.models import Job
from vigia.sources.base import Source, parse_date, strip_html

log = logging.getLogger(__name__)
QUERIES = ["software engineer", "developer", "AI engineer", "python", "full stack", "frontend"]
POSTED_RE = re.compile(r"(\d+)\+?\s+days?\s+ago", re.I)


def posted_on(text: str | None) -> datetime | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago'."""
    now = datetime.now(timezone.utc)
    t = (text or "").lower()
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = POSTED_RE.search(t)
    return now - timedelta(days=int(m.group(1))) if m else None


class Workday(Source):
    name = "workday"

    def fetch(self) -> list[Job]:
        out: list[Job] = []
        profile = config.profile()
        for board in self.cfg.get("boards", []):
            try:
                tenant, wd, site = board.split("/")
            except ValueError:
                log.warning("workday: bad board %r (want tenant/wdN/site)", board)
                continue
            try:
                out += self.site(tenant, wd, site, profile)
            except Throttled:
                log.warning("workday: throttled at %s, stopping this run", tenant)
                break
        return out

    def site(self, tenant: str, wd: str, site: str, profile: dict) -> list[Job]:
        host = f"https://{tenant}.{wd}.myworkdayjobs.com"
        api = f"{host}/wday/cxs/{tenant}/{site}"
        postings: dict[str, dict] = {}
        for q in self.cfg.get("queries", QUERIES):
            data = http.post_json(self.name, tenant, f"{api}/jobs",
                                  {"appliedFacets": {}, "limit": int(self.cfg.get("per_query", 20)),
                                   "offset": 0, "searchText": q})
            for p in (data or {}).get("jobPostings") or []:
                if p.get("externalPath"):
                    postings[p["externalPath"]] = p
        in_scope = {path: p for path, p in postings.items() if flags.title_in_scope(p.get("title", ""), profile)[0]}
        ids = {path: f"{tenant}{path}" for path in in_scope}
        known = db.known_source_ids(self.name, list(ids.values()))
        # A posting already stored keeps the company name it got from its full text, so it
        # dedupes onto the same row ("jj" would otherwise become a second "Johnson & Johnson").
        names = dict(db.conn().execute(
            f"SELECT source_id, company FROM jobs WHERE source = ? AND source_id IN ({','.join('?' * len(known))})",
            [self.name, *known]).fetchall()) if known else {}
        out = []
        for path, p in in_scope.items():
            job = Job(source=self.name, source_id=ids[path], title=p.get("title", ""),
                      company=names.get(ids[path], tenant),
                      url=f"{host}/{site}{path}", location=p.get("locationsText") or "",
                      posted_at=posted_on(p.get("postedOn")))
            if ids[path] not in known:
                full = http.get_json(self.name, "posting", f"{api}{path}") or {}
                detail = full.get("jobPostingInfo") or {}
                if detail:
                    job.description = strip_html(detail.get("jobDescription"))
                    job.location = detail.get("location") or job.location
                    job.posted_at = parse_date(detail.get("startDate")) or job.posted_at
                    job.url = detail.get("externalUrl") or job.url
                    org = full.get("hiringOrganization") or {}
                    name = org.get("name") if isinstance(org, dict) else ""
                    # Legal entities come with a cost-center code: "957100 Motorola Solutions Ireland Limited".
                    job.company = re.sub(r"^\d+\s+", "", name or "") or tenant
                    if "remote" in (job.location or "").lower():
                        job.remote = True
            out.append(job)
        return out

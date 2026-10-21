"""Company job boards on the three big applicant-tracking systems. All public, no key.

Watching a company directly catches roles before aggregators do, and these boards are
where LATAM-friendly companies (nearshore consultancies, remote-first startups) publish.
The companies to watch are listed per ATS in the config: `boards: [slug, ...]`, where the
slug is the one in the public URL (job-boards.greenhouse.io/<slug>, jobs.ashbyhq.com/<slug>,
jobs.lever.co/<slug>).
"""

from __future__ import annotations

import html

from vigia import http
from vigia.models import Job
from vigia.sources.base import Source, parse_date, strip_html


class Greenhouse(Source):
    name = "greenhouse"

    def fetch(self) -> list[Job]:
        out = []
        for slug in self.boards():
            data = http.get_json(self.name, slug,
                                 f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                                 params={"content": "true"})
            for j in (data or {}).get("jobs", []):
                out.append(Job(
                    source=self.name, source_id=str(j.get("id")), title=j.get("title", ""),
                    company=slug, url=j.get("absolute_url", ""),
                    location=(j.get("location") or {}).get("name", ""),
                    posted_at=parse_date(j.get("first_published") or j.get("updated_at")),
                    description=strip_html(html.unescape(j.get("content") or "")),  # escaped HTML
                ))
        return out


class Ashby(Source):
    name = "ashby"

    def fetch(self) -> list[Job]:
        out = []
        for slug in self.boards():
            data = http.get_json(self.name, slug,
                                 f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                                 params={"includeCompensation": "true"})
            for j in (data or {}).get("jobs", []):
                remote = j.get("isRemote") or j.get("workplaceType") == "Remote"
                locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
                out.append(Job(
                    source=self.name, source_id=j.get("id") or j.get("jobUrl", ""),
                    title=j.get("title", ""), company=slug, url=j.get("jobUrl", ""),
                    location=", ".join(l for l in locs if l) + (" (Remote)" if remote else ""),
                    remote=bool(remote), posted_at=parse_date(j.get("publishedAt")),
                    description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                    salary=(j.get("compensation") or {}).get("compensationTierSummary") or "",
                ))
        return out


class Lever(Source):
    name = "lever"

    def fetch(self) -> list[Job]:
        out = []
        for slug in self.boards():
            data = http.get_json(self.name, slug, f"https://api.lever.co/v0/postings/{slug}",
                                 params={"mode": "json"})
            for j in data or []:
                cat = j.get("categories") or {}
                workplace = j.get("workplaceType") or ""
                out.append(Job(
                    source=self.name, source_id=j.get("id", ""), title=j.get("text", ""),
                    company=slug, url=j.get("hostedUrl", ""),
                    location=", ".join(x for x in [cat.get("location"), workplace] if x),
                    remote=workplace == "remote" if workplace else None,
                    posted_at=parse_date(j.get("createdAt")),
                    description=j.get("descriptionPlain") or strip_html(j.get("description")),
                ))
        return out


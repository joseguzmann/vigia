"""Get on Board — the tech job board most used by LATAM companies. Public API, no key."""

from __future__ import annotations

from vigia import http
from vigia.models import Job
from vigia.sources.base import Source, parse_date, strip_html

API = "https://www.getonbrd.com/api/v0/search/jobs"


class GetOnBoard(Source):
    name = "getonbrd"

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        for query in self.cfg.get("queries", ["python"]):
            data = http.get_json(self.name, "search", API, params={
                "query": query, "per_page": 50, "expand": '["company"]'})
            for item in (data or {}).get("data", []):
                job = self.to_job(item)
                if job:
                    jobs[job.source_id] = job
        return list(jobs.values())

    def to_job(self, item: dict) -> Job | None:
        a = item.get("attributes", {})
        modality = a.get("remote_modality")
        # "fully_remote" = anywhere; "remote_local" = remote within one country (usually
        # the company's); hybrid / no_remote are office-bound. Keep only the first two and
        # let the flags decide on remote_local.
        if modality not in ("fully_remote", "remote_local"):
            return None
        company = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name", "")
        countries = [c for c in (a.get("countries") or []) if c and c != "Remote"]
        location = "Remote" if modality == "fully_remote" else "Remote (one country): " + ", ".join(countries or ["unspecified"])
        salary = ""
        if a.get("min_salary"):
            salary = f"USD {a['min_salary']:,}–{(a.get('max_salary') or a['min_salary']):,}/month"
        description = "\n\n".join(strip_html(a.get(k)) for k in
                                  ("projects", "functions", "description", "desirable") if a.get(k))
        return Job(
            source=self.name,
            source_id=str(item.get("id")),
            title=a.get("title", ""),
            company=company,
            url=(item.get("links") or {}).get("public_url", ""),
            location=location,
            remote=modality == "fully_remote",
            posted_at=parse_date(a.get("published_at")),
            description=description,
            salary=salary,
            tags=[modality],
        )

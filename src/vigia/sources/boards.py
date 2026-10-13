"""Remote job boards with public JSON APIs: Remotive, Himalayas, Jobicy."""

from __future__ import annotations

import ast

from vigia import http
from vigia.models import Job
from vigia.sources.base import Source, parse_date, strip_html


class Remotive(Source):
    name = "remotive"

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        for query in self.cfg.get("queries", ["python"]):
            data = http.get_json(self.name, "search", "https://remotive.com/api/remote-jobs",
                                 params={"search": query, "limit": 100})
            for j in (data or {}).get("jobs", []):
                jobs[str(j["id"])] = Job(
                    source=self.name, source_id=str(j["id"]), title=j.get("title", ""),
                    company=j.get("company_name", ""), url=j.get("url", ""),
                    location=j.get("candidate_required_location", ""), remote=True,
                    posted_at=parse_date(j.get("publication_date")),
                    description=strip_html(j.get("description")),
                    salary=j.get("salary") or "", tags=j.get("tags") or [],
                )
        return list(jobs.values())


class Himalayas(Source):
    name = "himalayas"

    def fetch(self) -> list[Job]:
        jobs: dict[str, Job] = {}
        for query in self.cfg.get("queries", ["python"]):
            data = http.get_json(self.name, "search", "https://himalayas.app/jobs/api/search",
                                 params={"q": query, "limit": 50})
            for j in (data or {}).get("jobs", []):
                url = j.get("applicationLink") or j.get("guid") or ""
                regions = _as_list(j.get("locationRestrictions"))
                salary = ""
                if j.get("minSalary"):
                    salary = f"{j.get('currency', '')} {int(float(j['minSalary'])):,}–{int(float(j.get('maxSalary') or j['minSalary'])):,}/{j.get('salaryPeriod') or 'year'}"
                jobs[url] = Job(
                    source=self.name, source_id=url, title=j.get("title", ""),
                    company=j.get("companyName", ""), url=url,
                    location=", ".join(regions) or "Worldwide", remote=True,
                    posted_at=parse_date(j.get("pubDate")),
                    description=strip_html(j.get("description")), salary=salary,
                )
        return list(jobs.values())


class Jobicy(Source):
    name = "jobicy"

    def fetch(self) -> list[Job]:
        data = http.get_json(self.name, "list", "https://jobicy.com/api/v2/remote-jobs",
                             params={"count": 100})
        out = []
        for j in (data or {}).get("jobs", []):
            salary = ""
            if j.get("salaryMin"):
                salary = f"{j.get('salaryCurrency', '')} {j['salaryMin']:,}–{j.get('salaryMax') or j['salaryMin']:,}"
            out.append(Job(
                source=self.name, source_id=str(j.get("id")), title=j.get("jobTitle", ""),
                company=j.get("companyName", ""), url=j.get("url", ""),
                location=str(j.get("jobGeo") or ""), remote=True,
                posted_at=parse_date(j.get("pubDate")),
                description=strip_html(j.get("jobDescription")), salary=salary,
            ))
        return out


def _as_list(value) -> list[str]:
    """Himalayas sends lists, but some fields arrive as their string repr."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value.startswith("["):
        try:
            return [str(v) for v in ast.literal_eval(value)]
        except (ValueError, SyntaxError):
            return [value]
    return [str(value)] if value else []

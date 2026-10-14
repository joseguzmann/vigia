"""Remote job boards with public feeds: Remotive, Himalayas, Jobicy, Working Nomads, We Work Remotely."""

from __future__ import annotations

import ast
import re
import xml.etree.ElementTree as ET

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
                    # Himalayas' locationRestrictions is an explicit "only these countries".
                    tags=["location_restricted"] if regions else [],
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


class WorkingNomads(Source):
    name = "workingnomads"

    def fetch(self) -> list[Job]:
        data = http.get_json(self.name, "list", "https://www.workingnomads.com/api/exposed_jobs/")
        out = []
        for j in data or []:
            out.append(Job(
                source=self.name, source_id=j.get("url", ""), title=j.get("title", ""),
                company=j.get("company_name", ""), url=j.get("url", ""),
                location=j.get("location") or "", remote=True,
                posted_at=parse_date(j.get("pub_date")),
                description=strip_html(j.get("description")),
                tags=[t.strip() for t in (j.get("tags") or "").split(",") if t.strip()],
            ))
        return out


class WeWorkRemotely(Source):
    """RSS per category. Titles come as "Company: Role"."""
    name = "weworkremotely"
    CATEGORIES = ["remote-back-end-programming-jobs", "remote-full-stack-programming-jobs",
                  "remote-devops-sysadmin-jobs", "remote-programming-jobs"]

    def fetch(self) -> list[Job]:
        out = {}
        for cat in self.cfg.get("categories", self.CATEGORIES):
            resp = http.get(self.name, cat, f"https://weworkremotely.com/categories/{cat}.rss")
            if resp is None:
                continue
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                continue
            for item in root.iter("item"):
                raw = item.findtext("title") or ""
                company, _, title = raw.partition(": ")
                url = item.findtext("link") or ""
                out[url] = Job(
                    source=self.name, source_id=url, title=title or raw, company=company if title else "",
                    url=url, location=item.findtext("region") or "", remote=True,
                    posted_at=_rss_date(item.findtext("pubDate")),
                    description=strip_html(item.findtext("description")),
                )
        return list(out.values())


def _rss_date(value):
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None

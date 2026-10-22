"""More applicant-tracking systems with public company boards: Teamtailor, Workable,
SmartRecruiters, Breezy HR and Recruitee. All public, no key.

Same idea as `ats.py`: the companies to watch are listed per ATS in the config
(`boards: [slug, ...]`, the slug in the public URL). Found on 2026-10-15 by probing the
remoteintech list of remote companies plus LATAM consultancies (see docs/sources-research.md).

Two of them (SmartRecruiters, Breezy) list postings without their text. For those, the
description is fetched only for titles in the profile's scope, so a board of 100 jobs costs
one request plus a handful.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from vigia import config, flags, http
from vigia.http import Throttled
from vigia.models import Job
from vigia.sources.base import Source, job_posting_ld, parse_date, strip_html

log = logging.getLogger(__name__)
TT = "{https://teamtailor.com/locations}"


def _in_scope(title: str) -> bool:
    return flags.title_in_scope(title, config.profile())[0]


class _Boards(Source):
    """Runs `board(slug)` for every configured company; a 429 stops the run, other errors
    skip only that company."""

    def fetch(self) -> list[Job]:
        out: list[Job] = []
        for slug in self.boards():
            try:
                out += self.board(slug)
            except Throttled:
                if not out:  # nothing read: say so, so a single-board check isn't taken as "no jobs"
                    raise
                log.warning("%s: throttled at %s, stopping this run", self.name, slug)
                break
        return out

    def board(self, slug: str) -> list[Job]:
        raise NotImplementedError


class Teamtailor(_Boards):
    """RSS of the career site: title, full description, date, locations and remote status."""
    name = "teamtailor"

    def board(self, slug):
        resp = http.get(self.name, slug, f"https://{slug}.teamtailor.com/jobs.rss")
        if resp is None:
            return []
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            return []
        company = root.findtext("channel/title") or slug
        out = []
        for item in root.iter("item"):
            places = [", ".join(x for x in (loc.findtext(f"{TT}city"), loc.findtext(f"{TT}country")) if x)
                      for loc in item.iter(f"{TT}location")]
            status = (item.findtext("remoteStatus") or "").lower()
            location = "; ".join(p for p in places if p)
            if status == "fully":
                location = f"{location} (Remote)" if location else "Remote"
            out.append(Job(
                source=self.name, source_id=item.findtext("guid") or item.findtext("link") or "",
                title=item.findtext("title") or "", company=item.findtext("company_name") or company,
                url=item.findtext("link") or "", location=location,
                remote=True if status == "fully" else False if status in ("none", "hybrid") else None,
                posted_at=parse_date(_rfc822(item.findtext("pubDate"))),
                description=strip_html(item.findtext("description")),
            ))
        return out


class Workable(_Boards):
    """The public widget API, with descriptions (`details=true`)."""
    name = "workable"

    def board(self, slug):
        data = http.get_json(self.name, slug, f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                             params={"details": "true"})
        if not data:
            return []
        out = []
        for j in data.get("jobs") or []:
            locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
            place = "; ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x) for l in locs)
            remote = bool(j.get("telecommuting"))
            out.append(Job(
                source=self.name, source_id=j.get("shortcode") or j.get("url", ""), title=j.get("title", ""),
                company=data.get("name") or slug, url=j.get("url") or j.get("shortlink") or "",
                location=place + (" (Remote)" if remote else ""), remote=remote,
                posted_at=parse_date(j.get("published_on") or j.get("created_at")),
                description=strip_html(j.get("description")),
            ))
        return out


class SmartRecruiters(_Boards):
    name = "smartrecruiters"
    API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"

    def board(self, slug):
        out, offset = [], 0
        while True:
            data = http.get_json(self.name, slug, self.API.format(slug=slug),
                                 params={"limit": 100, "offset": offset})
            content = (data or {}).get("content") or []
            for j in content:
                loc = j.get("location") or {}
                remote = bool(loc.get("remote"))
                job = Job(
                    source=self.name, source_id=str(j.get("id", "")), title=j.get("name", ""),
                    company=(j.get("company") or {}).get("name") or slug,
                    url=f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                    location=(loc.get("fullLocation") or "").replace(", ,", ",") + (" (Remote)" if remote else ""),
                    remote=remote, posted_at=parse_date(j.get("releasedDate")),
                )
                if _in_scope(job.title):
                    detail = http.get_json(self.name, "posting", f"{self.API.format(slug=slug)}/{j.get('id')}")
                    if detail:
                        sections = (detail.get("jobAd") or {}).get("sections") or {}
                        job.description = "\n\n".join(strip_html((sections.get(k) or {}).get("text"))
                                                      for k in ("jobDescription", "qualifications",
                                                                "additionalInformation", "companyDescription"))
                        job.url = detail.get("postingUrl") or job.url
                out.append(job)
            offset += len(content)
            if not content or offset >= (data or {}).get("totalFound", 0) or offset >= 500:
                return out


class Breezy(_Boards):
    """`<slug>.breezy.hr/json` lists postings; the text comes from the job page's JSON-LD."""
    name = "breezy"

    def board(self, slug):
        data = http.get_json(self.name, slug, f"https://{slug}.breezy.hr/json")
        out = []
        for j in data if isinstance(data, list) else []:
            locs = j.get("locations") or [j.get("location") or {}]
            remote = any(l.get("is_remote") for l in locs)
            place = "; ".join(l.get("name") or "" for l in locs)
            job = Job(
                source=self.name, source_id=j.get("id", ""), title=j.get("name", ""),
                company=(j.get("company") or {}).get("name") or slug, url=j.get("url", ""),
                location=place + (" (Remote)" if remote and "remote" not in place.lower() else ""),
                remote=remote, posted_at=parse_date(j.get("published_date")),
                salary=j.get("salary") or "",
            )
            if _in_scope(job.title) and job.url:
                resp = http.get(self.name, "posting", job.url)
                ld = job_posting_ld(resp.text) if resp is not None else None
                if ld:
                    job.description = strip_html(ld.get("description"))
            out.append(job)
        return out


class Recruitee(_Boards):
    name = "recruitee"

    def board(self, slug):
        data = http.get_json(self.name, slug, f"https://{slug}.recruitee.com/api/offers/")
        out = []
        for j in (data or {}).get("offers") or []:
            remote = bool(j.get("remote"))
            # `locations` says where candidates may be ("India - Remote", note "Applicable to candidates
            # based in India"); the flat `location` is often just "Remote job".
            locs = j.get("locations") or []
            names = [x.get("name") for x in locs if x.get("name")]
            notes = [((x.get("translations") or {}).get("en") or {}).get("note") or x.get("note") for x in locs]
            place = "; ".join(names) or j.get("location") or ", ".join(x for x in (j.get("city"), j.get("country")) if x)
            text = strip_html(f"{j.get('description') or ''}\n{j.get('requirements') or ''}")
            notes = [n for n in notes if n and n not in text]
            out.append(Job(
                source=self.name, source_id=str(j.get("id", "")), title=j.get("title", ""),
                company=j.get("company_name") or slug, url=j.get("careers_url", ""),
                location=place + (" (Remote)" if remote and "remote" not in place.lower() else ""), remote=remote,
                posted_at=parse_date((j.get("published_at") or j.get("created_at") or "").replace(" UTC", "+00:00")),
                description=text + ("\n\nLocation: " + " ".join(notes) if notes else ""),
            ))
        return out


class BambooHR(_Boards):
    """Public careers JSON: the list gives titles; the text, date and work mode come from each
    opening's detail, read only for in-scope titles. locationType: 1 remote, 2 hybrid, 0 on-site.
    A remote opening keeps "Remote" as its location: the country BambooHR lists for it is usually
    the company's own, and the text says where it hires."""
    name = "bamboohr"

    def board(self, slug):
        base = f"https://{slug}.bamboohr.com/careers"
        data = http.get_json(self.name, slug, f"{base}/list")
        out = []
        for j in (data or {}).get("result") or []:
            title = j.get("jobOpeningName") or ""
            if not j.get("id") or not _in_scope(title):
                continue
            d = ((http.get_json(self.name, slug, f"{base}/{j['id']}/detail") or {}).get("result") or {}).get("jobOpening") or {}
            kind = str(d.get("locationType") or j.get("locationType") or "")
            loc = d.get("atsLocation") or j.get("atsLocation") or {}
            place = ", ".join(x for x in (loc.get("city"), loc.get("state") or loc.get("province"), loc.get("country")) if x)
            remote = kind == "1" or bool(j.get("isRemote"))
            out.append(Job(
                source=self.name, source_id=f"{slug}/{j['id']}", title=title, company=slug,
                url=d.get("jobOpeningShareUrl") or f"{base}/{j['id']}",
                location="Remote" if remote else place + (" (Hybrid)" if kind == "2" else ""),
                remote=remote if kind else None, posted_at=parse_date(d.get("datePosted")),
                description=strip_html(d.get("description") or ""),
                salary=str(d.get("compensation") or ""),
            ))
        return out


class Rippling(_Boards):
    """Public board API: the list gives titles and work locations ("Remote (Brazil)"); the text and
    date come from each job's detail, read only for in-scope titles."""
    name = "rippling"

    def board(self, slug):
        base = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
        out = []
        for j in http.get_json(self.name, slug, base) or []:
            title = j.get("name") or ""
            if not j.get("uuid") or not _in_scope(title):
                continue
            d = http.get_json(self.name, slug, f"{base}/{j['uuid']}") or {}
            desc = d.get("description") or ""
            if isinstance(desc, dict):  # {"company": html, "role": html}
                desc = "\n".join(str(v) for v in desc.values())
            places = d.get("workLocations") or [(j.get("workLocation") or {}).get("label") or ""]
            location = "; ".join(x for x in places if x)
            out.append(Job(
                source=self.name, source_id=f"{slug}/{j['uuid']}", title=title, company=slug,
                url=j.get("url") or f"https://ats.rippling.com/{slug}/jobs/{j['uuid']}",
                location=location, remote=True if "remote" in location.lower() else None,
                posted_at=parse_date(d.get("createdOn")), description=strip_html(desc),
            ))
        return out


class Manatal(_Boards):
    """careers-page.com (Manatal): a paged public API with the full text in the list. Many boards
    are recruiting agencies hiring for several clients. No posting date is published."""
    name = "manatal"

    def board(self, slug):
        url, out = f"https://api.manatal.com/open/v3/career-page/{slug}/jobs/", []
        for _ in range(int(self.cfg.get("max_pages", 5))):
            data = http.get_json(self.name, slug, url) or {}
            for j in data.get("results") or []:
                title = j.get("position_name") or ""
                if not _in_scope(title):
                    continue
                out.append(Job(
                    source=self.name, source_id=f"{slug}/{j.get('id')}", title=title, company=slug,
                    url=f"https://www.careers-page.com/{slug}/job/{j.get('hash')}",
                    location=j.get("location_display") or "", remote=j.get("is_remote"),
                    description=strip_html(j.get("description") or ""),
                ))
            url = data.get("next")
            if not url:
                break
        return out


def _rfc822(value: str | None):
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(value).isoformat() if value else None
    except (TypeError, ValueError):
        return None

"""Indeed, through the GraphQL API its mobile app uses (`apis.indeed.com/graphql`).

Indeed has no public jobs API. This reads what the iPhone app reads, with the app's own
public key and headers, as [JobSpy](https://github.com/speedyapply/JobSpy) (MIT) does. One
request per search returns up to 100 postings with their full text; nothing else is fetched.

Searches run per country (`countries: [{code: EC, queries: [...]}]`). Measured 2026-10-16:
Ecuador returns the local market plus worldwide-remote roles listed there; other LATAM
countries return mostly on-site roles even with Indeed's remote filter.
"""

from __future__ import annotations

import json
import re

from vigia import http
from vigia.models import Job
from vigia.sources.base import Source, parse_date, strip_html

API = "https://apis.indeed.com/graphql"
HEADERS = {
    "content-type": "application/json",
    "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
    "user-agent": ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Mobile/15E148 Indeed App 193.1"),
    "indeed-app-info": "appv=193.1; appid=com.indeed.jobsearch; osv=16.6.1; os=ios; dtype=phone",
}
REMOTE, HYBRID = "DSQF7", "PAXZC"   # Indeed's attribute keys
COUNTRY = {"EC": "Ecuador", "CO": "Colombia", "PE": "Peru", "MX": "Mexico", "AR": "Argentina", "CL": "Chile",
           "UY": "Uruguay", "CR": "Costa Rica", "PA": "Panama", "BR": "Brazil"}
QUERY = """query {{ jobSearch(what: {what} limit: 100 sort: DATE filters: [{{ date: {{ field: "dateOnIndeed", start: "{hours}h" }} }}]) {{
  results {{ job {{ key title sourceEmployerName dateOnIndeed description {{ html }}
    location {{ countryCode admin1Code city }} attributes {{ key label }} employer {{ name }} }} }} }} }}"""
HOME = ("desde casa", "remoto", "home office", "teletrabajo")
# Indeed marks the work mode only when the employer sets it. Without it, a local posting is
# on-site unless its text says otherwise ("100% remoto", "Remote - LATAM", "trabajo desde casa").
REMOTE_TEXT_RE = re.compile(r"(?<!no )(?<!not )\b(?:100\s*%\s*remot[oe]|fully remote|remote|remoto|teletrabajo|"
                            r"desde casa|home office|work from home)\b", re.I)
HYBRID_TEXT_RE = re.compile(r"\b(?:h[ií]brid[oa]|hybrid|presencial y remoto|semipresencial)\b", re.I)


class Indeed(Source):
    name = "indeed"

    def fetch(self) -> list[Job]:
        out: dict[str, Job] = {}
        hours = int(self.cfg.get("days", 30)) * 24
        for country in self.cfg.get("countries", [{"code": "EC", "queries": ["desarrollador"]}]):
            code = country["code"].upper()
            headers = {**HEADERS, "indeed-co": code, "indeed-locale": f"es-{code}"}
            for q in country.get("queries", []):
                data = http.post_json(self.name, f"{code}:{q}", API,
                                      {"query": QUERY.format(what=json.dumps(q, ensure_ascii=False), hours=hours)},
                                      headers=headers)
                for r in (((data or {}).get("data") or {}).get("jobSearch") or {}).get("results") or []:
                    job = r.get("job") or {}
                    if job.get("key"):
                        out.setdefault(job["key"], self.job(job, code))
        return list(out.values())

    def job(self, j: dict, code: str) -> Job:
        loc = j.get("location") or {}
        keys = {a.get("key") for a in j.get("attributes") or []}
        city = loc.get("city") or ""
        remote = REMOTE in keys or city.lower() in HOME
        place = ", ".join(x for x in (city if city.lower() not in HOME else "",
                                      COUNTRY.get(loc.get("countryCode") or code, code)) if x)
        text = f"{j.get('title', '')}\n{strip_html(((j.get('description') or {}).get('html')) or '')[:2000]}"
        if HYBRID in keys or (not remote and HYBRID_TEXT_RE.search(text)):
            mode = " · hybrid"
        elif remote or REMOTE_TEXT_RE.search(text):
            mode, remote = " (Remote)", True
        else:
            mode = " · presencial"
        return Job(
            source=self.name, source_id=j["key"], title=j.get("title", ""),
            company=(j.get("employer") or {}).get("name") or j.get("sourceEmployerName") or "",
            url=f"https://{code.lower()}.indeed.com/viewjob?jk={j['key']}",
            location=place + mode, remote=remote,
            posted_at=parse_date(j.get("dateOnIndeed")),
            description=strip_html(((j.get("description") or {}).get("html")) or ""),
        )

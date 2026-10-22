"""Find companies to watch: Google searches restricted to each ATS's public job pages.

The ATS sources read a company's board for free once they know its slug, but no ATS
publishes the list of companies that use it. Google has indexed those pages, so a search
like `site:jobs.ashbyhq.com "Latin America" "AI Engineer"` returns boards of companies
hiring for the profile, with the slug in the URL. This source runs a few such searches
through Serper (one credit each), reads each new board once through its ATS source, and
keeps watching it only if something there passes the filters. It returns those jobs.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from vigia import config, db, flags, http
from vigia.models import Job
from vigia.sources.base import Source

SERPER = "https://google.serper.dev/search"

# ATS source name → (Google site: filter, regex that takes the slug out of a result URL).
SITES = {
    "ashby": ("site:jobs.ashbyhq.com", r"^https?://jobs\.ashbyhq\.com/([^/?#]+)"),
    "greenhouse": ("(site:job-boards.greenhouse.io OR site:boards.greenhouse.io)",
                   r"^https?://(?:job-)?boards\.greenhouse\.io/(?!embed\b)([^/?#]+)"),
    "lever": ("site:jobs.lever.co", r"^https?://jobs\.lever\.co/([^/?#]+)"),
    "workable": ("site:apply.workable.com", r"^https?://apply\.workable\.com/(?!api\b|j\b)([^/?#]+)"),
    "teamtailor": ("site:teamtailor.com", r"^https?://((?:[a-z0-9-]+)(?:\.na)?)\.teamtailor\.com/jobs"),
}


def slug_from(ats: str, url: str) -> str | None:
    m = re.match(SITES[ats][1], url or "", re.I)
    return m.group(1).lower() if m else None


class Discover(Source):
    name = "discover"

    def queries(self) -> list[tuple[str, str]]:
        out = []
        for ats in self.cfg.get("ats", list(SITES)):
            for region in self.cfg.get("regions", ['("Latin America" OR LATAM)']):
                for roles in self.cfg.get("roles", ['("AI Engineer" OR "LLM")']):
                    out.append((ats, f"{SITES[ats][0]} {region} {roles}"))
        return out

    def fetch(self) -> list[Job]:
        key = os.environ.get("SERPER_API_KEY")
        if not key:
            raise RuntimeError("SERPER_API_KEY not set")
        all_q = self.queries()
        n = int(self.cfg.get("queries_per_run", 15))
        start = int(db.get_state("discover.cursor", "0")) % len(all_q)
        batch = [all_q[(start + i) % len(all_q)] for i in range(min(n, len(all_q)))]
        db.set_state("discover.cursor", str(start + len(batch)))

        candidates: dict[tuple[str, str], tuple[str, str]] = {}
        for ats, q in batch:
            for r in self.search(key, q):
                slug = slug_from(ats, r.get("link", ""))
                if slug:
                    candidates.setdefault((ats, slug), (q, r.get("title", "")))

        out = []
        checked = 0
        for (ats, slug), (q, example) in candidates.items():
            if is_known(ats, slug):
                continue
            if checked >= int(self.cfg.get("max_new_per_run", 40)):
                break
            checked += 1
            try:
                out += check_board(ats, slug, q, example, reader=self.read_board)
            except http.Throttled:
                break  # unchecked boards stay unknown and come up again
        return out

    def read_board(self, ats: str, slug: str) -> list[Job]:
        return read_board(ats, slug)


    def search(self, key: str, q: str) -> list[dict]:
        started = datetime.now(timezone.utc)
        try:
            resp = http._session.post(SERPER, timeout=30, headers={"X-API-KEY": key},
                                      json={"q": q, "num": 10, "tbs": self.cfg.get("freshness", "qdr:m")})
            status = resp.status_code
            data = resp.json() if status == 200 else {}
        except Exception:  # noqa: BLE001 — one failed query must not stop the run
            status, data = 0, {}
        db.log_request("serper", "discover", status,
                       int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        return data.get("organic", [])


def read_board(ats: str, slug: str) -> list[Job]:
    from vigia.sources import REGISTRY
    try:
        return REGISTRY[ats]({"boards": [slug], "include_discovered": False}).fetch()
    except http.Throttled:
        raise  # not read: the caller stops and the board is checked again later
    except Exception:  # noqa: BLE001 — a broken board is just not watched
        return []


def is_known(ats: str, slug: str) -> bool:
    known = {str(s).lower() for s in config.source(ats).get("boards", [])}
    return slug.lower() in known or slug.lower() in set(db.discovered_slugs(ats, None))


def check_board(ats: str, slug: str, origin: str, example: str = "", reader=read_board) -> list[Job]:
    """Read a board once; watch it from now on only if a job passes the filters.
    Returns the jobs that passed."""
    jobs = reader(ats, slug)
    keep = [j for j in jobs if flags.evaluate(j, config.profile())[0] in ("fit", "extra", "review")]
    db.add_discovered(ats, slug.lower(), "watching" if keep else "rejected", origin,
                      (example or (keep[0].title if keep else ""))[:200], len(keep))
    return keep

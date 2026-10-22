"""openroles.today: an open, nightly dataset of postings from ~54 applicant-tracking systems.

openroles (github.com/datascry/openroles; data CC BY-SA 4.0) publishes every posting it finds
on public ATS boards as gzipped JSON files, newest first, with the ATS, the company slug,
the work arrangement and a country code, but no description. So this source uses it to
find *where* to look, not as postings:

  1. read the files covering the last `days` days and keep in-scope titles that are remote
     (or say nothing) and whose country is unknown, the home country or open to the region
  2. ATS that Vigía reads (Greenhouse, Ashby, Lever, Workday...): check the company's board
     once, like `discover`; watched from then on if anything passes the filters
  3. other ATS (BambooHR, Rippling, Personio...): read the posting's own page and its
     schema.org JobPosting, up to `max_pages` per run
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import datetime, timedelta, timezone

from vigia import config, db, http
from vigia.flags import title_in_scope
from vigia.models import Job
from vigia.sources.base import Source, job_posting_ld, ld_to_job
from vigia.sources.discover import check_board, is_known

BASE = "https://openroles.today/data/"
READABLE = {"greenhouse", "ashby", "lever", "smartrecruiters", "breezy", "recruitee", "workable", "teamtailor"}
WORKDAY_RE = re.compile(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", re.I)
OPEN_LOC_RE = re.compile(r"latam|latin america|south america|americas|worldwide|anywhere|global", re.I)


def board_of(row: dict) -> tuple[str, str] | None:
    """(Vigía ATS source, board id) for a posting, if Vigía can read that ATS."""
    if row.get("a") in READABLE and row.get("t"):
        return row["a"], str(row["t"]).lower()
    if row.get("a") == "workday":
        m = WORKDAY_RE.match(row.get("u") or "")
        if m:
            return "workday", f"{m.group(1).lower()}/{m.group(2).lower()}/{m.group(3)}"
    return None


class OpenRoles(Source):
    name = "openroles"

    def wanted(self, row: dict, profile: dict, home: str) -> bool:
        if not title_in_scope(row.get("ti") or "", profile)[0]:
            return False
        if row.get("l") in ("senior", "staff", "principal", "executive", "manager", "intern"):
            return False
        if row.get("w") not in ("remote", None):
            return False
        # Unknown country or the home country; otherwise only a location open to the region.
        return row.get("cc") in (None, home) or bool(OPEN_LOC_RE.search(row.get("loc") or ""))

    def rows(self) -> list[dict]:
        manifest = http.get_json(self.name, "manifest", BASE + "manifest.json") or {}
        since = datetime.now(timezone.utc) - timedelta(days=float(self.cfg.get("days", 2)))
        out = []
        for chunk in manifest.get("slim_index_chunks", []):
            newest = datetime.fromisoformat(chunk["posted_max"].replace("Z", "+00:00"))
            if newest < since and not self.cfg.get("all_chunks"):
                break  # files are sorted newest first
            resp = http.get(self.name, "chunk", BASE + chunk["file"])
            if resp is None:
                continue
            try:
                out += json.loads(gzip.decompress(resp.content))
            except (OSError, ValueError):
                continue
        return out

    def fetch(self) -> list[Job]:
        profile = config.profile()
        home = {"ecuador": "EC"}.get((profile.get("home_country") or "").lower(), "EC")
        boards: dict[tuple[str, str], str] = {}
        pages: list[dict] = []
        for r in self.rows():
            if not self.wanted(r, profile, home):
                continue
            b = board_of(r)
            if b:
                boards.setdefault(b, r.get("ti") or "")
            elif r.get("u"):
                pages.append(r)

        out: list[Job] = []
        budget = int(self.cfg.get("max_new_boards", 60))
        for (ats, slug), title in boards.items():
            if budget <= 0:
                break
            if is_known(ats, slug):
                continue
            budget -= 1
            out += check_board(ats, slug, "openroles", title)

        ids = {r["i"]: r for r in pages}
        known = db.known_source_ids(self.name, list(ids))
        for i, r in [(i, r) for i, r in ids.items() if i not in known][: int(self.cfg.get("max_pages", 40))]:
            resp = http.get(self.name, "posting", r["u"])
            ld = job_posting_ld(resp.text) if resp is not None else None
            if not ld:
                continue
            job = ld_to_job(self.name, i, r["u"], ld)
            job.company = job.company or r.get("c") or ""
            job.title = job.title or r.get("ti") or ""
            out.append(job)
        return out

"""LinkedIn public (logged-out) job search. Optional and deliberately slow.

LinkedIn has no public jobs API. This adapter reads the same pages a logged-out
browser loads:

  /jobs-guest/jobs/api/seeMoreJobPostings/search   10 job cards per call
  /jobs-guest/jobs/api/jobPosting/<id>              one posting's description

LinkedIn rate-limits by IP and answers HTTP 429 when we go too fast. So each run takes
ONE query from a rotating list, reads up to two pages of cards, filters by title and
location first, and only then fetches a handful of descriptions with pauses between
them. Every request lands in the `requests` table, which is how we learn the limit.
No account, cookie or login is ever used.
"""

from __future__ import annotations

import random
import re
import time

from bs4 import BeautifulSoup

from vigia import config, db, http
from vigia.flags import region, title_in_scope
from vigia.models import Job
from vigia.sources.base import Source, parse_date

BASE = "https://www.linkedin.com"
SEARCH = BASE + "/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING = BASE + "/jobs-guest/jobs/api/jobPosting/{}"
PAGES = 2


class LinkedIn(Source):
    name = "linkedin"

    def fetch(self) -> list[Job]:
        queries = self.cfg.get("queries") or []
        if not queries:
            return []
        i = int(db.get_state("linkedin.cursor", "0")) % len(queries)
        db.set_state("linkedin.cursor", str(i + 1))
        q = queries[i]

        cards: list[Job] = []
        for page in range(PAGES):
            resp = http.get(self.name, "search", SEARCH, params={
                "keywords": q["keywords"], "location": q.get("location", ""),
                "f_TPR": f"r{int(self.cfg.get('hours_old', 48)) * 3600}",
                "f_WT": q.get("work_type", ""),  # "2" = remote
                "start": page * 10})
            if resp is None:
                break
            page_cards = parse_cards(resp.text)
            cards += page_cards
            if len(page_cards) < 10:
                break
            self._pause()

        # Spend description requests only on cards that could be a fit and are new to us.
        profile = config.profile()
        known = db.known_keys([c.dedupe_key for c in cards])
        worth = [c for c in cards if c.dedupe_key not in known
                 and title_in_scope(c.title, profile)[0]
                 and region(c, profile)[0] != "restricted"]
        for card in worth[: int(self.cfg.get("max_descriptions", 8))]:
            self._pause()
            try:
                resp = http.get(self.name, "posting", POSTING.format(card.source_id))
            except http.Throttled:
                break  # keep the cards we have; descriptions can come on a later run
            if resp is not None:
                card.description = parse_description(resp.text)
        return cards

    def _pause(self) -> None:
        low, high = self.cfg.get("pause_seconds", [6, 12])
        time.sleep(random.uniform(low, high))


def parse_cards(html_text: str) -> list[Job]:
    soup = BeautifulSoup(html_text, "html.parser")
    out = []
    for card in soup.select("div.base-search-card, li div.job-search-card"):
        urn = card.get("data-entity-urn", "")
        m = re.search(r"(\d+)$", urn)
        link = card.select_one("a.base-card__full-link")
        if not m and link:
            m = re.search(r"-(\d+)(?:\?|$)", link.get("href", ""))
        if not m:
            continue
        job_id = m.group(1)
        title = _text(card.select_one(".base-search-card__title"))
        company = _text(card.select_one(".base-search-card__subtitle"))
        location = _text(card.select_one(".job-search-card__location"))
        when = card.select_one("time")
        out.append(Job(
            source="linkedin", source_id=job_id, title=title, company=company,
            url=f"{BASE}/jobs/view/{job_id}", location=location,
            remote=True if "remote" in location.lower() else None,
            posted_at=parse_date(when.get("datetime")) if when else None,
        ))
    return out


def parse_description(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    node = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    if not node:
        return ""
    for br in node.find_all(["br", "li", "p"]):
        br.insert_after("\n")
    text = node.get_text(" ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ")).strip() if node else ""

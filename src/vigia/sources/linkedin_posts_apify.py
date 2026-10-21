"""LinkedIn hiring posts from LinkedIn's own post search, through an Apify actor.

`linkedin_posts` only sees what Google indexed (a fraction, ~2 days late). LinkedIn's own
content search sees every post, sorted by date, but needs a logged-in session. Using our own
account for that risks a restriction, so this source rents the search from an Apify actor
that runs on the actor's accounts: we send a search URL, it returns the posts as JSON.

It runs by hand (MCP `search_linkedin_posts`, with the searches chosen each time) or on the
schedule if enabled. It is paid per post, so it is kept small and capped twice:
  - each run passes `maxTotalChargeUsd`, which Apify enforces on the actor;
  - before running, the account's spend this month is read from Apify, and the run is
    skipped once it reaches `monthly_budget_usd`.

Posts go through the same hiring / not-hiring checks as `linkedin_posts`. The activity id is
the source id in both sources, so the overlap between them can be measured.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import quote

from vigia import db, http, vocab
from vigia.models import Job
from vigia.sources.base import Source, parse_date
from vigia.sources.linkedin_posts import ACTIVITY_RE, HIRING_RE, NOT_HIRING_RE

API = "https://api.apify.com/v2"
ACTOR = "supreme_coder~linkedin-post"
SEARCH = "https://www.linkedin.com/search/results/content/?datePosted=%22{date}%22&keywords={kw}&origin=FACETED_SEARCH&sortBy=%22date_posted%22"


def search_url(keywords: str, date_posted: str = "past-24h") -> str:
    return SEARCH.format(date=date_posted, kw=quote(keywords))


def to_job(item: dict, source: str, query: str) -> Job | None:
    """One actor result → Job, or None when it is not a hiring post."""
    m = ACTIVITY_RE.search(f"{item.get('urn', '')} {item.get('url', '')}")
    text = (item.get("text") or "").strip()
    if not m or not text:
        return None
    if not HIRING_RE.search(text) or NOT_HIRING_RE.search(text):
        return None
    return Job(
        source=source, source_id=m.group(1), title=text.splitlines()[0][:160],
        company=item.get("authorName") or "LinkedIn post", url=(item.get("url") or "").split("?")[0],
        location="", remote=None, posted_at=parse_date(item.get("postedAtISO")),
        description=text, tags=["post", f"query:{query}"],
    )


class LinkedInPostsApify(Source):
    name = "linkedin_posts_apify"

    def queries(self) -> list[str]:
        templates = self.cfg.get("templates") or ['hiring "{phrase}" remote']
        phrases = self.cfg.get("phrases") or vocab.search_phrases(self.cfg.get("families"))
        return [t.format(phrase=p) for p in phrases for t in templates]

    def fetch(self) -> list[Job]:
        token = os.environ.get("APIFY_TOKEN")
        if not token:
            raise RuntimeError("APIFY_TOKEN not set")
        headers = {"Authorization": f"Bearer {token}"}
        spent = self.spent_this_month(headers)
        budget = float(self.cfg.get("monthly_budget_usd", 1.0))
        if spent >= budget:
            raise RuntimeError(f"monthly budget reached: ${spent:.2f} of ${budget:.2f}")

        batch = self.cfg.get("queries") or self.next_queries()

        per_query = int(self.cfg.get("posts_per_query", 25))
        max_charge = min(float(self.cfg.get("max_charge_per_run_usd", 0.06)), budget - spent)
        date_posted = self.cfg.get("date_posted", "past-24h")
        out = []
        self.stats = {"spent_before_usd": round(spent, 4), "queries": []}
        for q in batch:
            items = self.run(headers, search_url(q, date_posted), per_query, max_charge / len(batch))
            jobs = [j for j in (to_job(i, self.name, q) for i in items) if j]
            self.stats["queries"].append({"query": q, "posts": len(items), "hiring": len(jobs)})
            out += jobs
        self.stats["spent_after_usd"] = round(self.spent_this_month(headers), 4)
        self.found = out
        return out

    def next_queries(self) -> list[str]:
        all_q = self.queries()
        n = int(self.cfg.get("queries_per_run", 1))
        start = int(db.get_state(f"{self.name}.cursor", "0")) % len(all_q)
        batch = [all_q[(start + i) % len(all_q)] for i in range(min(n, len(all_q)))]
        db.set_state(f"{self.name}.cursor", str(start + len(batch)))
        return batch

    def spent_this_month(self, headers: dict) -> float:
        resp = http._session.get(f"{API}/users/me/limits", headers=headers, timeout=30)
        resp.raise_for_status()
        return float(resp.json()["data"]["current"]["monthlyUsageUsd"])

    def run(self, headers: dict, url: str, limit: int, max_charge: float) -> list[dict]:
        started = datetime.now(timezone.utc)
        body = {"urls": [url], "limitPerSource": limit, "deepScrape": False, "numComments": 0, "numLikes": 0}
        try:
            resp = http._session.post(
                f"{API}/acts/{ACTOR}/run-sync-get-dataset-items", headers=headers, json=body, timeout=320,
                params={"maxTotalChargeUsd": f"{max_charge:.3f}", "timeout": 300})
            status = resp.status_code
            data = resp.json() if status in (200, 201) else []
        except Exception:  # noqa: BLE001 — a failed actor run must not stop the scheduler
            status, data = 0, []
        db.log_request("apify", "linkedin-post", status,
                       int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        return data if isinstance(data, list) else []

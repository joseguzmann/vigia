"""LinkedIn hiring posts: "We're hiring an AI engineer, remote LATAM" written in the feed.

Many roles, especially from recruiters and small companies in LATAM, are only announced as
posts, never as job listings. LinkedIn's post search needs a login, so discovery happens
off LinkedIn:

  1. a search API over Google's index (Serper) finds post URLs:
     `site:linkedin.com/posts hiring "AI Engineer" remote`
  2. the post's date is decoded from its activity id (no request needed) and old posts dropped
  3. the snippet is checked for job-seekers ("open to work") and agencies selling services
  4. survivors are read logged-out through the shared LinkedIn guest client (cooldown, budget)
     from the page's schema.org SocialMediaPosting (headline, full text, author, date)
  5. the post must read as hiring, then goes through the same flags as any job

Ideas and measurements from moeezahmadkhan/linkedin-scraper (MIT): Google's index has posts
~2 days old where Brave's has ~28; country lists in the query make results worse.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

from vigia import db, http, vocab
from vigia.linkedin_guest import Blocked, Guest
from vigia.models import Job
from vigia.sources.base import Source, parse_date

SERPER = "https://google.serper.dev/search"
ACTIVITY_RE = re.compile(r"activity[-:](\d{17,20})")
HIRING_RE = re.compile(
    r"\bhiring\b|we(?:'| a)re looking for|we are looking for|looking for an? |join (?:our|the) team|"
    r"open (?:role|position)|now hiring|estamos (?:buscando|contratando)|buscamos|vacante|"
    r"estamos com vaga|contratando|#hiring|#vacante", re.I)
NOT_HIRING_RE = re.compile(
    r"open to work|#opentowork|looking for (?:a )?(?:new )?(?:job|role|opportunit)|hire me|"
    r"i am looking for (?:a )?(?:job|role|position)|available for (?:work|hire)|"
    r"we provide|our services|i will (?:build|design|develop)|freelanc\w+ tip|buscando (?:empleo|trabajo)",
    re.I)


def activity_date(url: str) -> datetime | None:
    """LinkedIn activity ids carry their creation time: the top 41 bits are epoch ms."""
    m = ACTIVITY_RE.search(url or "")
    if not m:
        return None
    return datetime.fromtimestamp((int(m.group(1)) >> 22) / 1000, timezone.utc)


def parse_post(html_text: str) -> dict | None:
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html_text or "", re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for node in (data if isinstance(data, list) else data.get("@graph", [data])):
            if isinstance(node, dict) and node.get("@type") in ("SocialMediaPosting", "DiscussionForumPosting"):
                author = node.get("author") or {}
                return {"headline": node.get("headline") or "", "body": node.get("articleBody") or "",
                        "author": author.get("name", "") if isinstance(author, dict) else "",
                        "date": node.get("datePublished")}
    return None


class LinkedInPosts(Source):
    name = "linkedin_posts"

    def queries(self) -> list[str]:
        templates = self.cfg.get("templates") or ['site:linkedin.com/posts hiring "{phrase}" remote']
        phrases = self.cfg.get("phrases") or vocab.search_phrases(self.cfg.get("families"))
        return [t.format(phrase=p) for p in phrases for t in templates]

    def fetch(self) -> list[Job]:
        key = os.environ.get("SERPER_API_KEY")
        if not key:
            raise RuntimeError("SERPER_API_KEY not set")
        all_q = self.queries()
        n = int(self.cfg.get("queries_per_run", 6))
        start = int(db.get_state("linkedin_posts.cursor", "0")) % len(all_q)
        batch = [all_q[(start + i) % len(all_q)] for i in range(min(n, len(all_q)))]
        db.set_state("linkedin_posts.cursor", str(start + len(batch)))

        max_age = timedelta(days=int(self.cfg.get("posted_within_days", 14)))
        now = datetime.now(timezone.utc)
        candidates: dict[str, dict] = {}
        for q in batch:
            for r in self.search(key, q):
                link = r.get("link", "")
                if "/posts/" not in link:
                    continue
                when = activity_date(link)
                if when is None or now - when > max_age:
                    continue
                if NOT_HIRING_RE.search(f"{r.get('title', '')} {r.get('snippet', '')}"):
                    continue
                aid = ACTIVITY_RE.search(link).group(1)
                candidates.setdefault(aid, {"url": link.split("?")[0], "date": when, "query": q})

        known = db.known_source_ids(self.name, list(candidates))
        guest = Guest(self.name)
        out = []
        for aid, c in candidates.items():
            if aid in known:
                continue
            try:
                resp = guest.get("post", c["url"])
            except Blocked:
                break
            post = parse_post(resp.text) if resp is not None else None
            if not post:
                continue
            text = f"{post['headline']}\n{post['body']}"
            if not HIRING_RE.search(text) or NOT_HIRING_RE.search(text):
                continue
            title = post["headline"].strip().splitlines()[0][:160] if post["headline"] else text[:120]
            out.append(Job(
                source=self.name, source_id=aid, title=title,
                company=post["author"] or "LinkedIn post", url=c["url"], location="",
                remote=None, posted_at=parse_date(post["date"]) or c["date"],
                description=post["body"], tags=["post", f"query:{c['query']}"],
            ))
        return out

    def search(self, key: str, q: str) -> list[dict]:
        started = datetime.now(timezone.utc)
        try:
            resp = http._session.post(SERPER, timeout=30, headers={"X-API-KEY": key},
                                      json={"q": q, "num": int(self.cfg.get("results", 10)),
                                            "tbs": self.cfg.get("freshness", "qdr:w")})
            status = resp.status_code
            data = resp.json() if status == 200 else {}
        except Exception:  # noqa: BLE001 — one failed query must not stop the run
            status, data = 0, {}
        db.log_request("serper", "search", status,
                       int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
        return data.get("organic", [])

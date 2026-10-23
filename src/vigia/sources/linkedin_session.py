"""LinkedIn hiring posts from LinkedIn's own post search, with a signed-in LinkedIn account.

Optional, and only with an account created for this (never your own): automated use of a
LinkedIn account breaks its User Agreement and can get the account restricted. The search is
done by a separate container running stickerdaniel/linkedin-mcp-server, which keeps the
session; this source calls its `search_posts` tool over MCP (streamable HTTP).

It is paced like a person, not like a crawler:
  - only on weekdays, a few searches a day (`per_day`), at random times inside a local-time
    window, with a minimum gap between them; the day's times are drawn once and kept in state
  - one page of results per search, rotating through the search templates
  - nothing but post search: no profiles, people, messages or connections

And it stops itself: if a result looks like a checkpoint (login, captcha, "unusual activity"),
the source pauses (state `linkedin_session.paused`) and reports it as its error until someone
clears the state by hand. It never tries to get past a check.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from vigia import db, vocab
from vigia.models import Job
from vigia.sources.base import Source
from vigia.sources.linkedin_posts import HIRING_RE, NOT_HIRING_RE

CHECKPOINT_RE = re.compile(
    r"checkpoint|captcha|unusual activity|verify (?:it'?s|that it'?s) you|security verification|"
    r"sign in to continue|session (?:has )?expired|not (?:signed|logged) in|authentication (?:required|in progress)|"
    r"--login|restricted", re.I)
AGE_RE = re.compile(r"\d+\s?(?:s|m|h|d|w|mo|y)(?:\s*•.*)?|Edited.*", re.I)
log = logging.getLogger(__name__)
POST_URL_RE = re.compile(r"/posts/[^\s\"]*?(?:activity|share|ugcPost)-(\d{17,20})")


def posted_from_id(post_id: str):
    """LinkedIn post ids carry their creation time: the top bits are epoch milliseconds."""
    try:
        return datetime.fromtimestamp((int(post_id) >> 22) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


BUTTONS = {"follow", "join", "connect", "message", "promoted", "visit my website", "book an appointment"}
DEGREE_RE = re.compile(r"\s*•\s*(?:1st|2nd|3rd\+?|Following|Author)\b.*$", re.I)


def post_author(head: list[str]) -> str:
    """The author from a post header: the name before "• 2nd" when there is one, else the first
    line that is not the age ("3d •"), a button ("Join") or a degree alone."""
    for i, ln in enumerate(head):
        if DEGREE_RE.search(ln):
            name = DEGREE_RE.sub("", ln).strip()
            if name:
                return name
            if i and not AGE_RE.fullmatch(head[i - 1]) and head[i - 1].lower() not in BUTTONS:
                return head[i - 1]
    for ln in head:
        if not AGE_RE.fullmatch(ln) and ln.lower() not in BUTTONS and not ln.startswith("•"):
            return ln
    return "LinkedIn post"


def split_posts(text: str) -> list[dict]:
    """The tool's search_results text, one block per post: author and body."""
    out = []
    for block in re.split(r"(?m)^Feed post\s*$", text or ""):
        lines = [ln.strip() for ln in block.strip().splitlines()]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        # Header: author (or a group, then the author with "• 2nd"), headline, age, "Follow".
        # Without a Follow button (a connection, a group post) the body starts after the age.
        head_end = lines.index("Follow") if "Follow" in lines else next(
            (i for i, ln in enumerate(lines[:8]) if AGE_RE.fullmatch(ln)), 0)
        author = post_author(lines[:head_end] if head_end else lines[:1])
        body = lines[head_end + 1:] if head_end else lines[1:]
        # The tail is reaction counts and boilerplate.
        while body and (re.fullmatch(r"[\d\s​]*(reactions?|comments?|reposts?)?", body[-1], re.I)
                        or body[-1].startswith(("Are these results", "Your feedback", "Load more", "… more", "...more"))):
            body.pop()
        if body:
            out.append({"author": author, "text": "\n".join(body)})
    return out


def post_urls(references: list[dict]) -> list[tuple[str, str]]:
    """(url, id) of the post links, in result order, without the duplicate feed/update ones."""
    out = []
    for ref in references or []:
        m = POST_URL_RE.search(ref.get("url", ""))
        if ref.get("kind") == "feed_post" and m:
            out.append(("https://www.linkedin.com" + ref["url"].split("?")[0], m.group(1)))
    return out


class LinkedInSession(Source):
    name = "linkedin_session"

    # --- pacing ------------------------------------------------------------------

    def slots(self, now: datetime) -> list[datetime]:
        """Today's search times (local), drawn once per day and kept in state."""
        tz = ZoneInfo(self.cfg.get("timezone", "America/Guayaquil"))
        local = now.astimezone(tz)
        key = f"{self.name}.slots.{local.date().isoformat()}"
        saved = db.get_state(key)
        if saved:
            return [datetime.fromisoformat(s) for s in json.loads(saved)]
        out: list[datetime] = []
        if local.weekday() < 5:
            start, end = self.cfg.get("hours_local", [9, 19])
            gap = timedelta(minutes=int(self.cfg.get("min_gap_minutes", 90)))
            day0 = local.replace(hour=start, minute=0, second=0, microsecond=0)
            span = int((end - start) * 60)
            gap_min = int(gap.total_seconds() // 60)
            # As many as fit with the gap; random times, then spread by the gap so none are closer.
            n = min(int(self.cfg.get("per_day", 2)), span // max(gap_min, 1) + 1)
            slack = span - (n - 1) * gap_min
            offsets = sorted(random.randrange(max(slack, 1)) for _ in range(n))
            out = [day0 + timedelta(minutes=o + i * gap_min) for i, o in enumerate(offsets)]
        db.set_state(key, json.dumps([s.isoformat() for s in out]))
        return out

    def due_at(self, now: datetime) -> bool:
        if db.get_state(f"{self.name}.paused"):
            return False
        done = int(db.get_state(f"{self.name}.done.{now.astimezone(ZoneInfo(self.cfg.get('timezone', 'America/Guayaquil'))).date().isoformat()}", "0"))
        slots = self.slots(now)
        return done < len(slots) and now >= slots[done]

    # --- search ------------------------------------------------------------------

    def queries(self) -> list[str]:
        templates = self.cfg.get("templates") or ['"hiring" AND "{phrase}" AND ("LATAM" OR "Latin America")']
        phrases = self.cfg.get("phrases") or vocab.search_phrases(self.cfg.get("families"))
        return [t.format(phrase=p) for p in phrases for t in templates]

    def fetch(self) -> list[Job]:
        paused = db.get_state(f"{self.name}.paused")
        if paused:
            raise RuntimeError(f"paused: {paused}")
        all_q = self.queries()
        q = all_q[int(db.get_state(f"{self.name}.cursor", "0")) % len(all_q)]
        db.set_state(f"{self.name}.cursor", str(int(db.get_state(f"{self.name}.cursor", "0")) + 1))
        tz = ZoneInfo(self.cfg.get("timezone", "America/Guayaquil"))
        day = datetime.now(tz).date().isoformat()
        db.set_state(f"{self.name}.done.{day}", str(int(db.get_state(f"{self.name}.done.{day}", "0")) + 1))

        started = datetime.now()
        try:
            raw, is_error = asyncio.run(self.call(q))
        except Exception as e:  # noqa: BLE001 — the server being down is not a checkpoint
            db.log_request("linkedin_session", "search_posts", 0, 0)
            raise RuntimeError(f"search server unreachable: {type(e).__name__}") from e
        db.log_request("linkedin_session", "search_posts", 500 if is_error else 200,
                       int((datetime.now() - started).total_seconds() * 1000))
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        text = (data.get("sections") or {}).get("search_results", "") if isinstance(data, dict) else ""
        if is_error or not text:
            log.info("linkedin_session %r: no results (error=%s, %d chars)", q, is_error, len(raw or ""))
            if CHECKPOINT_RE.search(raw or ""):
                reason = re.sub(r"\s+", " ", raw)[:200]
                db.set_state(f"{self.name}.paused", f"{datetime.now(tz):%Y-%m-%d %H:%M} {reason}")
                raise RuntimeError(f"paused, LinkedIn asked for a check: {reason}")
            return []

        posts = split_posts(text)
        urls = post_urls(data.get("references", {}).get("search_results", []))
        out = []
        for i, p in enumerate(posts):
            if not HIRING_RE.search(p["text"]) or NOT_HIRING_RE.search(p["text"]):
                continue
            url, pid = urls[i] if i < len(urls) and len(urls) == len(posts) else ("", "")
            out.append(Job(
                source=self.name, source_id=pid or str(abs(hash(p["text"][:200]))),
                title=p["text"].splitlines()[0][:160], company=p["author"],
                url=url or f"https://www.linkedin.com/search/results/content/?keywords={q}",
                location="", remote=None, posted_at=posted_from_id(pid) if pid else None, description=p["text"],
                tags=["post", f"query:{q}"],
            ))
        log.info("linkedin_session %r: %d posts, %d hiring", q, len(posts), len(out))
        return out

    async def call(self, q: str) -> tuple[str, bool]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        url = self.cfg.get("server_url", "http://linkedin-mcp:8000/mcp")
        async with streamable_http_client(url) as streams:
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=240) as s:
                await s.initialize()
                res = await s.call_tool("search_posts", {"keywords": q, "date_posted": self.cfg.get("date_posted", "past-24h"),
                                                         "max_pages": int(self.cfg.get("max_pages", 1))})
        raw = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", []) or [])
        return raw, bool(getattr(res, "isError", False))

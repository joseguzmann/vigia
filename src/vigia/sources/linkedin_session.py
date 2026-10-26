"""LinkedIn hiring posts from LinkedIn's own post search, with a signed-in LinkedIn account.

Optional, and only with an account created for this (never your own): automated use of a
LinkedIn account breaks its User Agreement and can get the account restricted. The search is
done by a separate container running stickerdaniel/linkedin-mcp-server, which keeps the
session; this source calls its `search_posts` tool over MCP (streamable HTTP).

It is paced like a person, not like a crawler:
  - a few searches a day (`per_day`, on the `weekdays` set), at random times inside a local-time
    window, with a minimum gap between them; the day's times are drawn once and kept in state
  - a few result pages per search (`max_pages`), rotating through the search templates
  - nothing but post search: no profiles, people, messages or connections

And it stops itself: if a result looks like a checkpoint (login, captcha, "unusual activity"),
the source pauses (state `linkedin_session.paused`) and reports it as its error until someone
clears the state by hand. It never tries to get past a check.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
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
# The search page also gives permalinks as /feed/update/urn:li:activity:<id>/, with no author in them.
FEED_URL_RE = re.compile(r"/feed/update/urn:li:(?:activity|share|ugcPost):(\d{17,20})")
AGE_PARTS_RE = re.compile(r"(\d+)\s?(mo|s|m|h|d|w|y)\b", re.I)
AGE_UNIT_DAYS = {"s": 1 / 86400, "m": 1 / 1440, "h": 1 / 24, "d": 1, "w": 7, "mo": 30, "y": 365}


def age_of(line: str) -> tuple[int, float] | None:
    """The post header's age ("7h", "5d • Edited") as (count, unit in days)."""
    m = AGE_PARTS_RE.match(line or "")
    return (int(m.group(1)), AGE_UNIT_DAYS[m.group(2).lower()]) if m else None


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
        age = next((age_of(ln) for ln in lines[:10] if AGE_RE.fullmatch(ln) and age_of(ln)), None)
        body = lines[head_end + 1:] if head_end else lines[1:]
        while body and body[0].lower() in BUTTONS:  # a group post's "Join" can land after the header
            body.pop(0)
        # The tail is reaction counts and boilerplate.
        while body and (re.fullmatch(r"[\d\s​]*(reactions?|comments?|reposts?)?", body[-1], re.I)
                        or body[-1].startswith(("Are these results", "Your feedback", "Load more", "… more", "...more"))):
            body.pop()
        if body:
            out.append({"author": author, "text": "\n".join(body), "age": age})
    return out


def post_urls(references: list[dict]) -> list[tuple[str, str]]:
    """(url, id) of the post links, one per post. A post can come as /posts/<author>_…-share-<id>
    and as /feed/update/ urns for its activity and its share, created within seconds of each other:
    those collapse into one, keeping the /posts/ form (it names the author)."""
    found = []
    for ref in references or []:
        url = ref.get("url", "")
        m = POST_URL_RE.search(url) or FEED_URL_RE.search(url)
        if ref.get("kind") == "feed_post" and m:
            found.append(("https://www.linkedin.com" + url.split("?")[0], m.group(1)))
    out: list[tuple[str, str]] = []
    for url, pid in found:
        t = posted_from_id(pid)
        twin = next((k for k, (u, i) in enumerate(out)
                     if t and posted_from_id(i) and abs((posted_from_id(i) - t).total_seconds()) < 10), None)
        if twin is None:
            out.append((url, pid))
        elif "/posts/" in url and "/posts/" not in out[twin][0]:
            out[twin] = (url, pid)
    return out


def _slug(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def author_handles(references: list[dict]) -> dict[str, str]:
    """Author name → profile path ("/in/<handle>"), from the person/company references: a
    /posts/<handle>_… permalink often uses a handle that is not the name ("rojasana"),
    and a post with no permalink can still be found on its author's activity page."""
    out = {}
    for ref in references or []:
        m = re.match(r"/(in|company)/([^/?]+)", ref.get("url", ""))
        if ref.get("kind") in ("person", "company") and m and isinstance(ref.get("text"), str):
            out.setdefault(ref["text"].strip(), f"/{m.group(1)}/{unicodedata.normalize('NFC', unquote(m.group(2)))}")
    return out


def activity_url(path: str) -> str:
    """Where an author's recent posts are listed."""
    return f"https://www.linkedin.com{path}/" + ("recent-activity/all/" if path.startswith("/in/") else "posts/")


def _window(age: tuple[int, float] | None, now: datetime):
    """When a post with this header age was created: LinkedIn rounds down ("5d" is 5 to 6 days)."""
    if not age:
        return None
    n, unit = age
    slack = timedelta(hours=1)
    return now - timedelta(days=(n + 1) * unit) - slack, now - timedelta(days=n * unit) + slack


def match_urls(posts: list[dict], urls: list[tuple[str, str]], now: datetime | None = None,
               handles: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """A (url, id) per post, or ("", ""). The captured permalinks are not in result order and
    can be fewer than the posts, so match by author first (/posts/<author-handle>_activity-…),
    then by time (the id carries its creation time, the header its age) when only one post
    fits, then by position among what is left only when the counts agree."""
    now = now or datetime.now(timezone.utc)
    out: list[tuple[str, str]] = [("", "")] * len(posts)
    left = list(urls)
    for i, p in enumerate(posts):
        slugs = {x for x in (_slug(p["author"]), _slug((handles or {}).get(p["author"], "").rsplit("/", 1)[-1])) if x}
        hits = [u for u in left if "/posts/" in u[0] and _slug(u[0].split("/posts/")[1].split("_")[0]) in slugs]
        if len(hits) == 1:
            out[i] = hits[0]
            left.remove(hits[0])
    changed = True
    while changed:
        changed = False
        free = [i for i, u in enumerate(out) if not u[0]]
        windows = {i: _window(posts[i].get("age"), now) for i in free}
        fits = {u: [i for i in free if windows[i] and posted_from_id(u[1])
                    and windows[i][0] <= posted_from_id(u[1]) <= windows[i][1]] for u in left}
        for u, cands in fits.items():
            if len(cands) == 1 and sum(cands[0] in c for c in fits.values()) == 1:
                out[cands[0]] = u
                left.remove(u)
                changed = True
                break
    unmatched = [i for i, u in enumerate(out) if not u[0]]
    if unmatched and len(unmatched) == len(left):
        for i, u in zip(unmatched, left):
            out[i] = u
    return out


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def permalink_in_profile(data: dict, post_text: str) -> tuple[str, str] | None:
    """The (url, id) of a post in its author's profile page, read with get_person_profile.

    The page's Activity preview shows the latest posts in order, each under its age line ("7h"),
    and its feed_post references come in that same order, one per post: the post is the one whose
    text holds the first line of ours."""
    page = (data.get("sections") or {}).get("main_profile", "")
    start = page.find("\nActivity")
    if start < 0:
        return None
    lines = page[start:].splitlines()
    items, current = [], None
    for ln in lines:
        if AGE_RE.fullmatch(ln.strip()):
            current = []
            items.append(current)
        elif current is not None:
            current.append(ln)
    first = next((ln for ln in (post_text or "").splitlines() if ln.strip()), "")
    key = _norm(first)[:60]
    idx = next((i for i, it in enumerate(items) if key and key in _norm("\n".join(it))), None)
    feed = [r for r in (data.get("references") or {}).get("main_profile", []) if r.get("kind") == "feed_post"]
    if idx is None or idx >= len(feed):
        return None
    url = feed[idx]["url"]
    m = POST_URL_RE.search(url) or FEED_URL_RE.search(url)
    return ("https://www.linkedin.com" + url.split("?")[0], m.group(1)) if m else None


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
        if local.weekday() in self.cfg.get("weekdays", [0, 1, 2, 3, 4]):
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
        """Every template with the shared `phrases`, or with its own: a template can be
        {"template": ..., "phrases": [...]} (Spanish posts need Spanish phrases)."""
        templates = self.cfg.get("templates") or ['"hiring" AND "{phrase}" AND ("LATAM" OR "Latin America")']
        phrases = self.cfg.get("phrases") or vocab.search_phrases(self.cfg.get("families"))
        out = []
        for t in templates:
            text, own = (t["template"], t.get("phrases") or phrases) if isinstance(t, dict) else (t, phrases)
            out += [text.format(phrase=p) for p in own]
        return out

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
        now = datetime.now(timezone.utc)
        refs = data.get("references", {}).get("search_results", [])
        handles = author_handles(refs)
        urls = match_urls(posts, post_urls(refs), now, handles)
        out = []
        for i, p in enumerate(posts):
            if not HIRING_RE.search(p["text"]) or NOT_HIRING_RE.search(p["text"]):
                continue
            url, pid = urls[i]
            out.append(Job(
                source=self.name, source_id=pid or hashlib.sha1(p["text"][:200].encode()).hexdigest()[:16],
                title=p["text"].splitlines()[0][:160], company=p["author"],
                url=url or (activity_url(handles[p["author"]]) if p["author"] in handles
                            else f"https://www.linkedin.com/search/results/content/?keywords={q}"),
                location="", remote=None, description=p["text"],
                posted_at=posted_from_id(pid) if pid else (now - timedelta(days=p["age"][0] * p["age"][1]) if p.get("age") else None),
                tags=["post", f"query:{q}"],
            ))
        log.info("linkedin_session %r: %d posts, %d hiring", q, len(posts), len(out))
        try:
            self.find_links(int(self.cfg.get("links_per_search", 1)))
        except Exception as e:  # noqa: BLE001 — a missing link must not cost the search's results
            log.warning("linkedin_session: link lookup failed: %s", e)
        return out

    # --- permalinks for the good posts ------------------------------------------------

    def find_links(self, limit: int, min_match: int = 7) -> int:
        """For posts that came without a permalink (their link is the author's activity page) and
        that the reader scored `min_match`+, read the author's profile once and take the post's
        permalink from it: one page load each, at most `limit` per search, each post tried once."""
        if limit <= 0:
            return 0
        rows = db.conn().execute(
            "SELECT j.id, j.url, j.description FROM jobs j JOIN job_readings r ON r.job_id = j.id "
            "WHERE j.source = ? AND j.url LIKE '%/in/%/recent-activity/%' AND r.match >= ? "
            "ORDER BY j.first_seen DESC LIMIT 30", (self.name, min_match)).fetchall()
        found = tried = 0
        for r in rows:
            key = f"{self.name}.link.{r['id']}"
            if tried >= limit or db.get_state(key):
                continue
            tried += 1
            db.set_state(key, "tried")
            handle = r["url"].split("/in/")[1].split("/")[0]
            raw, is_error = asyncio.run(self.call_tool("get_person_profile", {"linkedin_username": handle}))
            db.log_request("linkedin_session", "profile", 500 if is_error else 200, 0)
            if is_error and CHECKPOINT_RE.search(raw or ""):
                db.set_state(f"{self.name}.paused", f"{datetime.now():%Y-%m-%d %H:%M} {re.sub(chr(10), ' ', raw)[:200]}")
                raise RuntimeError("paused, LinkedIn asked for a check on a profile")
            try:
                hit = permalink_in_profile(json.loads(raw), r["description"])
            except ValueError:
                hit = None
            if hit:
                when = posted_from_id(hit[1])
                db.set_job_link(r["id"], hit[0], when.isoformat(timespec="seconds") if when else None)
                db.set_state(key, hit[0])
                found += 1
        if tried:
            log.info("linkedin_session: permalinks found for %d of %d posts", found, tried)
        return found

    async def call(self, q: str) -> tuple[str, bool]:
        return await self.call_tool("search_posts", {"keywords": q, "date_posted": self.date_posted(),
                                                     "max_pages": int(self.cfg.get("max_pages", 1))})

    async def call_tool(self, name: str, args: dict) -> tuple[str, bool]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        url = self.cfg.get("server_url", "http://linkedin-mcp:8000/mcp")
        async with streamable_http_client(url) as streams:
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=240) as s:
                await s.initialize()
                res = await s.call_tool(name, args)
        raw = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", []) or [])
        return raw, bool(getattr(res, "isError", False))

    def date_posted(self) -> str:
        """`date_posted`, or `backfill_date_posted` on the dates in `backfill_days` (a one-off sweep
        further back for templates that are new)."""
        today = datetime.now(ZoneInfo(self.cfg.get("timezone", "America/Guayaquil"))).date().isoformat()
        if today in (self.cfg.get("backfill_days") or []):
            return self.cfg.get("backfill_date_posted", "past-month")
        return self.cfg.get("date_posted", "past-24h")

"""Source adapters. Each one fetches postings and normalizes them into `Job`.

To add a source: subclass `Source`, implement `fetch()`, register it in `sources/__init__.py`.
A source receives its own config block (`sources.<name>` in vigia.yaml).
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

from vigia.models import Job


class Source:
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def fetch(self) -> list[Job]:
        raise NotImplementedError


def strip_html(text: str | None) -> str:
    """HTML to readable plain text, keeping paragraph breaks."""
    if not text:
        return ""
    text = re.sub(r"(?i)<\s*(br|p|/p|/li|/h\d|/div)\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_date(value) -> datetime | None:
    """Accepts epoch seconds/ms, ISO strings, or None."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if isinstance(value, (int, float)):
            if value > 1e12:
                value = value / 1000
            return datetime.fromtimestamp(value, timezone.utc)
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


LD_OPEN_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>')


def ld_json_blocks(html_text: str):
    """Parsed ld+json blocks of a page. Linear: an unclosed tag ends the scan instead of
    making every later tag rescan the rest of the page (a truncated page took ~30 s)."""
    html_text = html_text or ""
    pos = 0
    while m := LD_OPEN_RE.search(html_text, pos):
        end = html_text.find("</script>", m.end())
        if end < 0:
            return
        try:
            yield json.loads(html_text[m.end():end])
        except ValueError:
            pass
        pos = end


def job_posting_ld(html_text: str) -> dict | None:
    """The schema.org JobPosting embedded in a job page, if any, with countries flattened.

    Many boards embed it for Google Jobs. `applicantLocationRequirements` is the
    authoritative list of countries a remote role accepts, when present.
    """
    for data in ld_json_blocks(html_text):
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                req = node.get("applicantLocationRequirements") or []
                req = req if isinstance(req, list) else [req]
                node["_countries"] = [r.get("name") for r in req if isinstance(r, dict) and r.get("name")]
                return node
    return None


def ld_to_job(source: str, source_id: str, url: str, ld: dict) -> Job:
    """A schema.org JobPosting (from `job_posting_ld`) as a Job. The countries a remote role
    accepts, when listed, become the location and a `countries:` tag."""
    countries = ld.get("_countries") or []
    remote = ld.get("jobLocationType") == "TELECOMMUTE"
    places = []
    locs = ld.get("jobLocation") or []
    for place in locs if isinstance(locs, list) else [locs]:
        addr = (place or {}).get("address") or {}
        if isinstance(addr, str):
            places.append(addr)
            continue
        country = addr.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        bits = [addr.get("addressLocality") or addr.get("streetAddress"), country]
        places.append(", ".join(b for b in bits if b))
    if countries:
        location = "Remote: " + ", ".join(countries[:6]) + ("…" if len(countries) > 6 else "")
    else:
        location = "; ".join(p for p in places if p) or ("Remote" if remote else "")
    org = ld.get("hiringOrganization") or {}
    return Job(
        source=source, source_id=source_id, title=html.unescape(ld.get("title") or ""),
        company=html.unescape((org.get("name") if isinstance(org, dict) else str(org)) or ""), url=url,
        location=location + (" (Remote)" if remote and "remote" not in location.lower() else ""),
        remote=remote, posted_at=parse_date(ld.get("datePosted")),
        description=strip_html(ld.get("description")),
        tags=[f"countries:{', '.join(countries)}"] if countries else [],
    )

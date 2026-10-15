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

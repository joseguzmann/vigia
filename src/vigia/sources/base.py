"""Source adapters. Each one fetches postings and normalizes them into `Job`.

To add a source: subclass `Source`, implement `fetch()`, register it in `sources/__init__.py`.
A source receives its own config block (`sources.<name>` in vigia.yaml).
"""

from __future__ import annotations

import html
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

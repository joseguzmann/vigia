"""The one shape every source normalizes into."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    source: str                 # adapter name: "getonbrd", "greenhouse", "linkedin"...
    source_id: str              # id inside that source, stable across runs
    title: str
    company: str
    url: str
    location: str = ""          # free text as the source gives it
    remote: bool | None = None  # None when the source does not say
    posted_at: datetime | None = None
    description: str = ""       # plain text
    salary: str = ""            # free text, e.g. "USD 4,000–6,500/month"
    tags: list[str] = field(default_factory=list)

    @property
    def dedupe_key(self) -> str:
        """Same company + same title = same role, whichever board it came from.

        Trailing geography is dropped, so a role posted once per country
        ("... - LATAM", "... - Brazil", "... (Mexico)") counts once.
        """
        return dedupe_key(self.company, self.title)


GEO_SUFFIX_RE = re.compile(
    r"\s*(?:[-–|,]\s*|\()\s*(?:latam|latin america|dr|dominican republic|brazil|brasil|mexico|méxico|"
    r"colombia|argentina|chile|peru|perú|uruguay|costa rica|ecuador|bolivia|paraguay|guatemala|"
    r"us|usa|united states|canada|uk|europe|emea|apac|remote|global|worldwide)\)?\s*$", re.I)


def clean_title(title: str) -> str:
    t = (title or "").strip()
    while True:
        new = GEO_SUFFIX_RE.sub("", t)
        if new == t:
            return t
        t = new


def dedupe_key(company: str, title: str) -> str:
    return f"{_norm(company)}|{_norm(clean_title(title))}"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())

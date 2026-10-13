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
        """Same company + same title = same role, whichever board it came from."""
        return f"{_norm(self.company)}|{_norm(self.title)}"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())

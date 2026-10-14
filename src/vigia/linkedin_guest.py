"""The only way Vigía talks to LinkedIn: logged-out pages, slowly, with a shared block.

Measured from a home IP (2026-10-13/14): LinkedIn serves 2–7 requests, answers 429, and
keeps refusing for ~3 hours. Knocking during that window only extends it. So:

  - one persisted `blocked_until` shared by every LinkedIn source (job search, hiring posts):
    after a 429 nobody calls LinkedIn until it has passed;
  - a per-run request budget;
  - no redirects: a logged-out page that bounces to the login wall is skipped, not followed
    (an "authwall" on one post says nothing about the next one, so it is not a block).

Design adapted from moeezahmadkhan/linkedin-scraper (MIT), which uses the same circuit
breaker and cooldown ideas.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone

from vigia import config, db, http

STATE_KEY = "linkedin.blocked_until"


class Blocked(Exception):
    """LinkedIn is in cooldown; skip LinkedIn work for this run."""


class Guest:
    def __init__(self, source: str):
        cfg = config.source("linkedin")
        self.source = source
        self.cooldown = int(cfg.get("cooldown_minutes", 200))
        self.budget = int(cfg.get("request_budget", 12))
        self.pause = cfg.get("pause_seconds", [6, 12])
        self.used = 0

    @staticmethod
    def blocked_until() -> datetime | None:
        v = db.get_state(STATE_KEY)
        return datetime.fromisoformat(v) if v else None

    def check(self) -> None:
        until = self.blocked_until()
        if until and datetime.now(timezone.utc) < until:
            raise Blocked(f"LinkedIn cooldown until {until.isoformat(timespec='minutes')}")
        if self.used >= self.budget:
            raise Blocked(f"request budget of {self.budget} spent")

    def get(self, kind: str, url: str, params: dict | None = None):
        """GET a logged-out LinkedIn page. Returns the response, or None for a redirect /
        missing page. Raises Blocked when LinkedIn says 429 (and records the cooldown)."""
        self.check()
        if self.used:
            low, high = self.pause
            time.sleep(random.uniform(low, high))
        self.used += 1
        try:
            return http.get(self.source, kind, url, params=params, allow_redirects=False)
        except http.Throttled:
            until = datetime.now(timezone.utc) + timedelta(minutes=self.cooldown)
            db.set_state(STATE_KEY, until.isoformat(timespec="seconds"))
            raise Blocked(f"429 from LinkedIn; cooling down until {until.isoformat(timespec='minutes')}")

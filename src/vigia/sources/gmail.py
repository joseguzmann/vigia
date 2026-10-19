"""The mailbox as a source: job alerts become postings; replies are kept for the person.

All the work is in `vigia.mail`; this adapter only plugs it into the scheduler, so the
postings found in alerts are flagged and stored like any other source's.
"""

from __future__ import annotations

from vigia import mail
from vigia.models import Job
from vigia.sources.base import Source


class Gmail(Source):
    name = "gmail"

    def fetch(self) -> list[Job]:
        return mail.sync(self.cfg)

"""MCP server over stdio: the job radar and the application log as tools for an assistant.

Run it where the database lives and connect over any stdio pipe, e.g. from another machine:

    ssh <host> docker exec -i vigia vigia mcp

Nothing listens on a port. Logs go to stderr; stdout carries the protocol only.
"""

from __future__ import annotations

import functools
import logging
import sys
import threading
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from vigia import applications as apps
from vigia import report
from vigia.web import store

mcp = MCPServer("vigia", instructions=(
    "Vigía is a job radar and the source of truth for the job search. Jobs come from many boards "
    "and are judged fit / extra (abroad with visa or relocation) / review / out. Every application "
    "is recorded here with the CV, experiment variant and status history. Record an application "
    "as soon as it is sent (call next_application first for its number and variant), and move "
    "its status when an email or the user says something changed."))


_conn = None
_lock = threading.Lock()


def tool(fn):
    """Register a tool that runs alone: calls may arrive concurrently, and they share one
    connection and must see each other's writes in order."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with _lock:
            try:
                return fn(*args, **kwargs)
            except (LookupError, ValueError) as e:  # a bad value: say which, so it can be fixed
                raise ToolError(str(e)) from e
    return mcp.tool()(run)


def _c():
    """One connection for the life of the server (WAL + busy timeout, like the collector)."""
    global _conn
    if _conn is None:
        _conn = store.connect()
    return _conn


def _job_summary(j: dict) -> dict:
    return {k: j.get(k) for k in ("id", "title", "company", "url", "verdict", "score", "location",
                                  "salary", "age", "years", "level", "category", "sources", "reasons")} | {
        "applied": (j.get("last_action") or {}).get("variant") if (j.get("last_action") or {}).get("action") == "applied" else None}


# --- jobs ------------------------------------------------------------------------

@tool
def health() -> str:
    """Last run of every source, errors, and how many jobs are stored per verdict."""
    return report.health()


@tool
def new_jobs(since: str | None = None, limit: int = 40, include_out: bool = False) -> str:
    """Jobs first seen since the last review (or since an ISO timestamp), freshest first,
    grouped by verdict. Each one shows its job id and whether it was already applied to.
    Does not move the review point: call mark_reviewed after going through them."""
    return report.new_jobs(since=since, include_out=include_out, limit=limit)


@tool
def mark_reviewed() -> str:
    """Set the review point to now, so the next new_jobs starts from here."""
    report.mark_reviewed()
    return f"Reviewed up to {report.last_review()}"


@tool
def search_jobs(query: str = "", verdicts: list[str] | None = None, category: str = "",
                level: str = "", max_age_days: int | None = None, not_acted_on: bool = False,
                limit: int = 30) -> dict:
    """Stored jobs matching a title/company search and filters, freshest first.
    verdicts: any of fit, extra, review, out (default fit, extra, review).
    not_acted_on: hide jobs already applied to, skipped or saved."""
    rows, total = store.list_jobs(_c(), verdicts or list(store.DEFAULT_VERDICTS), "", category, level,
                                  max_age_days, query, not_acted_on, limit)
    return {"total": total, "jobs": [_job_summary(j) for j in rows]}


@tool
def get_job(job_id: int) -> dict:
    """One job with its full description, flags, boards it appeared on and what was done with it."""
    j = store.get_job(_c(), job_id)
    if j is None:
        raise ValueError(f"no job {job_id}")
    return j


@tool
def skip_job(job_id: int, reason: str, note: str = "") -> str:
    """Record that a job was looked at and not applied to.
    reason: years | location | stack | closed | level | other."""
    store.record_action(_c(), job_id, "skipped", reason, note)
    return f"job {job_id} skipped ({reason})"


# --- applications ----------------------------------------------------------------

@tool
def next_application() -> dict:
    """The number the next application will get and its experiment variant
    (even numbers A2 = the CV for the role's family, odd numbers A1 = the AI CV)."""
    return apps.next_variant(_c())


@tool
def record_application(company: str = "", title: str = "", url: str = "", job_id: int | None = None,
                       cv: str | None = None, variant: str | None = None, family: str | None = None,
                       channel: str | None = None, level: str | None = None, extra: bool | None = None,
                       inbound: bool = False, material: str | None = None, docs: str | None = None,
                       notes: str | None = None, status: str = "applied", applied_at: str | None = None,
                       number: int | None = None) -> dict:
    """Record an application. Pass job_id (or a url Vigía has) and company, title, level, posting
    age and the visa flag are filled in; otherwise company and title are required.
    variant: A1 | A2 | baseline. family: ai | backend | fullstack | frontend | mobile.
    channel: company | linkedin | getonbrd | platform | email | referral | other.
    status: applied (default) or preparing. applied_at: YYYY-MM-DD, default today.
    docs: where the written answers live. number: leave empty to take the next one."""
    return apps.add(company=company, title=title, url=url, job_id=job_id, status=status,
                    applied_at=applied_at, origin="assistant", c=_c(), number=number, cv=cv,
                    variant=variant, family=family, channel=channel, level=level, extra=extra,
                    inbound=inbound, material=material, docs=docs, notes=notes)


@tool
def set_application_status(status: str, number: int | None = None, app_id: int | None = None,
                           note: str = "", origin: str = "assistant", on: str | None = None) -> dict:
    """Move an application to a new status and log it.
    status: preparing | applied | ack (automatic acknowledgement) | screen (call with a person) |
    technical | final | offer | rejected | ghost (no reply 21+ days) | closed (dropped by us).
    origin: email | user | assistant. on: YYYY-MM-DD when it happened, if not today."""
    return apps.set_status(status, number=number, app_id=app_id, note=note, origin=origin, on=on, c=_c())


@tool
def update_application(fields: dict[str, Any], number: int | None = None, app_id: int | None = None) -> dict:
    """Correct recorded details of an application (not its status). fields may hold: company,
    title, url, job_id, applied_at, channel, inbound, cv, variant, family, level, extra,
    age_days, material, docs, notes."""
    return apps.update(number=number, app_id=app_id, c=_c(), **fields)


@tool
def list_applications(status: str = "", open_only: bool = False, query: str = "", limit: int = 100) -> list[dict]:
    """Applications, newest first. open_only: still waiting or in process."""
    return apps.list_apps(status=status, open_only=open_only, q=query, limit=limit, c=_c())


@tool
def get_application(number: int | None = None, app_id: int | None = None) -> dict:
    """One application with its full status history."""
    return apps.get(number=number, app_id=app_id, c=_c())


@tool
def experiment_stats() -> dict:
    """Cold applications and how many got a step with a person within 14 days, overall and by
    variant, visa flag, family, level, channel and posting freshness."""
    return apps.stats(_c())


@tool
def export_applications_markdown() -> str:
    """Every application and its status history as Markdown, for keeping a readable copy."""
    return apps.export_markdown(_c())


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    mcp.run()

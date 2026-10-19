"""FastAPI app. Server-rendered pages, plain HTML forms, no JavaScript build.

Meant to be reached only through an SSH tunnel: it has no login of its own.
"""

from __future__ import annotations

import html
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from vigia import applications as apps
from vigia import db, mail, report
from vigia.sources import REGISTRY
from vigia.web import store

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["local"] = lambda iso: report._local(iso) if iso else ""
templates.env.filters["pct"] = lambda x: f"{x * 100:.0f}%"
templates.env.filters["pretty"] = lambda d: json.dumps(d, indent=2, ensure_ascii=False)
templates.env.globals["verdict_label"] = lambda v: store.VERDICT_LABELS.get(v, v or "")
templates.env.filters["fromjson"] = lambda s: json.loads(s) if s else []
templates.env.filters["http"] = lambda u: u if isinstance(u, str) and re.match(r"https?://", u) else ""

_report_lock = threading.Lock()


def get_conn():
    c = store.connect()
    try:
        yield c
    finally:
        c.close()


def _back(next_url: str, default: str) -> str:
    return next_url if next_url.startswith("/") and not next_url.startswith("//") else default


def create_app() -> FastAPI:
    app = FastAPI(title="Vigía", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz(c=Depends(get_conn)):
        c.execute("SELECT 1").fetchone()
        return "ok"

    @app.get("/")
    def jobs(request: Request, c=Depends(get_conn),
             verdict: list[str] = Query(default=[]), source: str = "", category: str = "",
             level: str = "", max_age: str = "", q: str = "", open: str = "", f: str = ""):
        if not f and not verdict:
            verdict = list(store.DEFAULT_VERDICTS)
        age = int(max_age) if max_age.strip().isdigit() else None
        rows, total = store.list_jobs(c, verdict, source, category, level, age, q, bool(open))
        filters = {"verdict": verdict, "source": source, "category": category, "level": level,
                   "max_age": max_age, "q": q, "open": open}
        return templates.TemplateResponse(request, "jobs.html", {
            "page": "jobs", "jobs": rows, "total": total, "filters": filters,
            "sources": store.known_sources(c), "verdicts": store.VERDICTS,
            "categories": store.known_categories(c), "levels": store.LEVELS,
            "verdict_labels": store.VERDICT_LABELS,
            "back": "/?" + urlencode({**filters, "f": "1"}, doseq=True)})

    @app.get("/jobs/{job_id}")
    def job(request: Request, job_id: int, c=Depends(get_conn), back: str = "/"):
        j = store.get_job(c, job_id)
        if j is None:
            raise HTTPException(404, "No such job")
        return templates.TemplateResponse(request, "job.html", {
            "page": "jobs", "job": j, "back": _back(back, "/"), "reasons": store.SKIP_REASONS,
            "next": apps.next_variant(c), "variants": apps.VARIANTS, "families": apps.FAMILIES,
            "channels": apps.CHANNELS, "statuses": apps.STATUSES})

    @app.post("/jobs/{job_id}/actions")
    def add_action(job_id: int, c=Depends(get_conn), action: str = Form(...), reason: str = Form(""),
                   note: str = Form(""), back: str = Form("/")):
        try:
            store.record_action(c, job_id, action, reason, note)
        except LookupError:
            raise HTTPException(404, "No such job")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/jobs/{job_id}?" + urlencode({"back": _back(back, "/")}), status_code=303)

    @app.post("/jobs/{job_id}/apply")
    def apply(job_id: int, c=Depends(get_conn), variant: str = Form(""), family: str = Form(""),
              channel: str = Form(""), cv: str = Form(""), material: str = Form(""), notes: str = Form(""),
              extra: str = Form(""), status: str = Form("applied"), back: str = Form("/")):
        try:
            apps.add(job_id=job_id, status=status, origin="user", variant=variant or None,
                     family=family or None, channel=channel or None, cv=cv.strip() or None,
                     material=material.strip() or None, notes=notes.strip() or None,
                     extra=bool(extra), c=c)
        except LookupError:
            raise HTTPException(404, "No such job")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/jobs/{job_id}?" + urlencode({"back": _back(back, "/")}), status_code=303)

    @app.post("/actions/{action_id}/delete")
    def delete(action_id: int, c=Depends(get_conn), next: str = Form("/")):
        job_id = store.delete_action(c, action_id)
        return RedirectResponse(_back(next, f"/jobs/{job_id}" if job_id else "/"), status_code=303)

    @app.get("/applications")
    def applications(request: Request, c=Depends(get_conn), status: str = "", open: str = "", q: str = ""):
        status = status if status in apps.STATUSES else ""
        rows = apps.list_apps(status=status, open_only=bool(open), q=q, c=c)
        return templates.TemplateResponse(request, "applications.html", {
            "page": "applications", "apps": rows, "statuses": apps.STATUSES,
            "filters": {"status": status, "open": open, "q": q}, "positive": apps.POSITIVE,
            "back": "/applications?" + urlencode({"status": status, "open": open, "q": q})})

    @app.get("/applications/{app_id}")
    def application(request: Request, app_id: int, c=Depends(get_conn), back: str = "/applications"):
        try:
            a = apps.get(app_id=app_id, c=c)
        except LookupError:
            raise HTTPException(404, "No such application")
        return templates.TemplateResponse(request, "application.html", {
            "page": "applications", "a": a, "statuses": apps.STATUSES, "back": _back(back, "/applications"),
            "emails": mail.listing(c, application_id=app_id)})

    @app.post("/applications/{app_id}/status")
    def application_status(app_id: int, c=Depends(get_conn), status: str = Form(...), note: str = Form(""),
                           on: str = Form(""), next: str = Form("/applications")):
        try:
            apps.set_status(status, app_id=app_id, note=note.strip(), origin="user", on=on or None, c=c)
        except LookupError:
            raise HTTPException(404, "No such application")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(_back(next, "/applications"), status_code=303)

    @app.get("/emails")
    def emails(request: Request, c=Depends(get_conn), kind: str = "", pending: str = ""):
        kind = kind if kind in mail.KINDS else ""
        rows = mail.listing(c, kind=kind, unreviewed=bool(pending))
        counts = {r["kind"]: r["n"] for r in c.execute(
            "SELECT kind, COUNT(*) n FROM emails WHERE reviewed_at IS NULL AND kind != 'other' GROUP BY kind")}
        other = c.execute("SELECT COUNT(*) FROM emails WHERE kind = 'other'").fetchone()[0]
        return templates.TemplateResponse(request, "emails.html", {
            "page": "emails", "emails": rows, "kinds": mail.KEPT, "counts": counts, "other": other,
            "filters": {"kind": kind, "pending": pending},
            "back": "/emails?" + urlencode({"kind": kind, "pending": pending})})

    @app.get("/emails/{gmail_id}")
    def email(request: Request, gmail_id: str, c=Depends(get_conn), back: str = "/emails"):
        try:
            e = mail.get(gmail_id, c)
        except LookupError:
            raise HTTPException(404, "No such email")
        if e["kind"] == "other":
            raise HTTPException(404, "Not kept: only its id is stored")
        return templates.TemplateResponse(request, "email.html", {
            "page": "emails", "e": e, "postings": json.loads(e["postings"] or "[]"),
            "kinds": mail.KINDS, "back": _back(back, "/emails")})

    @app.post("/emails/{gmail_id}")
    def email_update(gmail_id: str, c=Depends(get_conn), kind: str = Form(""), number: str = Form(""),
                     reviewed: str = Form(""), next: str = Form("/emails")):
        try:
            if kind:
                mail.set_kind(gmail_id, kind, int(number) if number.strip() else None, c)
            if reviewed:
                mail.mark_reviewed([gmail_id], c)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(_back(next, "/emails"), status_code=303)

    @app.get("/experiment")
    def experiment(request: Request, c=Depends(get_conn)):
        return templates.TemplateResponse(request, "experiment.html", {
            "page": "experiment", "s": apps.stats(c), "skips": store.skip_reasons(c)})

    @app.get("/sources")
    def sources(request: Request, c=Depends(get_conn)):
        from vigia import config
        rows = store.sources_overview(c, REGISTRY, config.source)
        return templates.TemplateResponse(request, "sources.html", {
            "page": "sources", "sources": rows,
            "enabled": sum(s["enabled"] for s in rows),
            "boards": sum(len(s["searches"]) for s in rows if s["boards"] and s["enabled"])})

    @app.get("/health")
    def health(request: Request, c=Depends(get_conn), source: str = "linkedin", hours: int = 72):
        if source not in REGISTRY:
            source = "linkedin"
        hours = max(1, min(hours, 24 * 30))
        # report.* use the module-level connection; lend it this request's one.
        with _report_lock:
            saved, db._conn = db._conn, c
            try:
                health_md, throttle_md = report.health(), report.throttling(source, hours)
            finally:
                db._conn = saved
        return templates.TemplateResponse(request, "health.html", {
            "page": "health", "health": md_to_html(health_md), "throttle": md_to_html(throttle_md),
            "sources": list(REGISTRY), "source": source, "hours": hours})

    return app


def md_to_html(text: str) -> Markup:
    """Just enough Markdown for report.health() / report.throttling(): headings, tables, lists."""
    out, table, items = [], [], []

    def flush():
        if table:
            head, *body = table
            out.append("<table><thead><tr>" + "".join(f"<th>{c}</th>" for c in head) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table>")
            table.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>")
            items.clear()

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            cells = [html.escape(x.strip()) for x in s.strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", x) for x in cells):
                table.append(cells)
            continue
        if s.startswith("- "):
            items.append(html.escape(s[2:]))
            continue
        flush()
        if s.startswith("#"):
            level = min(len(s) - len(s.lstrip("#")) + 1, 4)
            out.append(f"<h{level}>{html.escape(s.lstrip('#').strip())}</h{level}>")
        elif s:
            out.append(f"<p>{html.escape(s)}</p>")
    flush()
    return Markup("\n".join(out))

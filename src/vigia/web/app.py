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

from vigia import db, report
from vigia.sources import REGISTRY
from vigia.web import store

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["local"] = lambda iso: report._local(iso) if iso else ""
templates.env.filters["pct"] = lambda x: f"{x * 100:.0f}%"
templates.env.filters["pretty"] = lambda d: json.dumps(d, indent=2, ensure_ascii=False)
templates.env.globals["verdict_label"] = lambda v: store.VERDICT_LABELS.get(v, v or "")
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
            "page": "jobs", "job": j, "back": _back(back, "/"),
            "variants": store.VARIANTS, "reasons": store.SKIP_REASONS, "outcomes": store.OUTCOMES})

    @app.post("/jobs/{job_id}/actions")
    def add_action(job_id: int, c=Depends(get_conn), action: str = Form(...), variant: str = Form(""),
                   reason: str = Form(""), note: str = Form(""), extra: str = Form(""),
                   back: str = Form("/")):
        try:
            store.record_action(c, job_id, action, variant, reason, note, bool(extra))
        except LookupError:
            raise HTTPException(404, "No such job")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(f"/jobs/{job_id}?" + urlencode({"back": _back(back, "/")}), status_code=303)

    @app.post("/actions/{action_id}/outcome")
    def outcome(action_id: int, c=Depends(get_conn), outcome: str = Form(...), next: str = Form("/experiment")):
        try:
            store.set_outcome(c, action_id, outcome)
        except LookupError:
            raise HTTPException(404, "No such application")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return RedirectResponse(_back(next, "/experiment"), status_code=303)

    @app.post("/actions/{action_id}/delete")
    def delete(action_id: int, c=Depends(get_conn), next: str = Form("/")):
        job_id = store.delete_action(c, action_id)
        return RedirectResponse(_back(next, f"/jobs/{job_id}" if job_id else "/"), status_code=303)

    @app.get("/experiment")
    def experiment(request: Request, c=Depends(get_conn)):
        apps = store.applications(c)
        for a in apps:
            a["extra_label"] = "visa/relocation" if a["extra"] else "regular"
        summaries = [
            ("Variant", store.summarize([a for a in apps if not a["extra"]], "variant", store.VARIANTS)),
            ("Visa / relocation", store.summarize(apps, "extra_label", ("regular", "visa/relocation"))),
            ("Category", store.summarize(apps, "category", store.CATEGORY_ORDER)),
            ("Level", store.summarize(apps, "level", store.LEVELS)),
            ("Source", store.summarize(apps, "source")),
            ("Freshness at apply time", store.summarize(apps, "freshness", store.FRESHNESS)),
        ]
        total = store.summarize(apps, "extra_label")
        return templates.TemplateResponse(request, "experiment.html", {
            "page": "experiment", "apps": apps, "summaries": summaries, "outcomes": store.OUTCOMES,
            "applied": len(apps), "positive": sum(a["positive"] for a in apps),
            "skips": store.skip_reasons(c), "total": total})

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

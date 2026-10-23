"""Command line.

  vigia serve                 run forever, each source on its own schedule (the container's command)
  vigia run [SOURCE ...]      run sources once now (all enabled ones if none given)
  vigia report [--mark]       new jobs since the last review; --mark sets the review point to now
  vigia health [--check]      last run per source; --check exits 1 if the loop is stale
  vigia throttle [SOURCE]     requests per hour and 429 episodes (default: linkedin)
  vigia reflag                re-judge all stored jobs with the current config
  vigia sources               list available sources and whether they are enabled
  vigia web [--host] [--port] the private web UI (jobs, applications, experiment, health)
  vigia mcp                   MCP server on stdio (e.g. `ssh host docker exec -i vigia vigia mcp`)
  vigia app list [--open] [--status S] [-q TEXT]
  vigia app show NUMBER
  vigia app status NUMBER STATUS [--note] [--on YYYY-MM-DD] [--origin]
  vigia app next              number and experiment variant of the next application
  vigia app stats             the experiment, as JSON
  vigia app export            every application as Markdown
  vigia app import FILE.json  bulk load (list of applications, see applications.import_rows)
"""

from __future__ import annotations

import argparse
import logging
import sys

from vigia import config, pipeline, report
from vigia.sources import REGISTRY


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vigia", description="A self-hosted job radar.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="run forever on the configured schedule")

    r = sub.add_parser("run", help="run sources once")
    r.add_argument("sources", nargs="*", help="source names (default: all enabled)")

    rep = sub.add_parser("report", help="new jobs since the last review")
    rep.add_argument("--since", help="ISO timestamp (default: last review)")
    rep.add_argument("--all", action="store_true", help="also list filtered-out jobs")
    rep.add_argument("--limit", type=int, default=60)
    rep.add_argument("--queue", choices=["direct", "ats"], default="", help="only one queue (default both)")
    rep.add_argument("--mark", action="store_true", help="mark everything up to now as reviewed")

    h = sub.add_parser("health", help="status of each source")
    h.add_argument("--check", action="store_true", help="exit 1 if the loop has stalled")

    t = sub.add_parser("throttle", help="request outcomes per hour and 429 episodes")
    t.add_argument("source", nargs="?", default="linkedin")
    t.add_argument("--hours", type=int, default=72)

    sub.add_parser("reflag", help="re-judge stored jobs with the current config")
    sub.add_parser("sources", help="list sources")

    sub.add_parser("mcp", help="MCP server on stdio")

    ap = sub.add_parser("app", help="applications")
    asub = ap.add_subparsers(dest="app_cmd", required=True)
    al = asub.add_parser("list")
    al.add_argument("--open", action="store_true")
    al.add_argument("--status", default="")
    al.add_argument("-q", default="")
    ash = asub.add_parser("show")
    ash.add_argument("number", type=int)
    ast = asub.add_parser("status")
    ast.add_argument("number", type=int)
    ast.add_argument("status")
    ast.add_argument("--note", default="")
    ast.add_argument("--on")
    ast.add_argument("--origin", default="user")
    asub.add_parser("next")
    asub.add_parser("stats")
    asub.add_parser("export")
    ai = asub.add_parser("import")
    ai.add_argument("file")

    w = sub.add_parser("web", help="serve the web UI")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8000)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "serve":
        pipeline.serve()
    elif a.cmd == "run":
        names = a.sources or pipeline.enabled_sources()
        unknown = [n for n in names if n not in REGISTRY]
        if unknown:
            print(f"unknown source(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        for n in names:
            pipeline.run_source(n)
    elif a.cmd == "report":
        print(report.new_jobs(since=a.since, include_out=a.all, limit=a.limit, queue=a.queue))
        if a.mark:
            report.mark_reviewed()
    elif a.cmd == "health":
        if a.check:
            return 0 if report.is_healthy() else 1
        print(report.health())
    elif a.cmd == "throttle":
        print(report.throttling(a.source, a.hours))
    elif a.cmd == "reflag":
        print(pipeline.reflag())
    elif a.cmd == "web":
        import uvicorn

        from vigia.web import create_app
        uvicorn.run(create_app(), host=a.host, port=a.port, proxy_headers=False)
    elif a.cmd == "mcp":
        from vigia import mcp_server
        mcp_server.main()
    elif a.cmd == "app":
        return _app(a)
    elif a.cmd == "sources":
        for n in REGISTRY:
            print(f"{n:16} {'enabled' if config.source(n).get('enabled') else 'disabled'}")
    return 0


def _app(a) -> int:
    import json

    from vigia import applications as apps

    def dump(x):
        print(json.dumps(x, indent=2, ensure_ascii=False, default=str))

    try:
        if a.app_cmd == "list":
            for r in apps.list_apps(status=a.status, open_only=a.open, q=a.q):
                flags = " visa" if r["extra"] else ""
                print(f"#{r['number']:<4} {r['applied_at'] or '-':10} {r['status']:9} {r['variant'] or '-':8} "
                      f"{r['company']} — {r['title']}{flags}")
        elif a.app_cmd == "show":
            dump(apps.get(number=a.number))
        elif a.app_cmd == "status":
            dump(apps.set_status(a.status, number=a.number, note=a.note, origin=a.origin, on=a.on))
        elif a.app_cmd == "next":
            dump(apps.next_variant())
        elif a.app_cmd == "stats":
            dump(apps.stats())
        elif a.app_cmd == "export":
            print(apps.export_markdown(), end="")
        elif a.app_cmd == "import":
            with open(a.file, encoding="utf-8") as f:
                print(f"imported {apps.import_rows(json.load(f))}")
    except (LookupError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

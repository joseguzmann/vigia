"""Command line.

  vigia serve                 run forever, each source on its own schedule (the container's command)
  vigia run [SOURCE ...]      run sources once now (all enabled ones if none given)
  vigia report [--mark]       new jobs since the last review; --mark sets the review point to now
  vigia health [--check]      last run per source; --check exits 1 if the loop is stale
  vigia throttle [SOURCE]     requests per hour and 429 episodes (default: linkedin)
  vigia reflag                re-judge all stored jobs with the current config
  vigia sources               list available sources and whether they are enabled
  vigia web [--host] [--port] the private web UI (jobs, actions, experiment, health)
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
    rep.add_argument("--mark", action="store_true", help="mark everything up to now as reviewed")

    h = sub.add_parser("health", help="status of each source")
    h.add_argument("--check", action="store_true", help="exit 1 if the loop has stalled")

    t = sub.add_parser("throttle", help="request outcomes per hour and 429 episodes")
    t.add_argument("source", nargs="?", default="linkedin")
    t.add_argument("--hours", type=int, default=72)

    sub.add_parser("reflag", help="re-judge stored jobs with the current config")
    sub.add_parser("sources", help="list sources")

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
        print(report.new_jobs(since=a.since, include_out=a.all, limit=a.limit))
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
    elif a.cmd == "sources":
        for n in REGISTRY:
            print(f"{n:16} {'enabled' if config.source(n).get('enabled') else 'disabled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

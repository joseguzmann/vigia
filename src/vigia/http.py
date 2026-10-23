"""HTTP with a per-request log, so we can see how each source behaves over time.

Every request is recorded (source, kind, status, latency). For LinkedIn that log is
the point: it tells us when we get throttled (HTTP 429) and how long it lasts.
"""

from __future__ import annotations

import time

import requests

from vigia import db

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
TIMEOUT = 30

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})


class Throttled(Exception):
    """The remote side said 429. Callers should stop for this tick."""


RETRIES = 2  # extra attempts after a network error (not after an HTTP error)


def get(source: str, kind: str, url: str, *, params: dict | None = None,
        accept_json: bool = False, allow_redirects: bool = True,
        keep_errors: bool = False) -> requests.Response | None:
    """GET and log it. Returns None on network errors; raises Throttled on 429.
    HTTP errors give None too, unless keep_errors (the caller wants to see a 404 or 410).

    Network errors are retried: the scheduler leaves the pooled connection idle for hours,
    the server closes it, and the first request of the next run would otherwise fail
    (seen on 2026-10-14: the first request of every source failed with status 0).
    """
    headers = {"Accept": "application/json"} if accept_json else None
    resp, status = None, 0
    for attempt in range(RETRIES + 1):
        started = time.monotonic()
        status = 0
        try:
            resp = _session.get(url, params=params, timeout=TIMEOUT, headers=headers,
                                allow_redirects=allow_redirects)
            status = resp.status_code
        except requests.RequestException:
            resp = None
        db.log_request(source, kind, status, int((time.monotonic() - started) * 1000))
        if status != 0:
            break
        time.sleep(2 * (attempt + 1))

    if status == 429:
        raise Throttled(f"{source}: 429 on {kind}")
    if resp is None or (status >= 300 and not keep_errors):
        return None
    return resp


def get_json(source: str, kind: str, url: str, *, params: dict | None = None):
    resp = get(source, kind, url, params=params, accept_json=True)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def post_json(source: str, kind: str, url: str, payload: dict, headers: dict | None = None):
    """POST a JSON body and parse the JSON answer. Logged and retried like `get`; raises
    Throttled on 429; None on any other failure."""
    resp, status = None, 0
    for attempt in range(RETRIES + 1):
        started = time.monotonic()
        status = 0
        try:
            resp = _session.post(url, json=payload, timeout=TIMEOUT,
                                 headers={"Accept": "application/json", **(headers or {})})
            status = resp.status_code
        except requests.RequestException:
            resp = None
        db.log_request(source, kind, status, int((time.monotonic() - started) * 1000))
        if status != 0:
            break
        time.sleep(2 * (attempt + 1))
    if status == 429:
        raise Throttled(f"{source}: 429 on {kind}")
    if resp is None or status >= 300:
        return None
    try:
        return resp.json()
    except ValueError:
        return None

import json

from vigia import db
from vigia.models import Job


def make(source, url, description=""):
    return Job(source=source, source_id=url, title="Backend Engineer", company="Zaelot",
               url=url, description=description)


def test_same_role_on_two_boards_is_one_job():
    assert db.upsert_job(make("getonbrd", "https://a"), "fit", 5, {}) is True
    assert db.upsert_job(make("linkedin", "https://b"), "fit", 5, {}) is False
    rows = db.all_jobs()
    assert len(rows) == 1
    assert [s["source"] for s in json.loads(rows[0]["seen_in"])] == ["getonbrd", "linkedin"]


def test_longer_description_refreshes_flags():
    db.upsert_job(make("linkedin", "https://a"), "review", 1, {"reasons": ["no description yet"]})
    db.upsert_job(make("getonbrd", "https://b", "Python, Django, LATAM"), "fit", 6, {"reasons": []})
    row = db.all_jobs()[0]
    assert row["verdict"] == "fit" and row["score"] == 6


def test_request_log():
    db.log_request("linkedin", "search", 429, 120)
    row = db.conn().execute("SELECT * FROM requests").fetchone()
    assert row["status"] == 429 and row["source"] == "linkedin"


def test_per_country_copies_are_one_job_and_keep_the_best():
    from vigia.models import Job
    br = Job(source="ashby", source_id="1", title="Back-End (Python) Engineer - Brazil",
             company="truelogic", url="https://br", location="São Paulo (Remote)")
    la = Job(source="ashby", source_id="2", title="Back-End (Python) Engineer - LATAM",
             company="truelogic", url="https://latam", location="LatAm (Remote)")
    assert br.dedupe_key == la.dedupe_key
    db.upsert_job(br, "out", 2, {"reasons": ["restricted"]})
    db.upsert_job(la, "fit", 9, {"reasons": []})
    rows = db.all_jobs()
    assert len(rows) == 1
    assert rows[0]["verdict"] == "fit" and rows[0]["url"] == "https://latam"


def test_network_error_is_retried(monkeypatch):
    import requests
    from vigia import http

    calls = []

    class Ok:
        status_code = 200

    def fake_get(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("stale connection")
        return Ok()

    monkeypatch.setattr(http._session, "get", fake_get)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    assert http.get("t", "k", "https://x") is not None
    statuses = [r["status"] for r in db.conn().execute("SELECT status FROM requests ORDER BY rowid")]
    assert statuses == [0, 200]

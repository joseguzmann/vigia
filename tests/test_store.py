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


def test_linkedin_cooldown_is_shared_and_blocks_requests(monkeypatch):
    import pytest
    from vigia import http
    from vigia.linkedin_guest import Blocked, Guest

    def throttled(*a, **k):
        raise http.Throttled("429")

    monkeypatch.setattr(http, "get", throttled)
    g = Guest("linkedin")
    with pytest.raises(Blocked):
        g.get("search", "https://x")
    calls = []
    monkeypatch.setattr(http, "get", lambda *a, **k: calls.append(1))
    with pytest.raises(Blocked):           # a different source, same cooldown
        Guest("linkedin_posts").get("post", "https://y")
    assert calls == []                      # nothing was sent during the cooldown


def test_full_description_can_make_the_verdict_worse():
    from vigia.models import Job
    card = Job(source="linkedin", source_id="1", title="Senior Full Stack Developer", company="FullStack",
               url="https://li/1", location="Ecuador")
    db.upsert_job(card, "fit", 7, {"reasons": []})
    card.description = "5+ years of professional Software Development experience."
    db.upsert_job(card, "out", 2, {"reasons": ["asks 5+ years"]})
    assert db.all_jobs()[0]["verdict"] == "out"


def test_a_copy_without_text_does_not_override_a_judged_one():
    full = make("workday", "https://a", "Asks 8+ years of experience. Remote, Latin America.")
    db.upsert_job(full, "out", 1, {"reasons": ["asks 8+ years"]})
    db.upsert_job(make("workday", "https://a", ""), "review", 3, {"reasons": ["no description yet"]})
    row = db.all_jobs()[0]
    assert row["verdict"] == "out" and "8+ years" in row["description"]

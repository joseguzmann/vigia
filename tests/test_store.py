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

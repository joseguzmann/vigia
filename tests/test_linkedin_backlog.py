"""LinkedIn jobs stored without their text get it on a later run and are judged again."""
from pathlib import Path

from vigia import db, pipeline
from vigia.linkedin_guest import Blocked, Guest
from vigia.models import Job
from vigia.sources import linkedin
from vigia.sources.linkedin import LinkedIn

FIXTURES = Path(__file__).parent / "fixtures"
POSTING = (FIXTURES / "linkedin_posting.html").read_text()


class Resp:
    def __init__(self, text):
        self.text = text


def stored_without_text(job_id="111", title="Full Stack Developer (Python)", verdict="review"):
    job = Job(source="linkedin", source_id=job_id, title=title, company="Acme",
              url=f"https://www.linkedin.com/jobs/view/{job_id}", location="Quito, Ecuador")
    db.upsert_job(job, verdict, 4, {"reasons": ["no description yet"]})
    return db.conn().execute("SELECT * FROM jobs WHERE source_id = ?", (job_id,)).fetchone()


def fake_linkedin(monkeypatch, posting=POSTING, block_after=None):
    """Search returns no cards; every posting request returns `posting`."""
    asked = []

    def get(self, kind, url, params=None):
        if kind == "posting":
            if block_after is not None and len([a for a in asked if a[0] == "posting"]) >= block_after:
                raise Blocked("429")
        asked.append((kind, url))
        return Resp("" if kind == "search" else posting)

    monkeypatch.setattr(Guest, "check", lambda self: None)
    monkeypatch.setattr(Guest, "get", get)
    monkeypatch.setattr(linkedin, "parse_cards", lambda html: [])
    return asked


def test_backlog_fetches_the_text_and_the_job_is_judged_again(monkeypatch):
    row = stored_without_text()
    asked = fake_linkedin(monkeypatch)
    jobs = LinkedIn({"queries": [{"keywords": "python"}], "max_descriptions": 8}).fetch()
    assert [j.source_id for j in jobs] == ["111"]
    assert ("posting", linkedin.POSTING.format("111")) in asked
    assert "LangGraph" in jobs[0].description

    db.upsert_job(jobs[0], "fit", 9, {"reasons": []})
    after = db.conn().execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
    assert "LangGraph" in after["description"] and after["verdict"] == "fit"


def test_backlog_skips_out_jobs_and_jobs_already_acted_on(monkeypatch):
    stored_without_text("1", "Out role", verdict="out")
    applied = stored_without_text("2", "Applied role")
    stored_without_text("3", "Pending role")
    from vigia import applications as apps
    apps.add(company="Acme", title="Applied role", job_id=applied["id"], origin="assistant")
    fake_linkedin(monkeypatch)
    jobs = LinkedIn({"queries": [{"keywords": "python"}]}).fetch()
    assert [j.source_id for j in jobs] == ["3"]


def test_backlog_uses_only_what_is_left_of_the_budget_and_stops_when_blocked(monkeypatch):
    for i in range(5):
        stored_without_text(str(i), f"Role {i}")
    asked = fake_linkedin(monkeypatch, block_after=2)
    jobs = LinkedIn({"queries": [{"keywords": "python"}], "max_descriptions": 8}).fetch()
    assert len(jobs) == 2 and len([a for a in asked if a[0] == "posting"]) == 2

    asked = fake_linkedin(monkeypatch)
    assert len(LinkedIn({"queries": [{"keywords": "python"}], "max_descriptions": 3}).fetch()) == 3


def test_run_source_stores_the_backlog_text(monkeypatch):
    row = stored_without_text()
    fake_linkedin(monkeypatch)
    monkeypatch.setattr(pipeline.config, "source", lambda name: {"queries": [{"keywords": "python"}]})
    pipeline.run_source("linkedin")
    after = db.conn().execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
    assert "LangGraph" in after["description"]
    assert "no description yet" not in after["flags"]

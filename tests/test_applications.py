import json
from datetime import date, datetime, timedelta, timezone

import pytest

from vigia import applications as apps
from vigia import db
from vigia.models import Job


def add_job(title="AI Engineer", company="Acme", verdict="fit", age=3, level="mid", url=None):
    url = url or f"https://jobs.example.test/{company}/{title}".replace(" ", "-")
    job = Job(source="ashby", source_id=url, title=title, company=company, url=url,
              posted_at=datetime.now(timezone.utc) - timedelta(days=age))
    db.upsert_job(job, verdict, 5, {"level": level, "category": "ai"})
    return db.conn().execute("SELECT id FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)).fetchone()["id"]


def test_from_a_known_job_fills_in_the_details():
    job_id = add_job(verdict="extra", age=3)
    a = apps.add(job_id=job_id, variant="A1", cv="ai-ended", family="ai")
    assert (a["number"], a["company"], a["title"], a["level"], a["extra"], a["age_days"], a["status"]) == \
        (1, "Acme", "AI Engineer", "mid", 1, 3, "applied")
    assert a["applied_at"] == date.today().isoformat()
    assert [e["status"] for e in a["events"]] == ["applied"]


def test_a_url_finds_the_job_and_unknown_jobs_need_company_and_title():
    job_id = add_job(url="https://jobs.example.test/acme/1")
    assert apps.add(url="https://jobs.example.test/acme/1?utm=x")["job_id"] == job_id
    with pytest.raises(ValueError):
        apps.add(url="https://elsewhere.test/2")
    manual = apps.add(company="Globex", title="Backend Engineer", url="https://elsewhere.test/2")
    assert manual["job_id"] is None and manual["number"] == 2


def test_numbers_and_the_rotation():
    assert apps.next_variant() == {"number": 1, "variant": "A1"}
    apps.add(company="A", title="t")
    assert apps.next_variant() == {"number": 2, "variant": "A2"}
    apps.add(company="B", title="t", number=40)
    assert apps.next_variant()["number"] == 41


def test_bad_values_are_rejected():
    with pytest.raises(ValueError):
        apps.add(company="A", title="t", variant="A1B2")
    with pytest.raises(ValueError):
        apps.add(company="A", title="t", status="hired")
    a = apps.add(company="A", title="t")
    with pytest.raises(ValueError):
        apps.set_status("hired", number=a["number"])
    with pytest.raises(ValueError):
        apps.update(number=a["number"], status="screen")
    with pytest.raises(LookupError):
        apps.get(number=99)


def test_status_history_and_days_to_a_person():
    a = apps.add(company="A", title="t", applied_at="2026-10-01")
    apps.set_status("ack", number=1, on="2026-10-01", origin="email")
    b = apps.set_status("screen", number=1, on="2026-10-06", note="call with recruiter", origin="email")
    assert [e["status"] for e in b["events"]] == ["applied", "ack", "screen"]
    assert b["signal_days"] == 5
    assert apps.list_apps()[0]["signal_days"] == 5
    assert a["id"] == b["id"]


def test_preparing_gets_its_date_when_sent():
    a = apps.add(company="A", title="t", status="preparing")
    assert a["applied_at"] is None
    assert apps.set_status("applied", number=a["number"], on="2026-10-10")["applied_at"] == "2026-10-10"


def test_duplicate_numbers_need_the_id():
    apps.add(company="A", title="t", number=29)
    apps.add(company="B", title="t", number=29)
    with pytest.raises(ValueError, match="pass app_id"):
        apps.get(number=29)


def test_stats_window_pending_and_exclusions():
    today = date.today()
    old = (today - timedelta(days=40)).isoformat()
    a = apps.add(company="A", title="t", variant="A1", family="ai", applied_at=old)
    apps.set_status("screen", app_id=a["id"], on=(today - timedelta(days=30)).isoformat())
    late = apps.add(company="B", title="t", variant="A1", family="ai", applied_at=old)
    apps.set_status("screen", app_id=late["id"], on=(today - timedelta(days=2)).isoformat())  # after 14 days
    apps.add(company="C", title="t", variant="A2", family="backend")                       # pending
    apps.add(company="D", title="t", variant="A2", family="backend", applied_at=old, extra=True)
    apps.add(company="E", title="t", inbound=True, applied_at=old)                           # left out
    apps.add(company="F", title="t", status="preparing")                                    # left out
    s = apps.stats()
    assert s["total"] == {"applied": 4, "signals": 1, "pending": 1, "rate": pytest.approx(1 / 3)}
    variant = {r["key"]: (r["applied"], r["signals"], r["pending"]) for r in s["groups"]["variant (visa roles left out)"]}
    assert variant == {"A1": (2, 1, 0), "A2": (1, 0, 1)}
    visa = {r["key"]: r["applied"] for r in s["groups"]["visa / relocation"]}
    assert visa == {"regular": 3, "visa/relocation": 1}


def test_import_and_export():
    rows = [
        {"number": 1, "company": "Avenga", "title": "AI Engineer", "applied_at": "2026-09-15",
         "channel": "company", "variant": "baseline", "status": "ack", "notes": "LATAM | remote",
         "events": [{"status": "applied", "on": "2026-09-15"}, {"status": "ack", "on": "2026-09-16"}]},
        {"number": 2, "company": "Heinsohn", "title": "AI Developer", "status": "rejected",
         "applied_at": "2026-09-23"},
    ]
    assert apps.import_rows(rows) == 2
    assert apps.import_rows(rows) == 0  # idempotent
    first = apps.get(number=1)
    assert [e["status"] for e in first["events"]] == ["applied", "ack"] and first["status"] == "ack"
    assert [e["origin"] for e in first["events"]] == ["import", "import"]
    assert apps.get(number=2)["status"] == "rejected"
    md = apps.export_markdown()
    assert "| 1 | 2026-09-15 | Avenga | AI Engineer | ack |" in md
    assert "LATAM \\| remote" in md
    assert "**#1 Avenga**: applied (2026-09-15) → ack (2026-09-16)" in md


def test_merging_duplicate_jobs_keeps_the_application_link():
    from vigia.models import Job
    br = Job(source="ashby", source_id="1", title="Back-End (Python) Engineer - Brazil",
             company="truelogic", url="https://br", location="São Paulo (Remote)")
    db.upsert_job(br, "out", 2, {})
    old_id = db.conn().execute("SELECT id FROM jobs").fetchone()["id"]
    apps.add(job_id=old_id)
    la = Job(source="ashby", source_id="2", title="Back-End (Python) Engineer - LATAM",
             company="truelogic", url="https://latam", location="LatAm (Remote)")
    # Store the LATAM copy under its own key, as an older dedupe rule would have.
    db.conn().execute("""INSERT INTO jobs(dedupe_key, source, source_id, title, company, url, first_seen,
                         last_seen, verdict, score, seen_in) VALUES ('old', 'ashby', '2', ?, 'truelogic',
                         'https://latam', '2026-10-01', '2026-10-01', 'fit', 9, ?)""",
                      (la.title, json.dumps([{"source": "ashby", "url": "https://latam"}])))
    db.conn().commit()
    assert db.merge_duplicates(lambda r: "same") == 1
    kept = db.conn().execute("SELECT id FROM jobs").fetchone()["id"]
    assert kept != old_id and apps.get(number=1)["job_id"] == kept


def test_mcp_tools_work_on_the_same_database():
    from vigia import mcp_server
    job_id = add_job()
    assert mcp_server.next_application() == {"number": 1, "variant": "A1"}
    a = mcp_server.record_application(job_id=job_id, variant="A1", cv="ai-ended", channel="company")
    assert a["number"] == 1
    assert mcp_server.set_application_status("ack", number=1, origin="email")["status"] == "ack"
    assert mcp_server.update_application({"notes": "fine"}, number=1)["notes"] == "fine"
    assert mcp_server.list_applications(open_only=True)[0]["company"] == "Acme"
    found = mcp_server.search_jobs(query="Acme")
    assert found["total"] == 1 and found["jobs"][0]["applied"] == "#1 ack"
    assert mcp_server.get_job(job_id)["applications"][0]["number"] == 1
    assert "job " + str(job_id) in mcp_server.new_jobs(since="2000-01-01T00:00:00+00:00")
    assert "applied #1" in mcp_server.new_jobs(since="2000-01-01T00:00:00+00:00")
    assert mcp_server.experiment_stats()["total"]["applied"] == 1
    assert "Acme" in mcp_server.export_applications_markdown()

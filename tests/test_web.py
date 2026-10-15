from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from vigia import applications as apps
from vigia import db
from vigia.models import Job
from vigia.web import create_app, store


def add_job(title, company, verdict="fit", score=5, age=1, source="getonbrd", category="backend",
            level="mid", reasons=(), url=None):
    posted = datetime.now(timezone.utc) - timedelta(days=age) if age is not None else None
    url = url or f"https://example.test/{company}/{title}".replace(" ", "-")
    job = Job(source=source, source_id=url, title=title, company=company, url=url,
              location="Remote", description="Line one\nLine two", posted_at=posted)
    db.upsert_job(job, verdict, score, {"age_days": age, "category": category, "level": level,
                                        "years": 3, "reasons": list(reasons), "stack": ["python"]})
    return db.conn().execute("SELECT id FROM jobs WHERE dedupe_key = ?", (job.dedupe_key,)).fetchone()["id"]


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_list_defaults_to_fit_extra_review_freshest_first(client):
    add_job("Backend Engineer", "Acme", "fit", score=9, age=10)
    add_job("Python Developer", "Globex", "review", score=2, age=1)
    add_job("Platform Engineer", "Umbrella", "extra", score=4, age=3)
    add_job("Sales Lead", "Initech", "out", score=0, age=0)
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Backend Engineer" in body and "Python Developer" in body and "Platform Engineer" in body
    assert "Sales Lead" not in body
    assert "Extra: abroad with visa/relocation" in body
    assert body.index("Python Developer") < body.index("Platform Engineer") < body.index("Backend Engineer")
    assert "🆕" in body


def test_filters(client):
    add_job("AI Engineer", "Acme", "fit", category="ai", level="senior", age=1, source="ashby")
    add_job("Backend Engineer", "Globex", "fit", category="backend", level="mid", age=20)
    add_job("Sales Lead", "Initech", "out", age=0)

    assert "Sales Lead" in client.get("/?f=1&verdict=out").text
    assert "AI Engineer" not in client.get("/?f=1&verdict=out").text
    r = client.get("/?category=ai")
    assert "AI Engineer" in r.text and "Backend Engineer" not in r.text
    r = client.get("/?level=mid")
    assert "Backend Engineer" in r.text and "AI Engineer" not in r.text
    r = client.get("/?max_age=7")
    assert "AI Engineer" in r.text and "Backend Engineer" not in r.text
    r = client.get("/?source=ashby")
    assert "AI Engineer" in r.text and "Backend Engineer" not in r.text
    r = client.get("/?q=globex")
    assert "Backend Engineer" in r.text and "AI Engineer" not in r.text


def test_categories_come_from_the_data(client):
    add_job("Mobile Engineer", "Acme", category="mobile")
    assert '<option value="mobile"' in client.get("/").text


def test_detail_and_actions(client):
    job_id = add_job("Backend Engineer", "Acme", age=4)
    other = add_job("Data Engineer", "Globex")
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200 and "Line one\nLine two" in r.text and "https://example.test/" in r.text
    assert "This will be #1" in r.text

    r = client.post(f"/jobs/{job_id}/apply", data={"variant": "A1", "family": "backend", "channel": "company",
                                                    "cv": "ai-ended", "extra": "1"}, follow_redirects=False)
    assert r.status_code == 303
    client.post(f"/jobs/{other}/actions", data={"action": "skipped", "reason": "years", "note": "asks 6"})

    c = store.connect()
    a = apps.list_apps(c=c)[0]
    assert (a["number"], a["job_id"], a["variant"], a["cv"], a["extra"], a["age_days"], a["status"]) == \
        (1, job_id, "A1", "ai-ended", 1, 4, "applied")
    skip = c.execute("SELECT * FROM actions").fetchone()
    assert (skip["job_id"], skip["action"], skip["reason"], skip["note"]) == (other, "skipped", "years", "asks 6")

    # The job page shows the application instead of the form; status is changed from its page.
    assert f'href="/applications/{a["id"]}"' in client.get(f"/jobs/{job_id}").text
    assert client.post(f"/applications/{a['id']}/status", data={"status": "screen", "note": "call"}).status_code == 200
    assert apps.get(app_id=a["id"], c=c)["status"] == "screen"
    assert client.post(f"/applications/{a['id']}/status", data={"status": "hired!"}).status_code == 400
    assert "screen" in client.get(f"/applications/{a['id']}").text
    assert "Backend Engineer" in client.get("/applications?open=1").text
    # Bad input is rejected.
    assert client.post(f"/jobs/{job_id}/apply", data={"variant": "A9"}).status_code == 400
    assert client.post(f"/jobs/{job_id}/actions", data={"action": "applied"}).status_code == 400
    assert client.post(f"/jobs/{job_id}/actions", data={"action": "skipped", "reason": "x"}).status_code == 400
    assert client.post("/jobs/999/actions", data={"action": "saved"}).status_code == 404
    assert client.post("/jobs/999/apply", data={"variant": "A1"}).status_code == 404

    # "Only not acted on" hides both.
    r = client.get("/?open=1")
    assert "Backend Engineer" not in r.text and "Data Engineer" not in r.text


def test_experiment_page(client):
    c = store.connect()
    today = date.today()
    old = (today - timedelta(days=30)).isoformat()
    a = apps.add(company="Acme", title="AI Engineer", variant="A1", family="ai", applied_at=old, c=c)
    apps.set_status("screen", app_id=a["id"], on=(today - timedelta(days=25)).isoformat(), c=c)
    apps.add(company="Globex", title="Backend Engineer", variant="A2", family="backend", applied_at=old, c=c)
    apps.add(company="Initech", title="Backend Engineer", variant="A2", family="backend", c=c)
    r = client.get("/experiment")
    assert r.status_code == 200
    assert "3 cold applications · 1 with a step with a person within 14 days" in r.text and "50%" in r.text


def test_old_applied_actions_are_moved_to_applications(tmp_path):
    job_id = add_job("Backend Engineer", "Acme", age=2)
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS actions (id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, ts TEXT NOT NULL,
                 action TEXT NOT NULL, variant TEXT, reason TEXT, outcome TEXT, note TEXT, age_days INTEGER,
                 extra INTEGER NOT NULL DEFAULT 0)""")
    c.execute("INSERT INTO actions(job_id, ts, action, variant, outcome, age_days, extra) "
              "VALUES (?, '2026-10-15T21:05:01+00:00', 'applied', 'A2', 'none', 10, 0)", (job_id,))
    c.commit()
    store._ready.clear()
    s = store.connect()
    a = apps.list_apps(c=s)
    assert [(x["job_id"], x["variant"], x["applied_at"], x["age_days"]) for x in a] == [(job_id, "A2", "2026-10-15", 10)]
    assert s.execute("SELECT COUNT(*) FROM actions WHERE action = 'applied'").fetchone()[0] == 0


def test_health_page(client):
    db.log_request("linkedin", "search", 429, 100)
    db.log_request("linkedin", "search", 200, 100)
    r = client.get("/health")
    assert r.status_code == 200
    assert "<table>" in r.text and "429 episodes" in r.text
    assert client.get("/healthz").text == "ok"

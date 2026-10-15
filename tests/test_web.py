from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

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

    r = client.post(f"/jobs/{job_id}/actions", data={"action": "applied", "variant": "A1B2", "extra": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    client.post(f"/jobs/{other}/actions", data={"action": "skipped", "reason": "years", "note": "asks 6"})

    c = store.connect()
    rows = c.execute("SELECT * FROM actions ORDER BY id").fetchall()
    assert [(a["job_id"], a["action"], a["variant"], a["outcome"], a["extra"]) for a in rows] == \
        [(job_id, "applied", "A1B2", "none", 1), (other, "skipped", None, None, 0)]
    assert rows[0]["age_days"] == 4 and rows[1]["reason"] == "years" and rows[1]["note"] == "asks 6"

    # Outcome is editable later; only valid values are accepted.
    assert client.post(f"/actions/{rows[0]['id']}/outcome", data={"outcome": "screen"}).status_code == 200
    assert c.execute("SELECT outcome FROM actions WHERE id = ?", (rows[0]["id"],)).fetchone()[0] == "screen"
    assert client.post(f"/actions/{rows[0]['id']}/outcome", data={"outcome": "hired!"}).status_code == 400
    # Bad input is rejected.
    assert client.post(f"/jobs/{job_id}/actions", data={"action": "applied"}).status_code == 400
    assert client.post(f"/jobs/{job_id}/actions", data={"action": "skipped", "reason": "x"}).status_code == 400
    assert client.post("/jobs/999/actions", data={"action": "saved"}).status_code == 404

    # "Only not acted on" hides both.
    r = client.get("/?open=1")
    assert "Backend Engineer" not in r.text and "Data Engineer" not in r.text


def test_experiment_summary_math(client):
    ids = [add_job(f"Engineer {i}", f"Co{i}", age=age, category=cat)
           for i, (age, cat) in enumerate([(1, "ai"), (1, "ai"), (5, "backend"), (20, "backend"), (None, "ai")])]
    plan = [("A1B1", "screen"), ("A1B1", "rejected"), ("A1B1", "offer"), ("A2B2", "none"), ("A2B2", "technical")]
    c = store.connect()
    for job_id, (variant, outcome) in zip(ids, plan):
        aid = store.record_action(c, job_id, "applied", variant)
        store.set_outcome(c, aid, outcome)

    apps = store.applications(c)
    by_variant = {r["key"]: r for r in store.summarize(apps, "variant", store.VARIANTS)}
    assert (by_variant["A1B1"]["applied"], by_variant["A1B1"]["positive"]) == (3, 2)
    assert by_variant["A1B1"]["rate"] == pytest.approx(2 / 3)
    assert (by_variant["A2B2"]["applied"], by_variant["A2B2"]["positive"], by_variant["A2B2"]["rate"]) == (2, 1, 0.5)
    by_cat = {r["key"]: (r["applied"], r["positive"]) for r in store.summarize(apps, "category")}
    assert by_cat == {"ai": (3, 2), "backend": (2, 1)}
    by_fresh = {r["key"]: (r["applied"], r["positive"]) for r in store.summarize(apps, "freshness", store.FRESHNESS)}
    assert by_fresh == {"0-2 days": (2, 1), "3-7 days": (1, 1), "15+ days": (1, 0), "no date": (1, 1)}

    r = client.get("/experiment")
    assert r.status_code == 200
    assert "5 applied · 3 positive signals" in r.text and "67%" in r.text


def test_reapplying_counts_once(client):
    job_id = add_job("Backend Engineer", "Acme")
    c = store.connect()
    store.record_action(c, job_id, "saved")
    store.record_action(c, job_id, "applied", "A1B1")
    store.record_action(c, job_id, "applied", "A1B1")
    assert len(store.applications(c)) == 1


def test_health_page(client):
    db.log_request("linkedin", "search", 429, 100)
    db.log_request("linkedin", "search", 200, 100)
    r = client.get("/health")
    assert r.status_code == 200
    assert "<table>" in r.text and "429 episodes" in r.text
    assert client.get("/healthz").text == "ok"

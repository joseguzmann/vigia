from vigia import db, notify, reader, report
from vigia.models import Job


def reading(match=8, eligible="yes", visa="not_stated", why="Fits.", posting=True):
    return {"is_job_posting": posting, "ecuador_eligible": eligible, "eligibility_quote": "", "visa_sponsorship": visa, "years_required": 3,
            "level": "mid", "blockers": [], "match": match, "why": why}


def store(title, verdict="fit"):
    job = Job(source="ashby", source_id=title, title=title, company="Acme", url=f"https://x/{title}",
              location="Remote", description="Build APIs with Python. " * 20)
    db.upsert_job(job, verdict, 5, {"category": "backend"})
    return db.conn().execute("SELECT id FROM jobs WHERE source_id = ?", (title,)).fetchone()[0]


def setup(monkeypatch, readers, sent):
    cfg = {"enabled": True, "readers": readers, "notify_min_match": 7, "candidate": "Lives in Quito.",
           "api_budget_usd": 3}
    monkeypatch.setattr(reader, "cfg", lambda: cfg)
    monkeypatch.setattr(reader, "cv_for", lambda category: f"CV for {category}")
    monkeypatch.setattr(notify, "quiet", lambda now=None: False)
    monkeypatch.setattr(notify, "send", lambda title, body, url="", tags="": sent.append((title, tags)) or True)


def test_reads_pending_jobs_and_notifies_only_good_open_ones(monkeypatch):
    sent = []
    setup(monkeypatch, [{"provider": "claude-p", "model": "sonnet"}], sent)
    good, closed, weak = store("Backend Engineer"), store("Platform Engineer"), store("Data Engineer", "review")
    promo, unclear = store("Hiring Roundup"), store("Mexico Engineer")
    answers = {"Backend Engineer": reading(9), "Platform Engineer": reading(9, eligible="no"),
               "Data Engineer": reading(5), "Hiring Roundup": reading(9, posting=False),
               "Mexico Engineer": reading(9, eligible="unclear")}
    seen = []
    def fake(system, user, model):
        seen.append((model, "CV for backend" in system, "Lives in Quito." in system))
        title = user.split("Title: ")[1].split("\n")[0]
        return answers[title], {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.0}
    monkeypatch.setattr(reader, "read_claude_p", fake)
    assert reader.run() == {"read": 5, "notified": 1, "pending": 0}
    assert all(s == ("sonnet", True, True) for s in seen)
    assert sent == [("9/10 · Backend Engineer", "star")]
    assert set(db.readings([good, closed, weak, promo])) == {good, closed, weak, promo}
    assert reader.pending(10) == []  # read once, never paid for twice


def test_falls_back_and_alerts_once(monkeypatch):
    sent = []
    setup(monkeypatch, [{"provider": "claude-p", "model": "sonnet"},
                        {"provider": "anthropic", "model": "claude-haiku-5-5", "price": [0.1, 0.5]}], sent)
    store("Backend Engineer"), store("API Engineer")
    def broken(*a):
        raise RuntimeError("Fable 5.1 requires usage credits")
    monkeypatch.setattr(reader, "read_claude_p", broken)
    monkeypatch.setattr(reader, "read_api", lambda provider, s, u, model, price: (
        reading(8), {"input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.00015}))
    reader.run()
    alerts = [t for t, tags in sent if tags == "warning"]
    assert alerts == ["Vigía: job reader problem"]  # two failures, one alert
    models = {r["model"] for r in db.conn().execute("SELECT model FROM job_readings")}
    assert models == {"anthropic:claude-haiku-5-5"}
    assert round(reader.api_spent_this_month(), 5) == 0.0003


def test_api_budget_stops_the_fallback(monkeypatch):
    sent = []
    setup(monkeypatch, [{"provider": "anthropic", "model": "claude-haiku-5-5"}], sent)
    store("Backend Engineer")
    monkeypatch.setattr(reader, "api_spent_this_month", lambda: 5.0)
    monkeypatch.setattr(reader, "read_api", lambda *a: (_ for _ in ()).throw(AssertionError("called")))
    assert reader.run()["read"] == 0
    assert [t for t, tags in sent if tags == "warning"]


def test_new_jobs_puts_the_best_reading_first(monkeypatch):
    weak, closed, best, unread, unsure = (store(t) for t in ("Weak Engineer", "Closed Engineer", "Best Engineer",
                                                             "Unread Engineer", "Unsure Engineer"))
    db.save_reading(weak, "m", reading(5), {})
    db.save_reading(unsure, "m", reading(9, eligible="unclear"), {})
    db.save_reading(closed, "m", reading(10, eligible="no"), {})
    db.save_reading(best, "m", reading(9, why="Python and agents."), {})
    text = report.new_jobs(since="2000-01-01T00:00:00+00:00")
    names = ["Best Engineer", "Weak Engineer", "Unsure Engineer", "Unread Engineer", "Closed Engineer"]
    assert sorted(names, key=text.index) == names
    assert "**9/10** · open to you · Python and agents." in text


def test_backlog_is_scored_but_not_pushed(monkeypatch):
    sent = []
    setup(monkeypatch, [{"provider": "claude-p", "model": "sonnet"}], sent)
    old = store("Old Engineer")
    db.conn().execute("UPDATE jobs SET first_seen = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", old))
    monkeypatch.setattr(reader, "pending", lambda limit: [dict(db.conn().execute("SELECT * FROM jobs WHERE id = ?", (old,)).fetchone())])
    monkeypatch.setattr(reader, "read_claude_p", lambda *a: (reading(9), {}))
    assert reader.run() == {"read": 1, "notified": 0, "pending": 0}
    assert sent == []


def test_claude_p_never_sees_the_api_key(monkeypatch):
    import json as _json
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-should-not-be-used")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-subscription")
    monkeypatch.setattr(reader, "cfg", lambda: {})
    seen = {}
    class P:
        returncode = 0
        stderr = ""
        stdout = _json.dumps({"structured_output": reading(), "modelUsage": {}})
    def run(args, **kw):
        seen.update(kw["env"])
        return P()
    monkeypatch.setattr(reader.subprocess, "run", run)
    reader.read_claude_p("system", "user", "sonnet")
    assert "ANTHROPIC_API_KEY" not in seen and seen["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-subscription"

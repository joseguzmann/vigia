"""Parsers against small fixtures shaped like each source's real responses."""

from pathlib import Path

from vigia.sources.getonbrd import GetOnBoard
from vigia.sources.hn import HNWhoIsHiring
from vigia.sources.linkedin import parse_cards, parse_description

FIXTURES = Path(__file__).parent / "fixtures"


def test_linkedin_cards():
    cards = parse_cards((FIXTURES / "linkedin_search.html").read_text())
    assert len(cards) == 2
    first = cards[0]
    assert first.source_id == "4477401943"
    assert first.title == "Forward Deployed Engineer (Python + AI)"
    assert first.company == "FullStack"
    assert first.location == "Quito, Pichincha, Ecuador"
    assert first.url == "https://www.linkedin.com/jobs/view/4477401943"
    assert first.posted_at is not None


def test_linkedin_description():
    text = parse_description((FIXTURES / "linkedin_posting.html").read_text())
    assert "LangGraph" in text and "3+ years" in text


def test_getonbrd_fully_remote():
    item = {"id": "backend-x", "links": {"public_url": "https://getonbrd.com/jobs/backend-x"},
            "attributes": {"title": "Back-end Software Engineer", "remote_modality": "fully_remote",
                           "countries": ["Remote"], "min_salary": 4000, "max_salary": 6500,
                           "published_at": 1791400000, "description": "<p>4+ years with Python</p>",
                           "company": {"data": {"attributes": {"name": "Zaelot"}}}}}
    job = GetOnBoard({}).to_job(item)
    assert job.company == "Zaelot" and job.remote is True and job.location == "Remote"
    assert job.salary == "USD 4,000–6,500/month"
    assert "4+ years" in job.description


def test_getonbrd_skips_onsite():
    item = {"id": "x", "links": {}, "attributes": {"title": "Dev", "remote_modality": "no_remote"}}
    assert GetOnBoard({}).to_job(item) is None


def test_hn_comment():
    c = {"id": 49925489, "created_at": "2026-10-01T16:00:00Z",
         "text": "Enveritas | Backend Software Engineer | Remote (Global) | $135-155k<p>Python, PostgreSQL."}
    job = HNWhoIsHiring({}).to_job(c)
    assert job.company == "Enveritas"
    assert job.title == "Backend Software Engineer"
    assert job.location == "Remote (Global)"
    assert job.remote is True


def test_getonbrd_reads_countries_from_page(monkeypatch):
    from vigia import http

    class Page:
        text = "<p>Position is 100% remote, but candidates must reside in Argentina, Peru, Mexico or Costa Rica.</p>"

    monkeypatch.setattr(http, "get", lambda *a, **k: Page())
    assert GetOnBoard({}).countries("https://x") == ["Argentina", "Peru", "Mexico", "Costa Rica"]


def test_activity_id_carries_the_date():
    from vigia.sources.linkedin_posts import activity_date
    d = activity_date("https://www.linkedin.com/posts/x_hiring-activity-7515061885470179328-FLl-")
    assert d.isoformat().startswith("2026-10-11T14:52")


def test_post_parse_and_intent():
    from vigia.sources.linkedin_posts import HIRING_RE, NOT_HIRING_RE, parse_post
    html = ('<script type="application/ld+json">{"@type":"SocialMediaPosting","headline":"HIRING: AI Engineer | Remote LATAM",'
            '"articleBody":"We are looking for an AI engineer with LangGraph. Remote, LATAM.","author":{"name":"Ana"},'
            '"datePublished":"2026-10-11T14:52:56.593Z"}</script>')
    post = parse_post(html)
    assert post["author"] == "Ana" and "LangGraph" in post["body"]
    assert HIRING_RE.search(post["headline"]) and not NOT_HIRING_RE.search(post["body"])
    assert NOT_HIRING_RE.search("I'm open to work as an AI engineer #OpenToWork")


def test_post_without_location_needs_review(profile):
    from datetime import datetime, timezone
    from vigia.flags import evaluate
    from vigia.models import Job
    j = Job(source="linkedin_posts", source_id="1", title="We're hiring!", company="Ana", url="https://x",
            description="Looking for an AI Engineer with Python and LangGraph, 3+ years of experience.",
            posted_at=datetime.now(timezone.utc), tags=["post"])
    verdict, _, flags = evaluate(j, profile)
    assert verdict == "review" and flags["region"] == "unknown"
    j.description += " Fully remote, open to candidates anywhere in Latin America."
    assert evaluate(j, profile)[0] == "fit"


def test_post_noise_filters():
    from vigia.sources.linkedin_posts import NOT_HIRING_RE
    for text in ("LATAM AI Talent for US Teams. Book a call.", "We help US companies hire LATAM engineers",
                 "LATAM / EU Open to Remote Full Stack roles. Let's build something great"):
        assert NOT_HIRING_RE.search(text), text
    assert not NOT_HIRING_RE.search("We're Hiring: Full Stack Developer – Remote (LATAM Only) $6,000–$7,000 USD/month")


def test_ld_json_parsers_stay_fast_on_unclosed_scripts():
    # A truncated page with many unclosed ld+json tags made the regex rescan the rest of
    # the page from every tag: ~30 s for this input. A run must never hang on one page.
    import time
    from vigia.sources.base import job_posting_ld
    from vigia.sources.linkedin_posts import parse_post
    html = '<script type="application/ld+json">x' * 20_000
    t = time.monotonic()
    assert job_posting_ld(html) is None and parse_post(html) is None
    assert time.monotonic() - t < 1


def test_apify_post_to_job():
    from vigia.sources.linkedin_posts_apify import search_url, to_job
    item = {"urn": "urn:li:activity:7515061885470179328", "authorName": "Ana",
            "url": "https://www.linkedin.com/posts/ana_hiring-activity-7515061885470179328-FLl-?utm_source=x",
            "postedAtISO": "2026-10-11T14:52:56.593Z",
            "text": "We're hiring: AI Engineer | Remote LATAM\nPython and LangGraph."}
    job = to_job(item, "linkedin_posts_apify", 'hiring "AI Engineer" LATAM')
    assert job.source_id == "7515061885470179328" and job.company == "Ana"
    assert job.title == "We're hiring: AI Engineer | Remote LATAM" and "?" not in job.url
    assert to_job({**item, "text": "I'm open to work as an AI engineer #OpenToWork"}, "s", "q") is None
    assert to_job({**item, "text": "Great conference today"}, "s", "q") is None
    assert "keywords=hiring%20%22AI%20Engineer%22" in search_url('hiring "AI Engineer"')


def test_apify_stops_at_the_monthly_budget(monkeypatch):
    import pytest
    from vigia.sources.linkedin_posts_apify import LinkedInPostsApify
    monkeypatch.setenv("APIFY_TOKEN", "t")
    src = LinkedInPostsApify({"monthly_budget_usd": 2.0})
    monkeypatch.setattr(src, "spent_this_month", lambda h: 2.0)
    monkeypatch.setattr(src, "run", lambda *a: pytest.fail("must not run over budget"))
    with pytest.raises(RuntimeError, match="budget"):
        src.fetch()


def test_source_runs_only_inside_its_utc_hours(monkeypatch):
    from datetime import datetime, timezone
    from vigia import config, pipeline
    monkeypatch.setattr(config, "source", lambda n: {"hours_utc": [6, 9], "every_minutes": 1200})
    monkeypatch.setattr(pipeline.db, "last_run", lambda n: None)
    assert not pipeline.due("x", datetime(2026, 10, 21, 4, tzinfo=timezone.utc))
    assert pipeline.due("x", datetime(2026, 10, 21, 7, tzinfo=timezone.utc))

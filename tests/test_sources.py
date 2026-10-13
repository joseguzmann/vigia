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

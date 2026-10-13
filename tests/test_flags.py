"""Cases taken from real postings that a person had to judge by hand."""

from datetime import datetime, timedelta, timezone

from vigia.flags import evaluate, region, years_required
from vigia.models import Job


def job(title="AI Engineer", location="Remote", description="", remote=True, days_old=2):
    return Job(source="t", source_id="1", title=title, company="Acme", url="https://x",
               location=location, remote=remote, description=description,
               posted_at=datetime.now(timezone.utc) - timedelta(days=days_old))


# --- years ---------------------------------------------------------------------

def test_years_plus():
    assert years_required("Requirements: 5+ years of experience in software engineering") == 5


def test_years_takes_the_main_requirement():
    text = "5+ years of backend experience. 2+ years building LLM applications."
    assert years_required(text) == 5


def test_years_range_uses_lower_bound():
    assert years_required("3–5+ years in AI/ML engineering with a track record") == 3


def test_years_words_and_spanish():
    assert years_required("Five or more years of software development experience") == 5
    assert years_required("Al menos 5 años de experiencia profesional en desarrollo backend") == 5


def test_years_ignores_company_history():
    assert years_required("For over 25 years, we've been shaping digital transformation.") is None
    assert years_required("we have been leading the way for over 15 years in technology") is None


def test_years_absent():
    assert years_required("Build agents with LangGraph and evaluate them.") is None


# --- region --------------------------------------------------------------------

def test_region_latam_in_location(profile):
    assert region(job(location="Latin America"), profile)[0] == "ok"


def test_region_latam_in_text_beats_country_location(profile):
    # Modus Create: listed in Brazil, but the text opens it to LATAM.
    j = job(location="Brazil", description="This role is open to candidates in the United States and LATAM.")
    assert region(j, profile)[0] == "ok"


def test_region_country_only_is_restricted(profile):
    # Cadre AI: "Remote · Colombia" with nothing else.
    assert region(job(location="Colombia", remote=True), profile)[0] == "restricted"


def test_region_must_reside(profile):
    j = job(location="Remote", description="Candidates must be located in the United States.")
    assert region(j, profile)[0] == "restricted"


def test_region_generic_global_word_is_not_eligibility(profile):
    j = job(location="United States", description="We are a global leader in insurance.")
    assert region(j, profile)[0] == "restricted"


def test_region_plain_remote_is_ok(profile):
    assert region(job(location="Remote", description="Fully distributed team."), profile)[0] == "ok"


def test_region_remote_in_one_unspecified_country_needs_review(profile):
    j = job(location="Remote (one country): unspecified", description="Build APIs.")
    assert region(j, profile)[0] == "unknown"


# --- verdicts --------------------------------------------------------------------

def test_fit(profile):
    j = job(description="3+ years of experience. Python, LangGraph, RAG, evals, LATAM remote.")
    verdict, score, flags = evaluate(j, profile)
    assert verdict == "fit"
    assert {"python", "langgraph", "rag"} <= set(flags["stack"])


def test_out_for_years(profile):
    j = job(description="At least 5 years of professional backend experience. LATAM.")
    verdict, _, flags = evaluate(j, profile)
    assert verdict == "out" and "asks 5+ years" in flags["reasons"]


def test_classic_ml_goes_to_review(profile):
    # Lumenalta: "AI Engineer" that trains models with PyTorch.
    j = job(description="Train models on banking rules. Expertise with PyTorch, TensorFlow, Scikit-Learn. LATAM.")
    verdict, _, flags = evaluate(j, profile)
    assert flags["classic_ml"] and verdict == "review"


def test_stale_posting_is_out(profile):
    verdict, _, flags = evaluate(job(description="LATAM. Python.", days_old=200), profile)
    assert verdict == "out" and any(r.startswith("stale") for r in flags["reasons"])


def test_title_out_of_scope(profile):
    assert evaluate(job(title="Digital Insurance Agent", description="LATAM"), profile)[0] == "out"
    assert evaluate(job(title="Senior Director, Agent-to-Agent", description="LATAM"), profile)[0] == "out"


def test_no_description_needs_review(profile):
    assert evaluate(job(description=""), profile)[0] == "review"


def test_office_bound_title_beats_latam_text(profile):
    # Truelogic: "Founding Engineer (Hybrid, NYC)" whose body mentions LATAM.
    j = job(title="Founding FullStack Engineer (Hybrid, NYC)", location="New York (Remote)",
            description="We hire across LATAM.")
    assert region(j, profile)[0] == "restricted"


def test_company_exclude(profile):
    profile = {**profile, "company_exclude": ["bairesdev"]}
    j = job(description="LATAM, Python")
    j.company = "BairesDev"
    verdict, _, flags = evaluate(j, profile)
    assert verdict == "out" and "company excluded (bairesdev)" in flags["reasons"]

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


def test_city_remote_is_country_bound_even_if_blurb_mentions_latam(profile):
    # Nubank: "Palo Alto (Remote)"; the company blurb talks about Latin America.
    j = job(title="Lead Software Engineer (Crypto)", location="Palo Alto (Remote)",
            description="Nubank is one of the largest digital banking platforms in Latin America.")
    assert region(j, profile)[0] == "restricted"


def test_worldwide_location_but_text_requires_europe(profile):
    # Makersite on Himalayas: location "Worldwide", text requires living in Europe.
    j = job(location="Worldwide", description="Required to be based in Berlin, Munich, London or Porto.")
    assert region(j, profile)[0] == "restricted"


def test_must_be_based_in_latam_is_ok(profile):
    j = job(location="Remote", description="You must be based in LATAM and overlap with EST.")
    assert region(j, profile)[0] == "ok"


def test_latam_in_title_is_ok(profile):
    j = job(title="Python Go Backend Engineer ( LATAM )", location="United States")
    assert region(j, profile)[0] == "ok"


def test_company_blurb_about_latam_does_not_open_the_role(profile):
    for blurb in ("We power 25 locations in 16 countries across Latin America.",
                  "A talent-density team driven to make an impact in Latin America.",
                  "We connect talent with enterprise clients across Latin America."):
        j = job(location="Brazil", description=blurb)
        assert region(j, profile)[0] == "restricted", blurb


def test_board_location_restriction_beats_latam_title(profile):
    # A5 Talent Finders on Himalayas: title "( LATAM )", board says "United States only".
    j = job(title="Python Go Backend Engineer ( LATAM )", location="United States",
            description="LATAM Python Go Backend Engineer (Remote)")
    j.tags = ["location_restricted"]
    assert region(j, profile)[0] == "restricted"


# --- vocabulary --------------------------------------------------------------------

def test_hyphenated_and_glued_titles_are_in_scope(profile):
    from vigia.flags import title_in_scope
    for title in ("Web App Full-Stack Engineer", "Senior Fullstack Developer", "Back-End Developer",
                  "Desarrollador Full-Stack AI Native", "Engenheiro de IA Sênior",
                  "Ingeniero de Inteligencia Artificial", "AI Harness Engineer"):
        assert title_in_scope(title, profile)[0], title


def test_excluded_titles_still_out(profile):
    from vigia.flags import title_in_scope
    for title in ("Senior .NET Developer", "Account Executive", "iOS Engineer", "Software Engineering Intern"):
        assert not title_in_scope(title, profile)[0], title


def test_normalize():
    from vigia.vocab import normalize
    assert normalize("Full-Stack/Back-End Ingeniería") == "full stack back end ingenieria"


def test_company_board_postings_age_more_slowly(profile):
    j = job(description="LATAM. Python.", days_old=90)
    j.source = "ashby"
    assert evaluate(j, profile)[0] != "out"
    j.source = "himalayas"
    assert evaluate(j, profile)[0] == "out"


def test_country_list_without_home_country_is_restricted(profile):
    # Strider: "LATAM" = Argentina, Brazil, Colombia, Mexico... but not Ecuador.
    j = job(location="LATAM: Argentina, Brazil, Colombia, Mexico", description="Remote LATAM.")
    j.tags = ["countries:Argentina,Brazil,Colombia,Mexico"]
    assert region(j, {**profile, "home_country": "Ecuador"})[0] == "restricted"
    j.tags = ["countries:Argentina,Ecuador,Peru"]
    assert region(j, {**profile, "home_country": "Ecuador"})[0] == "ok"


def test_post_naming_other_countries_is_restricted(profile):
    from vigia.models import Job
    j = job(title="🚨 HIRING: Applied AI Engineers | Canada & US | Remote 🇨🇦 🇺🇸", location="",
            description="Everyone is building AI agents.")
    j.tags = ["post"]
    assert region(j, profile)[0] == "restricted"


def test_anywhere_perk_does_not_beat_a_named_location(profile):
    for loc, text in [("San Francisco, CA • New York, NY", "work together in real time from anywhere in the world"),
                      ("Remote - Lithuania, Remote - North Macedonia", "Fully Remote. Work from anywhere."),
                      ("Belfast, Northern Ireland, Remote", "fully remote, work from anywhere within NI"),
                      ("EMEA, United States, Brazil", "Work from anywhere: Oyster has no borders")]:
        assert region(job(location=loc, description=text), profile)[0] == "restricted", loc


def test_anywhere_with_generic_location_is_ok(profile):
    assert region(job(location="", description="We are open to applicants worldwide."), profile)[0] == "ok"

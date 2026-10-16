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


def test_us_authorization_phrasings(profile):
    for text in ("join our team as a Junior AI Engineer remotely in the US",
                 "Authorized to work in the United States without sponsorship"):
        assert region(job(location="Remote", description=text), profile)[0] == "restricted", text


def test_title_level():
    from vigia.flags import title_level
    assert title_level("Senior AI Engineer") == "senior"
    assert title_level("Junior/SemiSenior Back-End (Python) Engineer") == "mid"
    assert title_level("Junior AI Engineer") == "junior"
    assert title_level("Desarrollador Python Semi Senior") == "mid"
    assert title_level("Staff Software Engineer, Backend") == "principal"
    assert title_level("AI Engineer") == "unstated"


def test_years_after_a_heading_ending_in_for():
    assert years_required("What We're Looking For \n\n 5+ years of professional Software Development experience.") == 5


def test_frontend_and_mobile_titles_in_scope(profile):
    from vigia.flags import title_in_scope
    profile = {**profile, "role_families": ["ai", "backend", "fullstack", "frontend", "mobile"]}
    for t in ("Senior React Developer", "Frontend Engineer (Next.js)", "React Native Developer"):
        assert title_in_scope(t, profile)[0], t


def test_abroad_with_visa_is_extra(profile):
    j = job(location="Berlin, Germany", description="Python backend role. We offer visa sponsorship and relocation support.")
    assert evaluate(j, profile)[0] == "extra"
    j.description = "Python backend role in Berlin. We do not provide visa sponsorship."
    assert evaluate(j, profile)[0] == "out"


def test_generic_remote_locations_in_other_words(profile):
    from vigia.flags import region
    from vigia.models import Job
    for loc in ("Remote job (Remote)", "Remoto", "100% remoto", "Remote (Remote)"):
        job = Job(source="recruitee", source_id="1", title="Fullstack Developer", company="Aerolab",
                  url="https://x", location=loc, remote=True, description="Build web apps.")
        assert region(job, profile)[0] == "ok", loc
    job = Job(source="breezy", source_id="2", title="Python Developer", company="Flux IT",
              url="https://y", location="Argentina (Remote)", remote=True, description="Python.")
    assert region(job, profile)[0] == "restricted"


def test_workday_form_fields_are_not_offers(profile):
    from vigia.flags import evaluate, region
    from vigia.models import Job
    text = ("Build RF systems in Python.\n#LI-Onsite\nTravel Requirements None\nRelocation Provided None\n"
            "Position Type Experienced")
    job = Job(source="workday", source_id="1", title="Senior Software Engineer", company="Motorola Solutions",
              url="https://x", location="Cork, Ireland", description=text)
    assert region(job, profile)[0] == "restricted"
    assert evaluate(job, profile)[0] == "out"


def test_office_where_the_candidate_lives_is_fine(profile):
    from vigia.flags import region
    from vigia.models import Job
    profile = {**profile, "home_country": "Ecuador", "home_cities": ["Quito"]}
    quito = Job(source="linkedin", source_id="1", title="Desarrollador Web Senior (PRESENCIAL - QUITO)",
                company="Construex", url="https://x", location="Quito, Pichincha, Ecuador", description="React.")
    assert region(quito, profile) == ("ok", "office in Quito")
    gye = Job(source="multitrabajos", source_id="3", title="Desarrollador", company="Y", url="https://z",
              location="Guayaquil, Guayas, Ecuador · presencial", description="React.")
    assert region(gye, profile)[0] == "restricted"
    lima = Job(source="linkedin", source_id="2", title="Desarrollador Web (Presencial)", company="X",
               url="https://y", location="Lima, Peru", description="React.")
    assert region(lima, profile)[0] == "restricted"


def test_visa_and_relocation_wording(profile):
    from vigia.flags import NO_VISA_RE, VISA_RE
    def offer(text):
        return bool(VISA_RE.search(text)) and not NO_VISA_RE.search(text)
    assert offer("Relocation is available for candidates who require it.")
    assert offer("We offer visa sponsorship and a relocation package.")
    assert not offer("**Visa Sponsorship:** Not available")
    assert not offer("SF-based or willing to relocate immediately.")
    assert not offer("Visa Sponsorship Details: Open to visa transfers (e.g. OPT, H1B transfers)")

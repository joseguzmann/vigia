"""Flags and verdicts: the judgment a person applies when reading a posting.

For each job we answer:
  - Is the title in scope?
  - How many years does it ask for?
  - Can the candidate work from where they are, or is it restricted to a country / office?
  - Is it classic ML (training models) rather than LLM application work?
  - How much of the candidate's stack does it mention?

and turn that into a verdict: "fit", "review" (a person should look) or "out", with the
reasons spelled out. Plain regex on purpose: fast, explainable, and a baseline for the
LLM-based triage planned for phase 2.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from vigia import vocab
from vigia.models import Job

WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                "eight": 8, "nine": 9, "ten": 10, "dos": 2, "tres": 3, "cuatro": 4,
                "cinco": 5, "seis": 6, "siete": 7, "ocho": 8}

# "5+ years of experience", "3-5 years", "5 or more years", "al menos 5 años", "five or more years"
_NUM = r"(\d{1,2}|" + "|".join(WORD_NUMBERS) + r")"
YEARS_RE = re.compile(
    _NUM + r"(?:\s*(?:-|–|to|a)\s*" + _NUM + r")?\s*\+?\s*(?:or more\s+|o más\s+)?"
    r"(?:years?|yrs?|años)", re.I)
# Company-history phrases that are not requirements: "for over 25 years", "founded 15 years ago".
YEARS_NOISE_RE = re.compile(r"(over|for|since|founded|history|ago|in business|leading the way)\s*$", re.I)

# Roles abroad that sponsor a visa: these become "extra" instead of "out". A relocation package
# alone is not enough (it is often only for people already allowed to work there).
VISA_RE = re.compile(
    r"visa sponsorship|sponsor(?:ship)? (?:is )?(?:available|provided|offered)|we (?:can |will )?sponsor|"
    r"we sponsor (?:visas?|h-?1b|o-?1)|patrocinio de visa|visa sponsor", re.I)
# Not offers: "willing to relocate" / "relocate to SF" ask the candidate to move on their own.
NO_VISA_RE = re.compile(r"(?:not|unable to|cannot|can't|don't|do not|will not|won't) (?:provide |offer )?(?:visa )?sponsor|"
                        r"no (?:visa )?sponsorship|sponsorship is not|without (?:the need for )?sponsorship|"
                        # Workday's form field "Relocation Provided: None" is not an offer.
                        r"relocation provided:?\s*(?:none|no)\b|relocation:?\s*(?:none|no|not provided)\b|"
                        # "Visa Sponsorship: Not available"; "open to visa transfers (H1B transfers)" only
                        # helps someone already holding a visa there.
                        r"sponsorship(?: details)?[:*\s]*(?:is\s+)?(?:not available|unavailable|none|no)\b|"
                        r"(?:visa|h-?1b) transfers?|not open to (?:any )?visas?|no visas?\b", re.I)
# How a posting states its work mode in the text (used where the source gives no mode field).
TEXT_MODE_RE = re.compile(
    r"(?:modalidad|modality|work model|modelo de trabajo|tipo de trabajo|workplace)[^\n]{0,25}?"
    r"(?:presencial|on-?site|h[ií]brid[oa]|hybrid)|100\s*%\s*presencial|trabajo presencial|"
    r"(?:position|role|posición|cargo) is (?:fully )?(?:on-?site|hybrid)", re.I)
# LinkedIn's posting tags, which many ATS copy into the text: an on-site or hybrid role.
LI_OFFICE_RE = re.compile(r"#LI-(?:Onsite|Hybrid)\b", re.I)

RESTRICTION_RE = re.compile(
    r"(?:must|required\s+to|need\s+to|needs\s+to|have\s+to)\s+(?:be\s+)?"
    r"(?:located|based|resid\w*|liv\w*)\s+in"
    r"|(?:legal\s+)?right\s+to\s+work\s+in"
    r"|work\s+authori[sz]ation|authori[sz]ed\s+to\s+work\s+in"
    r"|remote(?:ly)?\s+(?:with)?in\s+the\s+(?:us|u\.s\.|united states|uk)\b"
    r"|(?:only|exclusively)\s+(?:open\s+to\s+)?candidates\s+(?:based|located|residing)"
    # Spanish: "candidatos que residan en República Dominicana", "debe residir en Colombia"
    r"|\b(?:residan|resida|vivan|residentes|radicad[oa]s?|ubicad[oa]s?)\s+en\b"
    r"|\bdeb(?:e|en|es)\s+(?:residir|vivir|estar\s+(?:ubicad|radicad)\w*)\s+en\b"
    r"|\b(?:us|u\.s\.|usa)[- ]only\b"
    r"|remote\s+(?:with)?in\s+(?:the\s+)?(?:us|united states|canada|europe|eu|uk|colombia|mexico|"
    r"m[eé]xico|brazil|brasil|argentina|chile|peru|per[uú])\b"
    r"|\bon-?site\b|\bhybrid\b|\bh[ií]brido\b|\bpresencial\b|\brelocat",
    re.I)

LLM_RE = re.compile(r"\b(llms?|langgraph|langchain|rag|agentic|ai agents?|agents?|prompt|"
                    r"embeddings?|vector|openai|anthropic|claude|gpt)\b", re.I)
CLASSIC_ML_RE = re.compile(r"\b(pytorch|tensorflow|scikit|keras|xgboost|model training|"
                           r"train(?:ing)? (?:ml )?models|computer vision|data scientist|"
                           r"feature engineering|kubeflow)\b", re.I)
AI_TITLE_RE = re.compile(r"\b(ai|ml|llm|genai|generative|agent\w*|machine learning|"
                         r"conversational|nlp)\b", re.I)
BACKEND_TITLE_RE = re.compile(r"\b(backend|back-end|python|django|fastapi|software engineer|"
                              r"platform|full ?stack|api)\b", re.I)


COMPANY_BOARDS = {"greenhouse", "ashby", "lever"}


def title_in_scope(title: str, profile: dict) -> tuple[bool, str]:
    """In scope if the title contains a term from the profile's role families
    (config/vocabulary.yaml) or from `title_include`, and nothing from `title_exclude`.
    Matching is on normalized text, so "Full-Stack" = "full stack" = "fullstack"."""
    excluded = set()
    for w in profile.get("title_exclude", []):
        excluded |= vocab.variants(w)
    hit = vocab.match(title, excluded)
    if hit:
        return False, f"title excluded ({hit})"
    terms = set()
    for fam_terms in vocab.family_terms(profile.get("role_families")).values():
        terms |= fam_terms
    for w in profile.get("title_include", []):
        terms |= vocab.variants(w)
    if vocab.match(title, terms):
        return True, ""
    return False, "title out of scope"


def years_required(text: str) -> int | None:
    """The main years-of-experience requirement, or None if the posting does not say."""
    found = []
    for m in YEARS_RE.finditer(text or ""):
        before = text[max(0, m.start() - 25):m.start()].rsplit("\n", 1)[-1]  # same line only:
        if YEARS_NOISE_RE.search(before):   # "Looking For\n 5+ years" is a requirement
            continue
        after = text[m.end():m.end() + 60].lower()
        if not re.search(r"experien|of |in |as an? |building|develop|work|professional|hands|de |con |como ", after):
            continue
        low = _to_int(m.group(1))
        if low is None or not 1 <= low <= 20:  # "15+ years in software" is a requirement too
            continue
        found.append(low)  # for a range "3-5 years" the lower bound is the requirement
    return max(found) if found else None


# Where-you-can-work signals, from strongest to weakest:
#   1. office-bound markers in title/location (hybrid, onsite) → restricted
#   2. a LATAM-type region in the location → ok
#   3. an explicit restriction in the text ("must be located in ...") → restricted,
#      unless what follows it is itself an eligible region ("must be based in LATAM")
#   4. an explicit opening in the title or text ("open to candidates in ... LATAM",
#      "anywhere in Latin America", "LATAM" in the title) → ok
#   5. a generic remote location ("Remote", "Worldwide") → ok
#   6. anything else: a specific city/country, even with "(Remote)" next to it, means
#      remote within that country → restricted
# A bare "LATAM" somewhere in the body (company blurbs: "offices across Latin America")
# is NOT a signal: it is what made "Palo Alto (Remote)" look open.
LATAM = r"(?:latam|latin america|latinoam[eé]rica|south america|am[eé]rica latina|the americas|ecuador)"
# Phrases about WHO they hire, not about the company ("offices across Latin America").
OPEN_STRONG_RE = re.compile(
    r"(?:open\s+to\s+(?:candidates|applicants|talent|engineers|people)\b[^.\n]{0,40}?"
    r"|hiring\s+(?:in|from|across|throughout)\s+(?:the\s+)?"
    r"|(?:candidates|applicants|engineers|developers)\s+(?:from|in|based\s+in|located\s+in|living\s+in)\s+(?:the\s+)?"
    r"|anywhere\s+in\s+(?:the\s+)?"
    r"|(?:you\s+(?:are|must\s+be)\s+)?(?:based|located|living|residing)\s+(?:anywhere\s+)?in\s+(?:the\s+)?"
    r"|work\s+(?:remotely\s+)?from\s+(?:anywhere\s+in\s+)?(?:the\s+)?)" + LATAM +
    r"|\b(?:remote|remoto)\s*(?:[-–(,/|·•:]\s*)?" + LATAM +
    r"|\b(?:remote|remoto)\s+(?:from|in|within|desde|en)\s+(?:the\s+)?" + LATAM, re.I)
# "Anywhere" phrases are mostly culture/perks copy ("our team works from anywhere in the
# world"). They only count when the location itself is generic; a named city or country
# beats them (Figma: "San Francisco • New York" + "work together from anywhere in the world").
ANYWHERE_RE = re.compile(
    r"work\s+from\s+anywhere(?!\s+(?:within|in)\b)|anywhere\s+in\s+the\s+world|remote[ ,(-]*worldwide"
    r"|open\s+to\s+(?:candidates|applicants)\s+(?:from\s+)?(?:anywhere|worldwide)", re.I)
# Region names in an accepted-countries list that include Ecuador.
OPEN_REGIONS = {"worldwide", "anywhere", "global", "latin america", "latam", "south america", "the americas",
                "americas", "latinoamérica", "latinoamerica", "américa latina"}
LATAM_RE = re.compile(r"\b" + LATAM + r"\b", re.I)
OFFICE_RE = re.compile(r"\bhybrid\b|\bh[ií]brido\b|\bon-?site\b|\bin-office\b|\bpresencial\b", re.I)
GENERIC_REMOTE = {"", "remote", "remote job", "anywhere", "worldwide", "global", "fully remote",
                  "remote, global", "remote (global)", "100% remote", "remoto", "remota", "100% remoto",
                  "trabajo remoto", "teletrabajo", "remoto (global)"}
TAG_REMOTE_RE = re.compile(r"\s*\((?:remote|remoto)\)\s*$")
POST_PLACE_RE = re.compile(
    r"\b(?:us|usa|u\.s\.|united states|canada|uk|united kingdom|europe|emea|india|pakistan|"
    r"philippines|australia|germany|london|new york|nyc|san francisco|sf bay|bangalore|bengaluru|"
    r"hyderabad|pune|chennai|mumbai|delhi|noida|gurgaon|gurugram|dubai|singapore)\b|🇺🇸|🇨🇦|🇮🇳|🇬🇧|🇪🇺",
    re.I)
GENERIC_WORDS = {"worldwide", "anywhere", "global"}


def region(job: Job, profile: dict) -> tuple[str, str]:
    """("ok" | "restricted" | "unknown", evidence). See the signal order above."""
    regions = [r.lower() for r in profile.get("eligible_regions", [])]
    specific = [r for r in regions if r not in GENERIC_WORDS]
    loc = (job.location or "").lower().strip()
    # Several sources append "(Remote)" to the place they give: "Remote job (Remote)" is
    # still no place at all.
    loc = TAG_REMOTE_RE.sub("", loc).strip() if TAG_REMOTE_RE.sub("", loc).strip() in GENERIC_REMOTE else loc
    text = f"{job.title}\n{job.description}"

    office = OFFICE_RE.search(f"{job.title} {job.location}") or LI_OFFICE_RE.search(job.description or "")
    # With home cities set, only those count (on-site in Guayaquil is not on-site in Quito);
    # without them, the home country does.
    homes = profile.get("home_cities") or [profile.get("home_country")]
    home_place = next((h for h in homes if h and h.lower() in loc), None)
    if office:
        # On-site or hybrid where the candidate lives is fine: it is only "office-bound" elsewhere.
        if home_place:
            return "ok", f"office in {home_place}"
        return "restricted", f"office-bound: {office.group(0)}"
    # An explicit country list from the source (Strider, Get on Board) is authoritative:
    # "LATAM" there often means 9 countries, and Ecuador is not always one of them.
    countries = next((t[len("countries:"):] for t in (job.tags or []) if t.startswith("countries:")), None)
    home = (profile.get("home_country") or "").lower()
    if countries is not None and home:
        listed = [c.strip().lower() for c in countries.split(",") if c.strip()]
        # Some boards (RemoteYeah) put regions in the list: "Worldwide", "Latin America".
        broad = next((c for c in listed if c in OPEN_REGIONS), None)
        if broad:
            return "ok", f"accepted region includes {profile['home_country']}: {broad}"
        if listed:
            return (("ok", f"{profile['home_country']} is in the accepted countries") if home in listed
                    else ("restricted", f"accepted countries exclude {profile['home_country']}: {countries[:80]}"))
    hit_loc = next((r for r in specific if r in loc), None)
    # In the home country but not a home city, with the work mode only in the text (LinkedIn
    # and others send no mode): "Work Modality: On-site · Guayaquil" is not for someone in Quito.
    cities = [c.lower() for c in profile.get("home_cities") or []]
    if (hit_loc and home and home in loc and cities and not any(c in loc for c in cities)
            and not job.remote and "(remote)" not in loc):
        mode = TEXT_MODE_RE.search(job.description or "")
        if mode:
            return "restricted", f"office-bound outside {profile['home_cities'][0]}: {mode.group(0)[:40]}"
    if hit_loc:
        return "ok", f"location: {hit_loc}"
    if "location_restricted" in (job.tags or []):
        # The board states the allowed countries explicitly; nothing in the text overrides it
        # (A5 Talent Finders: "LATAM" in the title, "United States only" on Himalayas).
        return "restricted", f"board restricts to: {job.location}"
    for m in RESTRICTION_RE.finditer(text):
        if not LATAM_RE.search(text[m.end():m.end() + 60]):
            return "restricted", m.group(0)
    if LATAM_RE.search(job.title):
        return "ok", "title mentions LATAM"
    opening = OPEN_STRONG_RE.search(text)
    if opening:
        return "ok", f"text: {opening.group(0)[:80]}"
    generic_loc = loc in GENERIC_REMOTE or any(w in loc for w in GENERIC_WORDS) or not loc
    anywhere = ANYWHERE_RE.search(text)
    if anywhere and generic_loc:
        return "ok", f"text: {anywhere.group(0)[:80]}"
    if "post" in (job.tags or []):
        # Posts have no location field. A place named near the top ("Canada & US", 🇺🇸,
        # "Bangalore") ties the role to it; otherwise only an explicit opening counts.
        place = POST_PLACE_RE.search(f"{job.title}\n{job.description[:600]}")
        if place:
            return "restricted", f"post names {place.group(0)}"
        return "unknown", "the post does not say where it hires"
    if loc in GENERIC_REMOTE or any(w in loc for w in GENERIC_WORDS):
        return ("ok", "remote, no restriction stated") if job.remote is not False else ("unknown", "")
    if "unspecified" in loc:
        return "unknown", job.location
    return "restricted", f"location: {job.location}"


LEVELS = [("intern", r"intern|trainee|practicante|pasante"),
          ("principal", r"principal|staff|distinguished|architect|head|director|vp"),
          ("lead", r"lead|l[ií]der|manager"),
          ("mid", r"mid|semi ?senior|ssr|pleno|ii\b|intermediate"),
          ("senior", r"senior|sr|s[eê]nior|iii"),   # after "mid": "Semi Senior" is mid
          ("junior", r"junior|jr|entry|graduate|associate|i\b")]


def title_level(title: str) -> str:
    t = vocab.normalize(title)
    for name, pattern in LEVELS:
        if re.search(rf"\b(?:{pattern})\b", t):
            return name
    return "unstated"


def stack_hits(text: str, profile: dict) -> list[str]:
    return [s for s in profile.get("stack", []) if re.search(rf"\b{re.escape(s)}s?\b", text, re.I)]


def evaluate(job: Job, profile: dict) -> tuple[str, int, dict]:
    """Returns (verdict, score, flags)."""
    text = f"{job.title}\n{job.description}"
    reasons: list[str] = []
    verdict = "fit"

    is_post = "post" in (job.tags or [])
    # A post's "title" is just its first line; the role is usually named a little further in.
    in_scope, why = title_in_scope(f"{job.title} {job.description[:400]}" if is_post else job.title, profile)
    if not in_scope:
        verdict = "out"
        reasons.append(why)
    company = (job.company or "").lower()
    excluded = next((c for c in profile.get("company_exclude", []) if c.lower() in company), None)
    if excluded:
        verdict = "out"
        reasons.append(f"company excluded ({excluded})")

    years = years_required(job.description)
    max_years = profile.get("max_years")
    if years is not None and max_years is not None and years > max_years:
        verdict = "out"
        reasons.append(f"asks {years}+ years")

    reg, evidence = region(job, profile)
    if reg == "restricted":
        verdict = "out"
        reasons.append(f"restricted: {evidence}")
    elif reg == "unknown" and verdict == "fit":
        verdict = "review"
        reasons.append(f"eligibility unclear ({evidence or 'no location info'})")

    llm_terms = len(LLM_RE.findall(text))
    ml_terms = len(CLASSIC_ML_RE.findall(text))
    classic_ml = ml_terms >= 2 and ml_terms >= llm_terms / 2
    if classic_ml and verdict == "fit":
        verdict = "review"
        reasons.append("looks like classic ML (model training)")

    if AI_TITLE_RE.search(job.title) or llm_terms >= 4:
        category = "ai"
    elif BACKEND_TITLE_RE.search(job.title):
        category = "backend"
    else:
        category = "other"

    # A posting still on a company's own board is still open; aggregators keep dead ones.
    max_age = (profile.get("max_age_days_boards", 120) if job.source in COMPANY_BOARDS
               else profile.get("max_age_days"))
    age_days = None
    if job.posted_at is not None:
        age_days = (datetime.now(timezone.utc) - job.posted_at).days
        if max_age is not None and age_days > max_age:
            verdict = "out"
            reasons.append(f"stale: posted {age_days} days ago")

    # A role tied to another country that sponsors a visa or relocates is a valid "extra":
    # the candidate would move. Everything else about it must still fit.
    only_region = reg == "restricted" and all(r.startswith("restricted") for r in reasons)
    if only_region and VISA_RE.search(text) and not NO_VISA_RE.search(text):
        verdict = "extra"
        reasons.append("abroad with visa/relocation")

    if not job.description and verdict == "fit":
        verdict = "review"
        reasons.append("no description yet")

    # Seniority in the title: the profile says which levels to favour (mid-level converts
    # better than "Senior/Staff" for a ~4-year candidate) — a ranking signal, not a filter.
    level = title_level(job.title)
    hits = stack_hits(text, profile)
    score = len(hits) + {"ai": 3, "backend": 1}.get(category, 0) + (2 if reg == "ok" else 0)
    if years is not None and max_years is not None and years <= max_years:
        score += 1
    score += profile.get("level_bonus", {}).get(level, 0)

    flags = {"age_days": age_days, "level": level, "years": years, "region": reg, "region_evidence": evidence, "category": category,
             "classic_ml": classic_ml, "llm_terms": llm_terms, "stack": hits, "reasons": reasons}
    return verdict, score, flags


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    return int(s) if s.isdigit() else WORD_NUMBERS.get(s.lower())



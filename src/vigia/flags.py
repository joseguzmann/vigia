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

RESTRICTION_RE = re.compile(
    r"(?:must|required\s+to|need\s+to|needs\s+to|have\s+to)\s+(?:be\s+)?"
    r"(?:located|based|resid\w*|liv\w*)\s+in"
    r"|(?:legal\s+)?right\s+to\s+work\s+in"
    r"|work\s+authori[sz]ation"
    r"|(?:only|exclusively)\s+(?:open\s+to\s+)?candidates\s+(?:based|located|residing)"
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


def title_in_scope(title: str, profile: dict) -> tuple[bool, str]:
    t = title.lower()
    for word in profile.get("title_exclude", []):
        if _contains_term(t, word):
            return False, f"title excluded ({word})"
    if any(_contains_term(t, w) for w in profile.get("title_include", [])):
        return True, ""
    return False, "title out of scope"


def years_required(text: str) -> int | None:
    """The main years-of-experience requirement, or None if the posting does not say."""
    found = []
    for m in YEARS_RE.finditer(text or ""):
        if YEARS_NOISE_RE.search(text[max(0, m.start() - 25):m.start()]):
            continue
        after = text[m.end():m.end() + 60].lower()
        if not re.search(r"experien|of |in |building|develop|work|professional|hands|de |con ", after):
            continue
        low = _to_int(m.group(1))
        if low is None or not 1 <= low <= 12:
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
    r"|\b(?:remote|remoto)\s*[-–(,/]\s*" + LATAM +
    r"|work\s+from\s+anywhere|anywhere\s+in\s+the\s+world|remote[ ,(-]*worldwide"
    r"|open\s+to\s+(?:candidates|applicants)\s+(?:from\s+)?(?:anywhere|worldwide)", re.I)
LATAM_RE = re.compile(r"\b" + LATAM + r"\b", re.I)
OFFICE_RE = re.compile(r"\bhybrid\b|\bh[ií]brido\b|\bon-?site\b|\bin-office\b|\bpresencial\b", re.I)
GENERIC_REMOTE = {"", "remote", "remote job", "anywhere", "worldwide", "global", "fully remote",
                  "remote, global", "remote, global (remote)", "remote (global)", "worldwide (remote)"}
GENERIC_WORDS = {"worldwide", "anywhere", "global"}


def region(job: Job, profile: dict) -> tuple[str, str]:
    """("ok" | "restricted" | "unknown", evidence). See the signal order above."""
    regions = [r.lower() for r in profile.get("eligible_regions", [])]
    specific = [r for r in regions if r not in GENERIC_WORDS]
    loc = (job.location or "").lower().strip()
    text = f"{job.title}\n{job.description}"

    office = OFFICE_RE.search(f"{job.title} {job.location}")
    if office:
        return "restricted", f"office-bound: {office.group(0)}"
    hit_loc = next((r for r in specific if r in loc), None)
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
    if loc in GENERIC_REMOTE or any(w in loc for w in GENERIC_WORDS):
        return ("ok", "remote, no restriction stated") if job.remote is not False else ("unknown", "")
    if "unspecified" in loc:
        return "unknown", job.location
    return "restricted", f"location: {job.location}"


def stack_hits(text: str, profile: dict) -> list[str]:
    return [s for s in profile.get("stack", []) if re.search(rf"\b{re.escape(s)}s?\b", text, re.I)]


def evaluate(job: Job, profile: dict) -> tuple[str, int, dict]:
    """Returns (verdict, score, flags)."""
    text = f"{job.title}\n{job.description}"
    reasons: list[str] = []
    verdict = "fit"

    in_scope, why = title_in_scope(job.title, profile)
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

    max_age = profile.get("max_age_days")
    age_days = None
    if job.posted_at is not None:
        age_days = (datetime.now(timezone.utc) - job.posted_at).days
        if max_age is not None and age_days > max_age:
            verdict = "out"
            reasons.append(f"stale: posted {age_days} days ago")

    if not job.description and verdict == "fit":
        verdict = "review"
        reasons.append("no description yet")

    hits = stack_hits(text, profile)
    score = len(hits) + {"ai": 3, "backend": 1}.get(category, 0) + (2 if reg == "ok" else 0)
    if years is not None and max_years is not None and years <= max_years:
        score += 1

    flags = {"age_days": age_days, "years": years, "region": reg, "region_evidence": evidence, "category": category,
             "classic_ml": classic_ml, "llm_terms": llm_terms, "stack": hits, "reasons": reasons}
    return verdict, score, flags


def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    return int(s) if s.isdigit() else WORD_NUMBERS.get(s.lower())


def _contains_term(text: str, term: str) -> bool:
    term = term.lower()
    if re.fullmatch(r"[a-z0-9 ]+", term):
        return re.search(rf"\b{re.escape(term)}\b", text) is not None
    return term in text

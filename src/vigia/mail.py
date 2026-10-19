"""The mailbox as a source: read every new email, keep only what is about the job search.

Each message is read once (Gmail API, read-only scope) and classified by rules:

  alert        a job board's alert (Torre, Indeed, Computrabajo, a company's "new jobs"
               digest...). The postings it links to are opened and go through the same
               filter as every other source.
  application  an ATS, an assessment, or a company that was applied to: a reply to an
               application, linked to it when the company can be told.
  recruiter    a person reaching out (LinkedIn InMail).
  review       looks job-related but no rule is sure. Kept for a person to look at.
  other        everything else. Only the message id is kept, so it is not read again:
               never its sender, subject or text.

Nothing is ever sent, labelled, moved or deleted. The token lives outside the repository
(`sources.gmail.token_file`); see the README for how to create it.

emails
  gmail_id        TEXT PRIMARY KEY
  kind            TEXT     see above
  rule            TEXT     which rule decided it ("alert:torre", "ats:ashbyhq.com", ...)
  received_at     TEXT     NULL for "other"
  sender          TEXT     NULL for "other"
  subject         TEXT     NULL for "other"
  body            TEXT     plain text, cut at 20,000 characters; NULL for "other"
  postings        TEXT     JSON list of the posting URLs an alert led to
  application_id  INTEGER  the application it is about, when known
  reviewed_at     TEXT     set once a person (or the assistant) has gone through it
  processed_at    TEXT
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import requests

from vigia import db, http
from vigia.models import Job
from vigia.sources.base import job_posting_ld, ld_to_job, strip_html

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    gmail_id       TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    rule           TEXT,
    received_at    TEXT,
    sender         TEXT,
    subject        TEXT,
    body           TEXT,
    postings       TEXT,
    application_id INTEGER,
    reviewed_at    TEXT,
    processed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS emails_kind ON emails(kind, reviewed_at);
CREATE INDEX IF NOT EXISTS emails_app ON emails(application_id);
"""

KINDS = ("alert", "application", "recruiter", "review", "other")
KEPT = ("alert", "application", "recruiter", "review")
BODY_MAX = 20_000
API = "https://gmail.googleapis.com/gmail/v1/users/me"


class AuthError(Exception):
    """The token is missing, expired or revoked: the consent flow has to be run again."""


# --- Gmail API -------------------------------------------------------------------

class Gmail:
    """Just enough of the Gmail API, with a refresh token (no Google client library)."""

    def __init__(self, token_file: str | Path):
        path = Path(token_file)
        if not path.exists():
            raise AuthError(f"no token at {path}")
        self.cred = json.loads(path.read_text())
        self.access = None
        self.expires = 0.0
        self.session = requests.Session()

    def _token(self) -> str:
        if self.access and time.time() < self.expires - 60:
            return self.access
        resp = self.session.post(self.cred.get("token_uri", "https://oauth2.googleapis.com/token"), data={
            "grant_type": "refresh_token", "refresh_token": self.cred["refresh_token"],
            "client_id": self.cred["client_id"], "client_secret": self.cred["client_secret"]}, timeout=30)
        if resp.status_code != 200:
            raise AuthError(f"token refresh failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        self.access, self.expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self.access

    def _get(self, kind: str, path: str, params: dict | None = None) -> dict:
        started = time.monotonic()
        status = 0
        try:
            resp = self.session.get(f"{API}/{path}", params=params, timeout=30,
                                    headers={"Authorization": f"Bearer {self._token()}"})
            status = resp.status_code
        finally:
            db.log_request("gmail", kind, status, int((time.monotonic() - started) * 1000))
        if status == 401:
            raise AuthError("the API refused the token")
        if status == 429:
            raise http.Throttled("gmail: 429")
        resp.raise_for_status()
        return resp.json()

    def ids(self, query: str, limit: int) -> list[str]:
        out, page = [], None
        while len(out) < limit:
            data = self._get("list", "messages", {"q": query, "maxResults": 500, "pageToken": page})
            out += [m["id"] for m in data.get("messages") or []]
            page = data.get("nextPageToken")
            if not page:
                break
        return out[:limit]

    def message(self, gmail_id: str) -> dict:
        return self._get("message", f"messages/{gmail_id}", {"format": "full"})


def parse(msg: dict) -> dict:
    """A Gmail API message as {id, received_at, sender, address, subject, text, html}."""
    payload = msg.get("payload") or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers") or []}
    plain, rich = [], []

    def walk(part):
        data = (part.get("body") or {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (rich if part.get("mimeType") == "text/html" else plain if part.get("mimeType") == "text/plain"
             else []).append(text)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    when = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, timezone.utc)
    sender = headers.get("from", "")
    return {"id": msg["id"], "received_at": when.isoformat(timespec="seconds"),
            "internal": int(msg.get("internalDate", 0)), "sender": sender,
            "address": parseaddr(sender)[1].lower(), "subject": headers.get("subject", ""),
            "text": "\n".join(plain), "html": "\n".join(rich)}


# --- classification --------------------------------------------------------------

# Job alerts: sender pattern -> board, with an optional subject pattern that must also match.
ALERTS = [
    (r"jobalerts-noreply@linkedin\.com|jobs-listings@linkedin\.com|jobs-noreply@linkedin\.com", "linkedin", None),
    (r"@torre\.ai$", "torre", r"^(full-time|part-time|freelance|contract|internship|temporary|flexible)\b"),
    (r"indeed\.com$", "indeed", None),
    (r"computrabajo\.com$", "computrabajo", None),
    (r"multitrabajos\.com$|bumeran\.com", "multitrabajos", None),
    (r"getonbrd\.com$|getonboard", "getonbrd", r"job|empleo|trabajo|vacante|match"),
    (r"teamtailor-mail\.com$", "teamtailor", r"new jobs? matching|nuevos? empleos?"),
    (r"himalayas\.app$", "himalayas", None),
    (r"weworkremotely\.com$", "weworkremotely", r"\bjobs?\b"),
    (r"glassdoor\.com$|wellfound\.com$|remoteok\.com$|jobicy\.com$|workingnomads\.com$", "board", r"job|empleo"),
]

# Applicant tracking systems, assessments and talent platforms: their mail is about an application.
ATS = re.compile(
    r"(ashbyhq\.com|greenhouse\.io|greenhouse-mail\.io|lever\.co|workablemail\.com|workable\.com|"
    r"teamtailor-mail\.com|smartrecruiters\.com|myworkday\.com|icims\.com|bamboohr\.com|recruitee\.com|"
    r"jobvite\.com|breezy\.hr|personio\.|predictiveindex\.com|hackerrank\.com|codility\.com|"
    r"testgorilla\.com|coderbyte\.com|micro1\.ai|vanhack\.com|hireflix\.com|willo\.video)$")

LINKEDIN = re.compile(r"@linkedin\.com$")
# Job platforms whose own messages about an application ("Finish your pre-screening for: ...")
# come from the same address as their alerts and marketing.
PLATFORMS = re.compile(r"(torre\.ai|getonbrd\.com|indeed\.com|computrabajo\.com|multitrabajos\.com|"
                       r"weworkremotely\.com|himalayas\.app)$")
INMAIL = re.compile(r"inmail-hit-reply@linkedin\.com$")
APPLICATION_SUBJECT = re.compile(
    r"appl(y|ied|ying|ication)|candidat|postula|interview|entrevista|assessment|evaluaci|shortlist|"
    r"next step|siguiente paso|screen|offer\b|oferta\b|your profile|tu perfil|update|actualizaci|"
    r"thank you|gracias|welcome|bienvenid|reject|unfortunately|lamentablemente|status", re.I)
JOB_WORDS = re.compile(
    r"\bjobs?\b|empleo|vacante|oferta laboral|oportunidad laboral|job opportunit|position|posici[oó]n|"
    r"\brole\b|\bhiring\b|recruit|reclut|entrevista|interview|candidat|postula|your application|"
    r"job application|application (update|status|received)|for applying|talent|selecci[oó]n de personal|propuesta laboral", re.I)
JOB_SENDER = re.compile(r"recruit|talent|career|hiring|jobs?@|rrhh|people@|hr@|seleccion", re.I)
# Words that say nothing about which company it is.
GENERIC = {"agency", "systems", "system", "labs", "lab", "studios", "studio", "software", "technologies",
           "technology", "tech", "group", "global", "inc", "llc", "ltd", "corp", "company", "client", "named",
           "remote", "the", "and", "solutions", "services", "digital", "consulting", "partners", "team",
           "hiring", "careers", "jobs", "talent", "network", "expert", "confidencial", "not"}


def company_tokens(company: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (company or "").lower().split("(")[0])
    return [w for w in words if w not in GENERIC and (len(w) >= 4 or (len(w) == 3 and re.search(r"\d", w)))]


def match_application(mail: dict, apps: list[dict], in_subject: bool) -> dict | None:
    """The most recent application whose company shows in the sender (or the subject, when
    asked), e.g. "Talent @ Ledgerly", dana@forgeworkscareers.com, "Thanks for applying to Nubeware"."""
    hay = f"{mail['sender']} {mail['address']}".lower()
    if in_subject:
        hay += " " + mail["subject"].lower()
    # A token matches a whole word or the start of one ("forgeworks" in forgeworkscareers.com),
    # never across two of them (a company called "Devsup" must not match "x.dev support").
    words = re.findall(r"[a-z0-9]+", hay)
    for a in apps:  # newest first
        tokens = company_tokens(a["company"])
        if tokens and any(w.startswith(t) for t in tokens for w in words):
            return a
    return None


def classify(mail: dict, apps: list[dict]) -> tuple[str, str, dict | None]:
    """(kind, rule, application) for one parsed message."""
    addr, subject = mail["address"], mail["subject"]
    domain = addr.rsplit("@", 1)[-1]
    for sender_re, board, subject_re in ALERTS:
        if re.search(sender_re, addr) and (subject_re is None or re.search(subject_re, subject, re.I)):
            return "alert", f"alert:{board}", None
    if INMAIL.search(addr):
        return "recruiter", "linkedin:inmail", match_application(mail, apps, in_subject=True)
    if LINKEDIN.search(addr):
        if addr.startswith("messages-noreply") and re.search(r"sent you a message|te envi[oó] un mensaje", subject, re.I):
            return "recruiter", "linkedin:message", None
        return "other", "linkedin:notification", None
    app = match_application(mail, apps, in_subject=False)
    if app:
        return "application", f"company:{a_name(app)}", app
    if ATS.search(domain):
        return "application", f"ats:{domain}", match_application(mail, apps, in_subject=True)
    if PLATFORMS.search(domain) and APPLICATION_SUBJECT.search(subject) and not re.search(r"invest", subject, re.I):
        return "application", f"platform:{domain}", match_application(mail, apps, in_subject=True)
    if APPLICATION_SUBJECT.search(subject):
        app = match_application(mail, apps, in_subject=True)
        if app:
            return "application", f"subject:{a_name(app)}", app
    if JOB_WORDS.search(subject) or JOB_SENDER.search(addr.split("@")[0]):
        return "review", "job words", None
    return "other", "", None


def a_name(app: dict) -> str:
    return f"#{app['number']} {app['company']}"


# --- job links in alerts ---------------------------------------------------------

HREF_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
POSTING_RE = re.compile(
    r"torre\.ai/post/|indeed\.[a-z.]+/(viewjob|rc/clk|pagead/clk|m/viewjob)|[?&]jk=|teamtailor\.com/jobs/\d|"
    r"/jobs/\d{4,}|himalayas\.app/companies/[^/]+/jobs/|himalayas\.app/job/email\?.*job_id=|weworkremotely\.com/remote-jobs/[^/?]+-|"
    r"computrabajo\.com/ofertas-de-trabajo/oferta-de-trabajo-|getonbrd\.com/jobs/[^/?]+/|"
    r"linkedin\.com/(comm/)?jobs/view/\d+|jobs\.ashbyhq\.com/[^/]+/[0-9a-f-]{8,}|greenhouse\.io/[^/]+/jobs/\d+|"
    r"jobs\.lever\.co/[^/]+/[0-9a-f-]{8,}|apply\.workable\.com/[^/]+/j/|multitrabajos\.com/empleos/\d", re.I)
SHORTENER_RE = re.compile(r"torre\.ai/s/|cts\.indeed\.com/|lnkd\.in/", re.I)
SKIP_TEXT = re.compile(
    r"unsubscribe|cancelar|darse de baja|privacy|privacidad|preferenc|t[eé]rminos|terms|help|ayuda|"
    r"asistencia|perfil|profile|manage|aviso legal|contact|app store|google play|learn more|browse|"
    r"see more|ver m[aá]s|ver oportunidades|signal|editar|pausar|mala coincidencia|^\s*(s[ií]|no)\s*$|"
    r"invest|login|log in|sign in", re.I)
LINKEDIN_VIEW_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)")


def unwrap(url: str) -> str:
    """The real destination of a tracking link, when it is written inside it (links can be
    wrapped more than once: Himalayas' own redirect inside Amazon SES tracking)."""
    url = html.unescape(url).strip()
    for _ in range(3):
        inner = _unwrap_once(url)
        if inner == url:
            break
        url = inner
    return url


def _unwrap_once(url: str) -> str:
    m = re.search(r"awstrack\.me/L\d/([^/]+)", url)
    if m:
        return unquote(m.group(1))
    m = re.search(r"/e/c/([A-Za-z0-9_\-=]+)", url)  # Customer.io-style links (We Work Remotely)
    if m:
        try:
            raw = base64.urlsafe_b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4))
            href = json.loads(raw).get("href")
            if href:
                return href
        except ValueError:
            pass
    query = parse_qs(urlsplit(url).query)
    for key in ("url", "u", "redirect", "target", "dest"):
        for v in query.get(key, []):
            if v.startswith("http"):
                return unquote(v)
    return url


def posting_links(html_text: str, limit: int = 15) -> list[tuple[str, str]]:
    """(url, anchor text) of the links in an alert that lead to a posting."""
    out, seen = [], set()
    for href, inner in HREF_RE.findall(html_text or ""):
        text = re.sub(r"<[^>]+>|&[a-z#0-9]+;|\s+", " ", inner).strip()
        url = unwrap(href)
        if url in seen or (text and SKIP_TEXT.search(text)):
            continue
        if POSTING_RE.search(url) or (SHORTENER_RE.search(url) and text):
            seen.add(url)
            out.append((url, text))
        if len(out) >= limit:
            break
    return out


def canonical(url: str) -> str:
    """Tracking parameters off, except the ones that name the posting (Indeed's jk)."""
    parts = urlsplit(url)
    jk = parse_qs(parts.query).get("jk")
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + (f"?jk={jk[0]}" if jk else "")


def resolve(url: str, text: str, board: str, subject: str) -> Job | None:
    """Open a posting link and read the posting. None when it does not lead to one."""
    m = LINKEDIN_VIEW_RE.search(url)
    if m:
        return _linkedin_posting(m.group(1))
    resp = http.get("gmail", f"posting:{board}", url)
    if resp is None:
        return _stub(url, text, board, subject)
    final = getattr(resp, "url", url) or url
    m = LINKEDIN_VIEW_RE.search(final)
    if m:
        return _linkedin_posting(m.group(1))
    ld = job_posting_ld(resp.text)
    if ld and ld.get("title"):
        job = ld_to_job("gmail", canonical(final), canonical(final), ld)
        job.tags.append(f"via:{board}")
        return job
    return _stub(final, text, board, subject)


HIMALAYAS_RE = re.compile(r"himalayas\.app/companies/([^/]+)/jobs/")


def _stub(url: str, text: str, board: str, subject: str) -> Job | None:
    """A posting whose page is not served to us, built from the email itself and kept
    without text (the filter judges it on the title until a source brings the full one):
    Indeed says "<title> en <company>" in the subject; Himalayas has the company in the URL."""
    m = HIMALAYAS_RE.search(url)
    if m and text:
        company = m.group(1).replace("-io", ".io").replace("-", " ")
        return Job(source="gmail", source_id=canonical(url), title=text, company=company,
                   url=canonical(url), tags=[f"via:{board}"])
    m = re.match(r"^(.+?) (?:en|at) (.+)$", subject or "")
    if board != "indeed" or not m:
        return None
    return Job(source="gmail", source_id=canonical(url), title=m.group(1).strip(),
               company=m.group(2).strip(), url=canonical(url), tags=[f"via:{board}"])


def _linkedin_posting(job_id: str) -> Job | None:
    from bs4 import BeautifulSoup

    from vigia.linkedin_guest import Blocked, Guest
    from vigia.sources.linkedin import POSTING, parse_description
    try:
        resp = Guest("gmail").get("posting", POSTING.format(job_id))
    except Blocked:
        return None
    if resp is None:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.select_one(".top-card-layout__title, h2")
    company = soup.select_one(".topcard__org-name-link, .topcard__flavor")
    place = soup.select_one(".topcard__flavor--bullet")
    if not title or not company:
        return None
    url = f"https://www.linkedin.com/jobs/view/{job_id}"
    return Job(source="gmail", source_id=url, title=title.get_text(strip=True),
               company=company.get_text(strip=True), url=url,
               location=place.get_text(strip=True) if place else "",
               description=parse_description(resp.text), tags=["via:linkedin"])


# --- storage ---------------------------------------------------------------------

def known(ids: list[str]) -> set[str]:
    if not ids:
        return set()
    out = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = db.conn().execute(f"SELECT gmail_id FROM emails WHERE gmail_id IN ({','.join('?' * len(chunk))})", chunk)
        out |= {r["gmail_id"] for r in rows}
    return out


def save(mail: dict, kind: str, rule: str, app: dict | None, postings: list[str] | None = None) -> None:
    if kind == "other":  # only the id: enough to never read it again
        db.conn().execute("INSERT OR IGNORE INTO emails(gmail_id, kind, rule, processed_at) VALUES (?, 'other', ?, ?)",
                          (mail["id"], rule or None, db.now()))
        return
    body = mail["text"].strip() or strip_html(mail["html"])
    db.conn().execute(
        """INSERT OR IGNORE INTO emails(gmail_id, kind, rule, received_at, sender, subject, body, postings,
               application_id, processed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mail["id"], kind, rule, mail["received_at"], mail["sender"], mail["subject"], body[:BODY_MAX],
         json.dumps(postings or []), app["id"] if app else None, db.now()))


def _apps() -> list[dict]:
    rows = db.conn().execute("SELECT id, number, company FROM applications ORDER BY COALESCE(applied_at, created_at) DESC, id DESC")
    return [dict(r) for r in rows]


# --- the run ---------------------------------------------------------------------

def sync(cfg: dict, client: Gmail | None = None) -> list[Job]:
    """Read every message not seen yet, store what is about jobs, and return the postings the
    alerts led to (the caller flags and stores them like any source's)."""
    client = client or Gmail(cfg.get("token_file", "/app/secrets/gmail-token.json"))
    after = db.get_state("gmail.after")
    if after:
        query = f"after:{int(after) // 1000 - 86_400}"  # a day of overlap: ids already read are skipped
    else:
        query = f"after:{(cfg.get('since') or datetime.now().date().isoformat()).replace('-', '/')}"
    ids = client.ids(query, int(cfg.get("max_list", 2000)))
    seen = known(ids)
    unread = [i for i in ids if i not in seen][::-1]  # Gmail lists newest first; read oldest first
    fresh = unread[: int(cfg.get("max_messages", 300))]
    apps = _apps()
    budget = int(cfg.get("max_postings", 40))
    jobs: list[Job] = []
    newest = int(after or 0)
    for gmail_id in fresh:
        mail = parse(client.message(gmail_id))
        kind, rule, app = classify(mail, apps)
        found: list[str] = []
        if kind == "alert" and budget > 0:
            board = rule.split(":", 1)[1]
            for url, text in posting_links(mail["html"]):
                if budget <= 0:
                    break
                budget -= 1
                job = resolve(url, text, board, mail["subject"])
                if job and job.title and job.company:
                    jobs.append(job)
                    found.append(job.url)
        save(mail, kind, rule, app, found)
        db.conn().commit()
        newest = max(newest, mail["internal"])
    if newest and len(fresh) == len(unread):  # otherwise the next run must list the rest again
        db.set_state("gmail.after", str(newest))
    log.info("gmail: %d listed, %d new, %d postings", len(ids), len(fresh), len(jobs))
    return jobs


# --- reading back ----------------------------------------------------------------

def pending(c=None, include_alerts: bool = False, limit: int = 100) -> list[dict]:
    """Kept emails nobody has gone through yet, oldest first."""
    c = c or db.conn()
    kinds = KEPT if include_alerts else tuple(k for k in KEPT if k != "alert")
    rows = c.execute(
        f"""SELECT e.*, a.number AS app_number, a.company AS app_company, a.status AS app_status
            FROM emails e LEFT JOIN applications a ON a.id = e.application_id
            WHERE e.reviewed_at IS NULL AND e.kind IN ({','.join('?' * len(kinds))})
            ORDER BY e.received_at LIMIT ?""", (*kinds, limit))
    return [dict(r) for r in rows]


def listing(c=None, kind: str = "", unreviewed: bool = False, application_id: int | None = None,
            limit: int = 300) -> list[dict]:
    c = c or db.conn()
    where, args = ["e.kind != 'other'"], []
    if kind in KINDS:
        where.append("e.kind = ?")
        args.append(kind)
    if unreviewed:
        where.append("e.reviewed_at IS NULL")
    if application_id is not None:
        where.append("e.application_id = ?")
        args.append(application_id)
    rows = c.execute(
        f"""SELECT e.*, a.number AS app_number, a.company AS app_company FROM emails e
            LEFT JOIN applications a ON a.id = e.application_id WHERE {' AND '.join(where)}
            ORDER BY e.received_at DESC LIMIT ?""", (*args, limit))
    return [dict(r) for r in rows]


def get(gmail_id: str, c=None) -> dict:
    c = c or db.conn()
    row = c.execute("""SELECT e.*, a.number AS app_number, a.company AS app_company FROM emails e
                       LEFT JOIN applications a ON a.id = e.application_id WHERE e.gmail_id = ?""",
                    (gmail_id,)).fetchone()
    if row is None:
        raise LookupError(f"no email {gmail_id}")
    return dict(row)


def mark_reviewed(ids: list[str] | None = None, c=None) -> int:
    """Mark these emails (or every pending one) as gone through."""
    c = c or db.conn()
    if ids:
        cur = c.execute(f"UPDATE emails SET reviewed_at = ? WHERE reviewed_at IS NULL AND gmail_id IN "
                        f"({','.join('?' * len(ids))})", (db.now(), *ids))
    else:
        cur = c.execute("UPDATE emails SET reviewed_at = ? WHERE reviewed_at IS NULL AND kind != 'other'", (db.now(),))
    c.commit()
    return cur.rowcount


def set_kind(gmail_id: str, kind: str, application_number: int | None = None, c=None) -> dict:
    """Correct a classification (and which application it is about). Setting "other" deletes
    everything but the id, as if it had been classified so from the start."""
    c = c or db.conn()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    get(gmail_id, c)
    app_id = None
    if application_number is not None:
        row = c.execute("SELECT id FROM applications WHERE number = ? ORDER BY id DESC", (application_number,)).fetchone()
        if row is None:
            raise LookupError(f"no application #{application_number}")
        app_id = row["id"]
    if kind == "other":
        c.execute("""UPDATE emails SET kind = 'other', rule = 'manual', received_at = NULL, sender = NULL,
                     subject = NULL, body = NULL, postings = NULL, application_id = NULL WHERE gmail_id = ?""",
                  (gmail_id,))
    else:
        c.execute("UPDATE emails SET kind = ?, rule = 'manual', application_id = COALESCE(?, application_id) "
                  "WHERE gmail_id = ?", (kind, app_id, gmail_id))
    c.commit()
    return get(gmail_id, c)


def report(rows: list[dict], body_chars: int = 700) -> str:
    """Pending emails as Markdown, grouped by kind."""
    if not rows:
        return "No emails waiting."
    out = []
    for kind in ("application", "recruiter", "review", "alert"):
        group = [r for r in rows if r["kind"] == kind]
        if not group:
            continue
        out.append(f"## {kind} ({len(group)})")
        for r in group:
            app = f" → #{r['app_number']} {r['app_company']} ({r.get('app_status') or ''})" if r.get("app_number") else ""
            out.append(f"- `{r['gmail_id']}` {(r['received_at'] or '')[:16]} · {r['sender']}\n"
                       f"  **{r['subject']}**{app} · rule {r['rule']}")
            if kind == "alert":
                out.append(f"  postings found: {len(json.loads(r['postings'] or '[]'))}")
            else:
                body = re.sub(r"\s+", " ", r["body"] or "").strip()
                out.append(f"  > {body[:body_chars]}{'…' if len(body) > body_chars else ''}")
    return "\n".join(out)

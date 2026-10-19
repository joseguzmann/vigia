"""The mailbox source, against messages shaped like the real ones (2026-10-19)."""
import base64
import json

from vigia import applications as apps
from vigia import db, mail
from vigia.models import Job


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(mid, sender, subject, html="", text="", when=1_760_300_000_000):
    parts = []
    if text:
        parts.append({"mimeType": "text/plain", "body": {"data": b64(text)}})
    if html:
        parts.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    return {"id": mid, "internalDate": str(when), "payload": {
        "mimeType": "multipart/alternative", "parts": parts,
        "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]}}


def m(sender, subject):
    return mail.parse(message("x", sender, subject))


def app(company, number=1):
    a = apps.add(company=company, title="Engineer", origin="assistant", number=number)
    return {"id": a["id"], "number": a["number"], "company": a["company"]}


# --- classification --------------------------------------------------------------

def test_board_alerts():
    assert mail.classify(m("Torre <torre@torre.ai>", "Full-time (remote): Front-End Developer at Initech"), [])[:2] == ("alert", "alert:torre")
    assert mail.classify(m("Indeed <donotreply@match.indeed.com>", "Full Stack Developer en Harbor"), [])[0] == "alert"
    assert mail.classify(m("Avenga <no-reply@avenga.teamtailor-mail.com>", "Avenga: one new job matching your profile"), [])[0] == "alert"
    assert mail.classify(m("Equipo de Computrabajo <empleos_ec@computrabajo.com>", "¡Te buscan para nuevos empleos!"), [])[0] == "alert"


def test_torre_marketing_is_not_an_alert_nor_a_reply():
    assert mail.classify(m('"Torre.ai" <torre@torre.ai>', "Last day to invest in Torre.ai with a 10% better deal"), [])[0] == "other"


def test_linkedin_noise_and_inmail():
    assert mail.classify(m("LinkedIn <messages-noreply@linkedin.com>", "24 people viewed your profile"), [])[0] == "other"
    assert mail.classify(m("Ana <invitations@linkedin.com>", "I want to connect"), [])[0] == "other"
    kind, rule, _ = mail.classify(m('"Recruiter Acme" <inmail-hit-reply@linkedin.com>', "AI Engineer opportunity"), [])
    assert (kind, rule) == ("recruiter", "linkedin:inmail")


def test_replies_link_to_the_application():
    nube, dual, black = app("Nubeware", 39), app("Ledgerly", 37), app("Forgeworks Agency", 35)
    rows = mail._apps()
    kind, _, a = mail.classify(m('"Talent @ Ledgerly" <no-reply@ashbyhq.com>', "Thanks for applying to Ledgerly!"), rows)
    assert kind == "application" and a["id"] == dual["id"]
    kind, _, a = mail.classify(m("Workable <noreply@candidates.workablemail.com>", "Thanks for applying to Nubeware"), rows)
    assert kind == "application" and a["id"] == nube["id"]
    kind, _, a = mail.classify(m("Dana Cole <dana@forgeworkscareers.com>", "Book your engineering screen"), rows)
    assert kind == "application" and a["id"] == black["id"]


def test_an_ats_without_a_known_application_is_still_an_application():
    kind, rule, a = mail.classify(m("Corvid Hiring Team <no-reply@ashbyhq.com>", "Thanks for applying to Corvid!"), [])
    assert (kind, rule, a) == ("application", "ats:ashbyhq.com", None)


def test_job_words_go_to_review_and_the_rest_is_other():
    assert mail.classify(m("Lena <lena@quillstack.ai>", "Quillstack Application Update"), [])[0] == "review"
    assert mail.classify(m("Some Bank <alerts@somebank.example>", "Tu estado de cuenta"), [])[0] == "other"
    assert mail.classify(m("GitHub <noreply@github.com>", "A third-party OAuth application has been added"), [])[0] == "other"
    assert mail.classify(m("Udemy <hello@students.udemy.com>", "José, take the next step in your career"), [])[0] == "other"


def test_company_tokens_match_whole_words_only():
    rows = [app("Nubeware", 39), app("n8n", 31)]
    assert mail.classify(m("Searchy <support@serper.dev>", "Please verify your email address"), mail._apps())[0] == "other"
    kind, _, a = mail.classify(m("n8n Hiring Team <no-reply@ashbyhq.com>", "We've received your application"), mail._apps())
    assert kind == "application" and a["number"] == 31


def test_platform_messages_about_an_application():
    kind, rule, _ = mail.classify(m("Torre <torre@torre.ai>", "Finish your pre-screening for: Full-Stack Python Developer"), [])
    assert (kind, rule) == ("application", "platform:torre.ai")


def test_generic_company_words_do_not_match():
    rows = [app("Forgeworks Agency (client not named)", 10)]
    assert mail.classify(m("Some Agency <news@agency.com>", "Our newsletter"), rows)[0] == "other"


# --- links -----------------------------------------------------------------------

def test_unwrap_tracking_links():
    aws = "https://x.r.us-east-1.awstrack.me/L0/https:%2F%2Fhimalayas.app%2Fcompanies%2Facme%2Fjobs%2Fai-engineer/1/abc"
    assert mail.unwrap(aws) == "https://himalayas.app/companies/acme/jobs/ai-engineer"
    payload = base64.urlsafe_b64encode(json.dumps({"href": "https://weworkremotely.com/remote-jobs/acme-dev"}).encode()).decode()
    assert mail.unwrap(f"https://email.m.weworkremotely.com/e/c/{payload}") == "https://weworkremotely.com/remote-jobs/acme-dev"
    ct = "https://go.computrabajo.com/go/goa?url=https%3a%2f%2fec.computrabajo.com%2fofertas-de-trabajo%2foferta-de-trabajo-de-dev-ABC"
    assert mail.unwrap(ct).startswith("https://ec.computrabajo.com/ofertas-de-trabajo/oferta-de-trabajo-de-dev")


def test_himalayas_double_wrapped_link_becomes_a_posting_without_its_page(monkeypatch):
    href = ("https://x.r.us-east-1.awstrack.me/L0/https:%2F%2Fhimalayas.app%2Fjob%2Femail%3Fjob_id%3D1%26redirect%3D"
            "https%253A%252F%252Fhimalayas.app%252Fcompanies%252Fpear-io%252Fjobs%252Fsenior-full-stack-developer/1/x")
    [(url, text)] = mail.posting_links(f'<a href="{href}">Senior React Full-stack Developer</a>')
    assert url == "https://himalayas.app/companies/pear-io/jobs/senior-full-stack-developer"
    monkeypatch.setattr(mail.http, "get", lambda *a, **k: None)  # the site answers 403
    job = mail.resolve(url, text, "himalayas", "You've got new matches")
    assert (job.title, job.company) == ("Senior React Full-stack Developer", "pear.io")


def test_posting_links_skip_footer_links():
    html = ('<a href="https://torre.ai/s/AAA"></a>'
            '<a href="https://torre.ai/s/BBB">Front-End Developer</a>'
            '<a href="https://torre.ai/s/CCC">Unsubscribe from job alerts</a>'
            '<a href="https://career.avenga.com/jobs/8569519-ai-engineer">AI Engineer</a>'
            '<a href="https://career.avenga.com/privacy-policy">Privacy Policy</a>')
    assert [u for u, _ in mail.posting_links(html)] == ["https://torre.ai/s/BBB", "https://career.avenga.com/jobs/8569519-ai-engineer"]


def test_indeed_posting_falls_back_to_the_subject(monkeypatch):
    monkeypatch.setattr(mail.http, "get", lambda *a, **k: None)
    job = mail.resolve("https://cts.indeed.com/v3/xyz", "Ver empleo", "indeed", "Full Stack Developer en Harbor")
    assert (job.title, job.company) == ("Full Stack Developer", "Harbor")


def test_posting_read_from_json_ld(monkeypatch):
    page = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "AI Engineer", '
            '"hiringOrganization": {"name": "Acme"}, "jobLocationType": "TELECOMMUTE", '
            '"description": "<p>LLMs</p>"}</script>')

    class Resp:
        text = page
        url = "https://torre.ai/post/abc-acme-ai-engineer?utm_source=email"

    monkeypatch.setattr(mail.http, "get", lambda *a, **k: Resp())
    job = mail.resolve("https://torre.ai/s/BBB", "AI Engineer", "torre", "")
    assert (job.title, job.company, job.remote, job.url) == ("AI Engineer", "Acme", True, "https://torre.ai/post/abc-acme-ai-engineer")
    assert "via:torre" in job.tags


# --- the run ---------------------------------------------------------------------

class FakeGmail:
    def __init__(self, messages):
        self.messages = {msg["id"]: msg for msg in messages}
        self.read = []

    def ids(self, query, limit):
        return list(reversed(self.messages))  # newest first, like Gmail

    def message(self, gmail_id):
        self.read.append(gmail_id)
        return self.messages[gmail_id]


def test_sync_keeps_job_mail_and_only_the_id_of_the_rest(monkeypatch):
    dual = app("Ledgerly", 37)
    alert = message("a1", "Torre <torre@torre.ai>", "Full-time (remote): AI Engineer at Acme",
                    html='<a href="https://torre.ai/s/BBB">AI Engineer</a>', when=1)
    reply = message("r1", '"Talent @ Ledgerly" <no-reply@ashbyhq.com>', "Thanks for applying to Ledgerly!",
                    text="We received your application.", when=2)
    bank = message("b1", "Some Bank <alerts@somebank.example>", "Tu estado de cuenta", text="Saldo: 123", when=3)
    monkeypatch.setattr(mail, "resolve", lambda url, text, board, subject: Job(
        source="gmail", source_id=url, title="AI Engineer", company="Acme", url=url))
    client = FakeGmail([alert, reply, bank])

    jobs = mail.sync({"since": "2026-10-12"}, client)

    assert [j.company for j in jobs] == ["Acme"]
    rows = {r["gmail_id"]: dict(r) for r in db.conn().execute("SELECT * FROM emails")}
    assert rows["r1"]["kind"] == "application" and rows["r1"]["application_id"] == dual["id"]
    assert rows["r1"]["body"] == "We received your application."
    assert json.loads(rows["a1"]["postings"]) == ["https://torre.ai/s/BBB"]
    other = rows["b1"]
    assert other["kind"] == "other" and other["sender"] is None and other["subject"] is None and other["body"] is None

    client.read.clear()
    assert mail.sync({}, client) == [] and client.read == []  # nothing is read twice


def test_a_backlog_larger_than_one_run_is_finished_later(monkeypatch):
    msgs = [message(f"m{i}", "Banco <banco@x.com>", "Hola", when=i + 1) for i in range(5)]
    client = FakeGmail(msgs)
    mail.sync({"max_messages": 3}, client)
    assert db.get_state("gmail.after") is None
    mail.sync({"max_messages": 3}, client)
    assert db.conn().execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 5
    assert db.get_state("gmail.after") == "5"


def test_pending_review_and_reclassify():
    app("Nubeware", 39)
    client = FakeGmail([message("r1", "Workable <noreply@candidates.workablemail.com>", "Thanks for applying to Nubeware", text="hi")])
    mail.sync({}, client)
    pending = mail.pending()
    assert [p["gmail_id"] for p in pending] == ["r1"] and pending[0]["app_number"] == 39
    assert "Thanks for applying to Nubeware" in mail.report(pending)
    mail.set_kind("r1", "other")
    row = db.conn().execute("SELECT * FROM emails WHERE gmail_id = 'r1'").fetchone()
    assert row["subject"] is None and row["body"] is None
    assert mail.pending() == []


def test_emails_page_and_application_section():
    from fastapi.testclient import TestClient

    from vigia.web import create_app
    a = app("Nubeware", 39)
    mail.sync({}, FakeGmail([message("r1", "Workable <noreply@candidates.workablemail.com>", "Thanks for applying to Nubeware", text="hello")]))
    db.conn().commit()
    client = TestClient(create_app())
    assert "Thanks for applying to Nubeware" in client.get("/emails").text
    assert "hello" in client.get("/emails/r1").text
    assert "Thanks for applying to Nubeware" in client.get(f"/applications/{a['id']}").text
    client.post("/emails/r1", data={"reviewed": "1"})
    assert mail.pending() == []

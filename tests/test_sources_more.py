"""The ATS and board added on 2026-10-15, against responses shaped like the real ones."""
import json

from vigia import http
from vigia.sources import ats_more, boards

TT_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations"><channel><title>Avenga</title>
<item><title>AI Engineer</title><description>&lt;p&gt;Build LLM agents. Remote LATAM.&lt;/p&gt;</description>
<pubDate>Thu, 15 Oct 2026 15:27:10 +0200</pubDate><link>https://career.avenga.com/jobs/1-ai-engineer</link>
<remoteStatus>fully</remoteStatus><guid>g-1</guid><company_name>Avenga</company_name>
<tt:locations><tt:location><tt:city>Bogota</tt:city><tt:country>Colombia</tt:country></tt:location></tt:locations>
</item></channel></rss>"""


class Resp:
    def __init__(self, body, status=200):
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.text = self.content.decode()
        self.status_code = status

    def json(self):
        return json.loads(self.content)


def serve(monkeypatch, routes):
    """routes: substring of URL -> body. Records the URLs asked for."""
    asked = []

    def get(source, kind, url, **k):
        asked.append(url)
        for key, body in routes.items():
            if key in url:
                return Resp(body)
        return None

    monkeypatch.setattr(http, "get", get)
    monkeypatch.setattr(http, "get_json", lambda *a, **k: (r := get(*a, **k)) and r.json())
    return asked


def test_teamtailor(monkeypatch):
    serve(monkeypatch, {"avenga.teamtailor.com/jobs.rss": TT_RSS})
    [j] = ats_more.Teamtailor({"boards": ["avenga"]}).fetch()
    assert (j.title, j.company, j.location, j.remote) == ("AI Engineer", "Avenga", "Bogota, Colombia (Remote)", True)
    assert j.posted_at.timestamp() == 1792070830 and "LLM agents" in j.description


def test_workable(monkeypatch):
    serve(monkeypatch, {"widget/accounts/platzi": {"name": "Platzi", "jobs": [
        {"title": "Backend Engineer", "shortcode": "ABC", "url": "https://apply.workable.com/j/ABC",
         "telecommuting": True, "published_on": "2026-10-10", "description": "<p>Python, Django</p>",
         "locations": [{"city": "Bogotá", "country": "Colombia"}]}]}})
    [j] = ats_more.Workable({"boards": ["platzi"]}).fetch()
    assert (j.company, j.location, j.remote, j.description) == ("Platzi", "Bogotá, Colombia (Remote)", True, "Python, Django")


def test_smartrecruiters_fetches_text_only_in_scope(monkeypatch):
    listing = {"totalFound": 2, "content": [
        {"id": "1", "name": "AI Engineer", "releasedDate": "2026-10-15T14:14:24.542Z", "company": {"name": "Endava"},
         "location": {"fullLocation": "Medellín, , Colombia", "remote": True}},
        {"id": "2", "name": "Junior Service Desk Agent", "company": {"name": "Endava"}, "location": {}}]}
    detail = {"postingUrl": "https://jobs.smartrecruiters.com/Endava/1-ai-engineer",
              "jobAd": {"sections": {"jobDescription": {"text": "<p>LLM agents</p>"},
                                     "qualifications": {"text": "<ul><li>Python</li></ul>"}}}}
    asked = serve(monkeypatch, {"postings/1": detail, "companies/endava/postings": listing})
    jobs = ats_more.SmartRecruiters({"boards": ["endava"]}).fetch()
    assert [j.title for j in jobs] == ["AI Engineer", "Junior Service Desk Agent"]
    assert "LLM agents" in jobs[0].description and "Python" in jobs[0].description
    assert jobs[0].location == "Medellín, Colombia (Remote)" and jobs[0].url.endswith("1-ai-engineer")
    assert not any("postings/2" in u for u in asked)


def test_breezy_reads_text_from_the_job_page(monkeypatch):
    page = ('<script type="application/ld+json">{"@type": "JobPosting", "description": "<p>React, Node</p>"}'
            '</script>').encode()
    serve(monkeypatch, {"fluxit.breezy.hr/json": [
        {"id": "x1", "name": "Full Stack Developer", "url": "https://fluxit.breezy.hr/p/x1",
         "published_date": "2026-09-08T11:54:35.879Z", "company": {"name": "Flux IT SA"},
         "locations": [{"name": "Buenos Aires, AR", "is_remote": True}]}],
        "fluxit.breezy.hr/p/x1": page})
    [j] = ats_more.Breezy({"boards": ["fluxit"]}).fetch()
    assert (j.company, j.remote, j.description) == ("Flux IT SA", True, "React, Node")
    assert j.location == "Buenos Aires, AR (Remote)"


def test_recruitee(monkeypatch):
    serve(monkeypatch, {"aerolab.recruitee.com": {"offers": [
        {"id": 9, "title": "Frontend Developer", "company_name": "Aerolab", "careers_url": "https://aerolab.recruitee.com/o/fe",
         "location": "Buenos Aires, Argentina", "remote": True, "published_at": "2026-10-15 09:31:36 UTC",
         "description": "<p>React</p>", "requirements": "<p>3 years</p>"}]}})
    [j] = ats_more.Recruitee({"boards": ["aerolab"]}).fetch()
    assert j.location == "Buenos Aires, Argentina (Remote)" and j.posted_at.year == 2026
    assert "React" in j.description and "3 years" in j.description


def test_a_429_stops_the_run_but_keeps_what_was_read(monkeypatch):
    def get(source, kind, url, **k):
        if "second" in url:
            raise http.Throttled("429")
        return Resp(TT_RSS)
    monkeypatch.setattr(http, "get", get)
    jobs = ats_more.Teamtailor({"boards": ["first", "second", "third"]}).fetch()
    assert len(jobs) == 1


def test_remoteok_skips_the_terms_and_fixes_encoding(monkeypatch):
    serve(monkeypatch, {"remoteok.com/api": [
        {"legal": "API Terms of Service"},
        {"id": "1137523", "position": "Sr React Native", "company": "Somnio Software",
         "url": "https://remoteOK.com/remote-jobs/1137523", "date": "2026-10-15T00:00:19+00:00",
         "description": "<p>QuÃ© es Deazy</p>", "location": "LATAM", "salary_min": 60000, "salary_max": 90000,
         "tags": ["react native"]}]})
    [j] = boards.RemoteOK({}).fetch()
    assert j.description == "Qué es Deazy" and j.salary == "USD 60,000–90,000/year" and j.location == "LATAM"


def test_workday_searches_and_reads_only_new_in_scope_postings(monkeypatch):
    from vigia import db
    from vigia.sources import workday
    listing = {"total": 3, "jobPostings": [
        {"title": "Senior Software Engineer", "externalPath": "/job/Remote-Latin-America/SSE_JR1",
         "locationsText": "Remote, Latin America", "postedOn": "Posted 3 Days Ago"},
        {"title": "Accountant", "externalPath": "/job/Quito/Accountant_JR2", "locationsText": "Quito"},
        {"title": "Backend Developer", "externalPath": "/job/Montevideo/BD_JR3", "locationsText": "Montevideo, Uruguay",
         "postedOn": "Posted Today"}]}
    detail = {"jobPostingInfo": {"jobDescription": "<p>Python and LLM agents</p>", "location": "Remote, Latin America",
                                 "startDate": "2026-10-12", "externalUrl": "https://stord.wd503.myworkdayjobs.com/x/job/SSE"},
              "hiringOrganization": {"name": "Stord"}}
    posts, gets = [], []
    monkeypatch.setattr(http, "post_json", lambda s, k, url, payload: posts.append(payload["searchText"]) or listing)
    monkeypatch.setattr(http, "get_json", lambda s, k, url, **kw: gets.append(url) or detail)
    db.conn().execute("INSERT INTO jobs(dedupe_key, source, source_id, title, company, url, first_seen, last_seen) "
                      "VALUES ('k', 'workday', 'stord/job/Montevideo/BD_JR3', 'Backend Developer', 'Stord', 'u', 'x', 'x')")
    jobs = workday.Workday({"boards": ["stord/wd503/Stord_External_Career"], "queries": ["engineer", "python"]}).fetch()
    assert posts == ["engineer", "python"]
    assert sorted(j.title for j in jobs) == ["Backend Developer", "Senior Software Engineer"]  # no Accountant
    assert len(gets) == 1 and gets[0].endswith("/wday/cxs/stord/Stord_External_Career/job/Remote-Latin-America/SSE_JR1")
    assert next(j for j in jobs if j.title == "Backend Developer").company == "Stord"  # the stored name
    sse = next(j for j in jobs if j.title == "Senior Software Engineer")
    assert (sse.company, sse.remote, sse.description) == ("Stord", True, "Python and LLM agents")
    assert sse.posted_at.date().isoformat() == "2026-10-12"


def test_workday_posted_on():
    from vigia.sources.workday import posted_on
    assert posted_on("Posted Today") and posted_on("Posted Yesterday")
    assert (posted_on("Posted 30+ Days Ago") < posted_on("Posted 3 Days Ago"))
    assert posted_on("") is None

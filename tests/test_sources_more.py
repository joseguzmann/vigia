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
              "hiringOrganization": {"name": "583000 Stord"}}
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


CT_CARD = """<article class="box_offer sel " data-id='2058CFB32A1C25CC61373E686DCF3405' data-blind="false">
 <h2 class="fs18 fwB prB"> <a class="js-o-link fc_base" href="/ofertas-de-trabajo/oferta-de-trabajo-de-desarrollador-de-ia-en-quito-2058CFB32A1C25CC61373E686DCF3405#lc=ListOffers-Score4-0"> Desarrollador de IA </a></h2>
 <p class="dFlex vm_fx fs16 fc_base mt5"><a class="fc_base t_ellipsis" href="https://ec.computrabajo.com/empresas/x" target='_blank' offer-grid-article-company-url> i-Route Solutions </a></p>
 <p class="fs16 fc_base mt5"> <span class="mr10"> Quito, Pichincha </span> </p>
 <div><span>2.300,00 US$ (Mensual)</span></div><div><span>Presencial y remoto</span></div>
 <p class="fs13 fc_aux mt15"> Hace 23 horas </p></article>"""


def test_computrabajo_card_and_page_fallback(monkeypatch):
    from vigia.sources import ecuador
    page = ("<html><h3>Descripción de la oferta</h3><p>Agentes con LangChain.</p><h3>Requerimientos</h3>"
            "<p>3 años de experiencia</p><div>Aptitudes asociadas a esta oferta</div><p>Aws</p></html>")
    serve(monkeypatch, {"trabajo-de-python": CT_CARD.encode(), "oferta-de-trabajo-de-desarrollador-de-ia": page.encode()})
    [j] = ecuador.Computrabajo({"queries": ["python"], "pages": 1}).fetch()
    assert (j.title, j.company, j.salary) == ("Desarrollador de IA", "i-Route Solutions", "2.300,00 US$ (Mensual)")
    assert j.location == "Quito, Pichincha, Ecuador · hybrid" and j.remote is False
    assert j.url.startswith("https://ec.computrabajo.com/ofertas-de-trabajo/") and "#" not in j.url
    assert "LangChain" in j.description and "3 años" in j.description and "Aws" not in j.description


def test_multitrabajos_whole_it_area(monkeypatch):
    from vigia.sources import ecuador
    asked = []
    monkeypatch.setattr(http, "get", lambda *a, **k: asked.append(a[2]) or Resp(b"<html></html>"))
    def post(source, kind, url, payload, headers=None):
        asked.append((url, payload["filtros"][0]["value"], headers["x-site-id"]))
        return {"content": [
            {"id": 1118461355, "titulo": "Desarrollador Fullstack (Python)", "detalle": "<p>Python y FastAPI</p>",
             "empresa": "Confidencial", "confidencial": True, "fechaHoraPublicacion": "29-09-2026 16:39:24",
             "localizacion": "Quito, Pichincha", "modalidadTrabajo": "Presencial"},
            {"id": 2, "titulo": "QA Pruebas de Rendimiento", "detalle": "k6", "empresa": "Acme", "confidencial": False,
             "fechaPublicacion": "10-10-2026", "localizacion": "Guayaquil, Guayas", "modalidadTrabajo": "Remoto"}]}
    monkeypatch.setattr(http, "post_json", post)
    jobs = ecuador.Multitrabajos({}).fetch()
    assert asked[0].endswith("tecnologia-sistemas-y-telecomunicaciones.html")      # session cookies first
    assert asked[1][1:] == ("tecnologia-sistemas-y-telecomunicaciones", "BMEC")
    a, b = jobs
    assert (a.company, a.location, a.url) == ("Confidencial (Multitrabajos)", "Quito, Pichincha, Ecuador · presencial",
                                              "https://www.multitrabajos.com/empleos/1118461355.html")
    assert a.posted_at.isoformat() == "2026-09-29T16:39:24-05:00" and a.description == "Python y FastAPI"
    assert (b.location, b.remote) == ("Guayaquil, Guayas, Ecuador (Remote)", True)


def test_ecuador_regions(profile):
    from vigia.flags import region
    from vigia.models import Job
    profile = {**profile, "home_country": "Ecuador", "home_cities": ["Quito"]}
    def at(loc):
        return region(Job(source="multitrabajos", source_id=loc, title="Desarrollador", company="X", url="u",
                          location=loc, description="Python"), profile)[0]
    assert at("Quito, Pichincha, Ecuador · presencial") == "ok"
    assert at("Quito, Pichincha, Ecuador · hybrid") == "ok"
    assert at("Guayaquil, Guayas, Ecuador (Remote)") == "ok"
    assert at("Guayaquil, Guayas, Ecuador · presencial") == "restricted"


def test_indeed_work_mode_from_attributes_or_text(monkeypatch):
    from vigia.sources import indeed
    def job(key, title, city, attrs=(), desc=""):
        return {"job": {"key": key, "title": title, "dateOnIndeed": 1792000000000, "employer": {"name": "Acme"},
                        "description": {"html": f"<p>{desc}</p>"}, "location": {"countryCode": "EC", "city": city},
                        "attributes": [{"key": a} for a in attrs]}}
    data = {"data": {"jobSearch": {"results": [
        job("a", "Desarrollador Full Stack", "Guayaquil"),                                  # nothing said: on-site
        job("b", "Python Developer", "Quito", attrs=[indeed.REMOTE]),                       # Indeed says remote
        job("c", "Junior Developer (Remote- LATAM ONLY)", "Desde casa"),                    # remote in title / city
        job("d", "Frontend Developer", "Quito", desc="Modalidad híbrida, 3 días en oficina"),
    ]}}}
    seen = []
    monkeypatch.setattr(http, "post_json", lambda s, k, url, payload, headers=None: seen.append(headers["indeed-co"]) or data)
    jobs = {j.source_id: j for j in indeed.Indeed({"countries": [{"code": "EC", "queries": ["python"]}]}).fetch()}
    assert seen == ["EC"]
    assert jobs["a"].location == "Guayaquil, Ecuador · presencial"
    assert (jobs["b"].location, jobs["b"].remote) == ("Quito, Ecuador (Remote)", True)
    assert jobs["c"].location == "Ecuador (Remote)"
    assert jobs["d"].location == "Quito, Ecuador · hybrid"
    assert jobs["a"].url == "https://ec.indeed.com/viewjob?jk=a" and jobs["a"].posted_at.year == 2026


def test_torre_from_sitemaps_skips_out_of_scope_slugs(monkeypatch):
    from datetime import datetime, timezone
    from vigia.sources import torre
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    index = b"<sitemapindex><sitemap><loc>https://torre.ai/sitemap-en-1655.xml</loc></sitemap>" \
            b"<sitemap><loc>https://torre.ai/sitemap-en-1656.xml</loc></sitemap></sitemapindex>"
    smap = (f"<urlset><url><loc>https://torre.ai/post/AbC123-niuro-senior-ai-engineer-fintech</loc><lastmod>{today}</lastmod></url>"
            f"<url><loc>https://torre.ai/post/ZzZ999-acme-sales-manager</loc><lastmod>{today}</lastmod></url>"
            f"<url><loc>https://torre.ai/post/Old111-acme-python-developer</loc><lastmod>2020-01-01T00:00:00Z</lastmod></url></urlset>").encode()
    page = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Senior AI Engineer, Fintech",'
            '"description": "<p>LLM agents</p>", "datePosted": "2026-10-08T10:00:00Z", "jobLocationType": "TELECOMMUTE",'
            '"hiringOrganization": {"name": "Niuro"}, "applicantLocationRequirements": ['
            '{"@type": "Country", "name": "Argentina"}, {"@type": "Country", "name": "Ecuador"}]}</script>').encode()
    asked = serve(monkeypatch, {"sitemap-index.xml": index, "sitemap-en-1656.xml": smap, "sitemap-en-1655.xml": b"<urlset/>",
                                "post/AbC123": page})
    [j] = torre.Torre({"sitemaps_en": 2, "sitemaps_es": 0}).fetch()
    assert not any("ZzZ999" in u or "Old111" in u for u in asked)          # never fetched
    assert (j.source_id, j.company, j.remote) == ("AbC123", "Niuro", True)
    assert j.tags == ["countries:Argentina, Ecuador"] and j.location == "Remote: Argentina, Ecuador"


def test_remoteyeah_reads_cards_and_job_pages(monkeypatch):
    from vigia import http
    from vigia.sources import remoteyeah
    listing = ('<a href="https://remoteyeah.com/jobs/remote-ai-engineer-acme" class="job-card-title">AI Engineer</a>'
               '<a href="https://remoteyeah.com/jobs/remote-sales-manager-acme" class="job-card-title">Sales Manager</a>')
    job = ('<script type="application/ld+json">{"@type":"JobPosting","title":"AI Engineer",'
           '"hiringOrganization":{"name":"Acme"},"jobLocationType":"TELECOMMUTE","datePosted":"2026-10-20",'
           '"description":"<p>Python and LLMs</p>","applicantLocationRequirements":[{"@type":"Country","name":"Ecuador"}]}</script>')

    class Page:
        def __init__(self, text):
            self.text = text

    asked = []
    monkeypatch.setattr(http, "get", lambda src, kind, url, **k: asked.append(kind) or Page(listing if kind == "listing" else job))
    monkeypatch.setattr(remoteyeah.config, "profile", lambda: {"title_exclude": []})
    monkeypatch.setattr(remoteyeah, "title_in_scope", lambda t, p: ("Engineer" in t, ""))
    monkeypatch.setattr(remoteyeah.db, "known_source_ids", lambda s, ids: set())
    jobs = remoteyeah.RemoteYeah({"listings": ["remote-jobs-in-latin-america"]}).fetch()
    assert asked == ["listing", "job"]  # the out-of-scope title is never opened
    assert len(jobs) == 1 and jobs[0].company == "Acme" and "Ecuador" in jobs[0].location


def test_discover_takes_slugs_from_ats_urls():
    from vigia.sources.discover import slug_from
    assert slug_from("ashby", "https://jobs.ashbyhq.com/Acme/aa81-42") == "acme"
    assert slug_from("greenhouse", "https://job-boards.greenhouse.io/acme/jobs/123") == "acme"
    assert slug_from("greenhouse", "https://boards.greenhouse.io/embed/job_app?for=x") is None
    assert slug_from("lever", "https://jobs.lever.co/acme/e051") == "acme"
    assert slug_from("workable", "https://apply.workable.com/acme/j/ABC/") == "acme"
    assert slug_from("teamtailor", "https://acme.na.teamtailor.com/jobs/1-ai-engineer") == "acme.na"


def test_discover_watches_only_boards_with_jobs_in_scope(monkeypatch):
    from vigia import db, flags
    from vigia.models import Job
    from vigia.sources import discover
    monkeypatch.setenv("SERPER_API_KEY", "k")
    src = discover.Discover({"ats": ["ashby"], "queries_per_run": 1})
    monkeypatch.setattr(src, "search", lambda key, q: [
        {"link": "https://jobs.ashbyhq.com/good/1", "title": "AI Engineer"},
        {"link": "https://jobs.ashbyhq.com/bad/2", "title": "Sales"},
        {"link": "https://jobs.ashbyhq.com/known/3", "title": "AI Engineer"}])
    monkeypatch.setattr(discover.config, "source", lambda n: {"boards": ["known"]})
    monkeypatch.setattr(discover.config, "profile", lambda: {})
    job = lambda slug: Job(source="ashby", source_id=slug, title=slug, company=slug, url="https://x")
    read = []
    monkeypatch.setattr(src, "read_board", lambda ats, slug: read.append(slug) or [job(slug)])
    monkeypatch.setattr(discover.flags, "evaluate", lambda j, p: ("fit" if j.title == "good" else "out", 0, {}))
    added = []
    monkeypatch.setattr(discover.db, "add_discovered", lambda *a: added.append((a[1], a[2])))
    monkeypatch.setattr(discover.db, "discovered_slugs", lambda ats, status="watching": [])
    monkeypatch.setattr(discover.db, "get_state", lambda k, d=None: "0")
    monkeypatch.setattr(discover.db, "set_state", lambda k, v: None)
    jobs = src.fetch()
    assert read == ["good", "bad"]  # the board already in the config is not read again
    assert added == [("good", "watching"), ("bad", "rejected")] and [j.title for j in jobs] == ["good"]


def test_ats_sources_read_discovered_boards(monkeypatch):
    from vigia import db
    from vigia.sources.base import Source
    monkeypatch.setattr(db, "discovered_slugs", lambda ats, status="watching": ["found", "acme"])
    s = Source({"boards": ["acme"]})
    assert s.boards() == ["acme", "found"]
    assert Source({"boards": ["acme"], "include_discovered": False}).boards() == ["acme"]


SESSION_TEXT = ("Feed post\n\nAcme Labs\n\n1d\n\nFollow\n\nWe're hiring: AI Engineer\n\nRemote - LATAM. Python and FastAPI.\n\n4\n​\n4 reactions\n"
                "Feed post\n\nAna Pérez\n\n• 3rd+\n\nRecruiter\n\n2h\n\nFollow\n\nI'm open to work as a backend engineer #OpenToWork\n\n1 reaction\n"
                "\nAre these results helpful?\n\nYour feedback helps us improve search results")


def test_linkedin_session_splits_posts_and_links():
    from vigia.sources.linkedin_session import post_urls, split_posts
    posts = split_posts(SESSION_TEXT)
    assert [p["author"] for p in posts] == ["Acme Labs", "Ana Pérez"]
    assert posts[0]["text"].startswith("We're hiring: AI Engineer") and "reactions" not in posts[0]["text"]
    refs = [{"kind": "feed_post", "url": "/posts/acme_hiring-share-7516542601848848385-cgQ5"},
            {"kind": "feed_post", "url": "/feed/update/urn:li:activity:7518250073844064256/"},
            {"kind": "person", "url": "/in/ana/"},
            {"kind": "feed_post", "url": "/posts/ana_open-activity-7517208697547288576-BvQA"}]
    assert [i for _, i in post_urls(refs)] == ["7516542601848848385", "7517208697547288576"]


def test_linkedin_session_keeps_hiring_posts_and_pauses_on_a_check(monkeypatch):
    import json, pytest
    from vigia import db
    from vigia.sources import linkedin_session as ls
    state = {}
    monkeypatch.setattr(ls.db, "get_state", lambda k, d=None: state.get(k, d))
    monkeypatch.setattr(ls.db, "set_state", lambda k, v: state.__setitem__(k, v))
    monkeypatch.setattr(ls.db, "log_request", lambda *a: None)
    src = ls.LinkedInSession({"phrases": ["FastAPI"]})
    result = {"sections": {"search_results": SESSION_TEXT}, "references": {"search_results": []}}
    monkeypatch.setattr(src, "call", lambda q: _done((json.dumps(result), False)))
    jobs = src.fetch()
    assert [j.company for j in jobs] == ["Acme Labs"]
    monkeypatch.setattr(src, "call", lambda q: _done(("LinkedIn showed a security verification (captcha)", True)))
    with pytest.raises(RuntimeError, match="paused"):
        src.fetch()
    assert state["linkedin_session.paused"]
    with pytest.raises(RuntimeError, match="paused"):  # stays paused until cleared by hand
        src.fetch()


async def _done(value):
    return value


def test_linkedin_session_paces_like_a_person(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from vigia.sources import linkedin_session as ls
    state = {}
    monkeypatch.setattr(ls.db, "get_state", lambda k, d=None: state.get(k, d))
    monkeypatch.setattr(ls.db, "set_state", lambda k, v: state.__setitem__(k, v))
    tz = ZoneInfo("America/Guayaquil")
    src = ls.LinkedInSession({"per_day": 2, "hours_local": [9, 19], "min_gap_minutes": 90})
    wed = datetime(2026, 10, 21, 8, 0, tzinfo=tz)
    slots = src.slots(wed)
    assert len(slots) == 2 and slots[1] - slots[0] >= ls.timedelta(minutes=90)
    assert all(9 <= s.hour < 19 for s in slots) and src.slots(wed) == slots  # drawn once a day
    assert not src.due_at(wed)
    assert src.due_at(slots[0]) and not src.due_at(slots[0] - ls.timedelta(minutes=1))
    assert src.slots(datetime(2026, 10, 24, 10, 0, tzinfo=tz)) == []  # Saturday
    state["linkedin_session.paused"] = "captcha"
    assert not src.due_at(slots[1])
    many = ls.LinkedInSession({"per_day": 12, "hours_local": [9, 19], "min_gap_minutes": 40}).slots(
        datetime(2026, 10, 22, 8, 0, tzinfo=tz))
    assert len(many) == 12 and all(b - a >= ls.timedelta(minutes=40) for a, b in zip(many, many[1:]))
    assert many[0].hour >= 9 and many[-1].hour < 19


def test_linkedin_session_reads_group_posts_without_follow():
    from vigia.sources.linkedin_session import split_posts
    text = ("Feed post\n\nAI Tools & Productivity\n\nMichelle Landa • 3rd+\n\nRecruiter at Niuro\n\n5h\n\n"
            "#WeAreHiring Senior AI Engineer, remote LATAM\n\n3 reactions\n")
    [p] = split_posts(text)
    assert p["author"] == "Michelle Landa" and p["text"].startswith("#WeAreHiring")
    [p] = split_posts(text.replace("5h\n\n", "5h\n\nJoin\n\n"))
    assert p["text"].startswith("#WeAreHiring")


def test_openroles_maps_rows_to_boards_and_keeps_open_remote_roles():
    from vigia.sources.openroles import OpenRoles, board_of
    assert board_of({"a": "ashby", "t": "Acme", "u": "https://jobs.ashbyhq.com/Acme/1"}) == ("ashby", "acme")
    assert board_of({"a": "workday", "t": "x", "u": "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Remote/AI_123"}) == ("workday", "acme/wd5/Careers")
    assert board_of({"a": "bamboohr", "t": "acme", "u": "https://acme.bamboohr.com/careers/32"}) == ("bamboohr", "acme")
    assert board_of({"a": "personio", "t": "acme", "u": "https://acme.jobs.personio.de/job/32"}) is None
    src, prof = OpenRoles({}), {"title_include": ["AI Engineer"], "title_exclude": []}
    row = {"ti": "AI Engineer", "w": "remote", "cc": None, "loc": "Remote", "l": None}
    assert src.wanted(row, prof, "EC")
    assert not src.wanted({**row, "cc": "US"}, prof, "EC")
    assert src.wanted({**row, "cc": "CO", "loc": "LATAM | Colombia"}, prof, "EC")
    assert not src.wanted({**row, "w": "onsite"}, prof, "EC")
    assert not src.wanted({**row, "l": "senior"}, prof, "EC")


def test_notify_caps_messages_and_respects_quiet_hours(monkeypatch):
    from datetime import datetime
    from vigia import notify
    from vigia.models import Job
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example/vigia")
    monkeypatch.setenv("NTFY_TOKEN", "t")
    monkeypatch.setattr(notify.config, "load", lambda: {"notify": {"quiet_hours": [22, 8], "max_per_run": 2}})
    sent = []
    monkeypatch.setattr(notify, "send", lambda title, body, url="", tags="": sent.append(title) or True)
    monkeypatch.setattr(notify, "quiet", lambda now=None: False)
    jobs = [(Job(source="s", source_id=str(i), title=f"AI Engineer {i}", company="Acme", url="https://x"), i) for i in range(4)]
    assert notify.new_fits("ashby", jobs) == 3
    assert sent == ["New fit: AI Engineer 3", "New fit: AI Engineer 2", "+2 more new fits"]
    monkeypatch.undo()
    monkeypatch.setattr(notify.config, "load", lambda: {"notify": {"quiet_hours": [22, 8]}})
    assert notify.quiet(datetime(2026, 10, 21, 23)) and notify.quiet(datetime(2026, 10, 22, 7))
    assert not notify.quiet(datetime(2026, 10, 22, 9))


def test_notify_needs_url_and_token(monkeypatch):
    from vigia import notify
    monkeypatch.delenv("NTFY_URL", raising=False)
    assert notify.send("t", "b") is False


def test_openroles_stops_at_files_without_dates(monkeypatch):
    from vigia.sources import openroles
    manifest = {"slim_index_chunks": [{"file": "a", "posted_max": None}]}
    monkeypatch.setattr(openroles.http, "get_json", lambda *a, **k: manifest)
    monkeypatch.setattr(openroles.http, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    assert list(openroles.OpenRoles({}).rows()) == []


def test_a_429_on_the_only_board_is_not_taken_as_no_jobs(monkeypatch):
    def get(source, kind, url, **k):
        raise http.Throttled("429")
    monkeypatch.setattr(http, "get", get)
    try:
        ats_more.Teamtailor({"boards": ["only"]}).fetch()
    except http.Throttled:
        pass
    else:
        raise AssertionError("a board never read must not look empty")


def test_a_throttled_board_is_left_unchecked(monkeypatch):
    from vigia.sources import discover
    added = []
    monkeypatch.setattr(discover.db, "add_discovered", lambda *a: added.append(a))
    def reader(ats, slug):
        raise http.Throttled("429")
    try:
        discover.check_board("workday", "acme/wd1/jobs", "openroles", reader=reader)
    except http.Throttled:
        pass
    assert added == []  # not "rejected": it is checked again later


def test_openroles_keeps_what_it_found_when_pages_are_throttled(monkeypatch):
    from vigia.sources import openroles
    rows = [{"i": "a", "a": "ashby", "t": "good", "ti": "AI Engineer"},
            {"i": "b", "a": "personio", "t": "x", "ti": "AI Engineer", "u": "https://x/1"}]
    src = openroles.OpenRoles({})
    monkeypatch.setattr(src, "rows", lambda: iter(rows))
    monkeypatch.setattr(src, "wanted", lambda r, p, h: True)
    monkeypatch.setattr(openroles.config, "profile", lambda: {})
    monkeypatch.setattr(openroles, "is_known", lambda ats, slug: False)
    monkeypatch.setattr(openroles, "check_board", lambda *a, **k: ["job from board"])
    monkeypatch.setattr(openroles.db, "known_source_ids", lambda s, ids: set())
    def get(*a, **k):
        raise http.Throttled("429")
    monkeypatch.setattr(openroles.http, "get", get)
    assert src.fetch() == ["job from board"]


def test_openroles_rechecks_rejected_companies_that_post_again(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from vigia.sources import openroles
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    rows = {("ashby", "stale"): {"status": "rejected", "found_at": old, "query": "dataset"},
            ("ashby", "recent"): {"status": "rejected", "found_at": new, "query": "dataset"},
            ("ashby", "watched"): {"status": "watching", "found_at": old, "query": "dataset"}}
    src = openroles.OpenRoles({})
    monkeypatch.setattr(src, "rows", lambda: iter([{"i": s, "a": "ashby", "t": s, "ti": "AI Engineer"}
                                                    for s in ("stale", "recent", "watched", "brand-new")]))
    monkeypatch.setattr(src, "wanted", lambda r, p, h: True)
    monkeypatch.setattr(openroles.config, "profile", lambda: {})
    monkeypatch.setattr(openroles, "is_known", lambda ats, slug: (ats, slug) in rows)
    monkeypatch.setattr(openroles.db, "discovered_board", lambda ats, slug: rows.get((ats, slug)))
    monkeypatch.setattr(openroles.db, "known_source_ids", lambda s, ids: set())
    checked = []
    monkeypatch.setattr(openroles, "check_board", lambda ats, slug, origin, title: checked.append((slug, origin)) or [])
    src.fetch()
    assert checked == [("stale", "dataset"), ("brand-new", "openroles")]


def test_bamboohr_reads_details_only_for_in_scope_titles(monkeypatch):
    lst = {"result": [{"id": "32", "jobOpeningName": "AI Software Engineer", "locationType": "1",
                       "atsLocation": {"country": "Colombia"}},
                      {"id": "33", "jobOpeningName": "Account Executive", "locationType": "1"},
                      {"id": "34", "jobOpeningName": "Backend Engineer", "locationType": "0",
                       "atsLocation": {"city": "Bogotá", "country": "Colombia"}}]}
    detail = {"32": {"jobOpeningShareUrl": "https://acme.bamboohr.com/careers/32", "locationType": "1",
                     "description": "<p>Remote, Latam | 4+ years</p>", "datePosted": "2026-10-20"},
              "34": {"locationType": "0", "description": "<p>On-site</p>", "datePosted": "2026-10-01"}}
    asked = []
    def get_json(src, kind, url, **k):
        asked.append(url)
        if url.endswith("/list"):
            return lst
        return {"result": {"jobOpening": detail[url.split("/")[-2]]}}
    monkeypatch.setattr(http, "get_json", get_json)
    monkeypatch.setattr(ats_more, "_in_scope", lambda t: "Engineer" in t)
    jobs = ats_more.BambooHR({"boards": ["acme"]}).fetch()
    assert [j.source_id for j in jobs] == ["acme/32", "acme/34"]
    assert not any("/33/" in u for u in asked)  # out-of-scope titles cost no extra request
    assert jobs[0].location == "Remote" and jobs[0].remote and "Remote, Latam" in jobs[0].description
    assert jobs[1].location == "Bogotá, Colombia" and jobs[1].remote is False


def test_rippling_and_manatal(monkeypatch):
    def get_json(src, kind, url, **k):
        if "rippling" in url and url.endswith("/jobs"):
            return [{"uuid": "u1", "name": "Backend Engineer", "url": "https://ats.rippling.com/acme/jobs/u1",
                     "workLocation": {"label": "Remote (Brazil)"}},
                    {"uuid": "u2", "name": "Barista", "workLocation": {"label": "NYC"}}]
        if "rippling" in url:
            assert url.endswith("/u1")
            return {"description": {"company": "<p>About</p>", "role": "<p>Python</p>"},
                    "workLocations": ["Remote (Brazil)"], "createdOn": "2026-10-09T10:59:35-07:00"}
        if "page=2" in url:
            return {"results": [{"id": 2, "hash": "H2", "position_name": "AI Engineer", "description": "<p>LLM</p>",
                                 "location_display": "", "is_remote": True}], "next": None}
        return {"results": [{"id": 1, "hash": "H1", "position_name": "Sales Rep", "description": ""}],
                "next": "https://core.api.manatal.com/open/v3/career-page/agency/jobs/?page=2"}
    monkeypatch.setattr(http, "get_json", get_json)
    monkeypatch.setattr(ats_more, "_in_scope", lambda t: "Engineer" in t)
    r = ats_more.Rippling({"boards": ["acme"]}).fetch()
    assert [(j.title, j.location, j.remote) for j in r] == [("Backend Engineer", "Remote (Brazil)", True)]
    assert "Python" in r[0].description and r[0].posted_at.year == 2026
    m = ats_more.Manatal({"boards": ["agency"]}).fetch()
    assert [(j.title, j.url) for j in m] == [("AI Engineer", "https://www.careers-page.com/agency/job/H2")]


def test_hot_pass_reads_only_boards_that_posted_lately(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from vigia import config, db, pipeline
    from vigia.models import Job
    from vigia.sources.base import board_key
    assert board_key("Acme") == "acme" and board_key("acme/wd5/Careers") == "acme.wd5"
    job = Job(source="ashby", source_id="1", title="AI Engineer", company="Hot", url="https://jobs.ashbyhq.com/hotco/1")
    db.upsert_job(job, "fit", 5, {})
    old = Job(source="ashby", source_id="2", title="Backend Engineer", company="Old", url="https://jobs.ashbyhq.com/oldco/2")
    db.upsert_job(old, "fit", 5, {})
    db.conn().execute("UPDATE jobs SET first_seen = '2026-01-01T00:00:00+00:00' WHERE source_id = '2'")
    from vigia.sources import REGISTRY
    src = REGISTRY["ashby"]({"boards": ["hotco", "oldco", "quietco"], "include_discovered": False, "hot_only": True})
    assert src.boards() == ["hotco"]

    cfg = dict(config.load()); cfg["hot_boards"] = {"sources": ["ashby"], "every_minutes": 20}
    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(config, "source", lambda n: {"enabled": True, "boards": ["hotco"]})
    assert pipeline.hot_due("ashby") and not pipeline.hot_due("lever")
    ran = []
    monkeypatch.setattr(pipeline, "run_source", lambda name, src=None: ran.append((name, src.cfg["hot_only"])) or {})
    pipeline.run_hot("ashby")
    assert ran == [("ashby:hot", True)]
    db.start_run("ashby:hot")
    assert not pipeline.hot_due("ashby")
    assert not pipeline.hot_due("ashby", datetime.now(timezone.utc) + timedelta(minutes=5))
    assert pipeline.hot_due("ashby", datetime.now(timezone.utc) + timedelta(minutes=21))


def test_linkedin_session_reads_ugcpost_links_and_dates():
    from vigia.sources import linkedin_session as ls
    refs = [{"kind": "company", "url": "/company/acme/"},
            {"kind": "feed_post", "url": "/posts/ai-architect-opportunity-ugcPost-7516839569812705281-3x-K"},
            {"kind": "feed_post", "url": "/posts/ana_hiring-share-7516556867670667264-yDK_"},
            {"kind": "feed_post", "url": "/feed/update/urn:li:ugcPost:7516839569812705281/"}]
    assert [i for _, i in ls.post_urls(refs)] == ["7516839569812705281", "7516556867670667264"]
    assert ls.posted_from_id("7516839569812705281").isoformat().startswith("2026-10-16T12:36")


def test_recruitee_uses_the_candidate_locations(monkeypatch, profile):
    from vigia.flags import region
    offer = {"id": 1, "title": "AI Engineer", "careers_url": "https://jobs.clanx.ai/o/ai-engineer-remote",
             "location": "Remote job", "country": "India", "remote": True, "description": "<p>Remote team.</p>",
             "locations": [{"name": "India - Remote", "country": "India",
                            "translations": {"en": {"note": "Applicable to candidates based in India"}}}]}
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"offers": [offer]})
    job = ats_more.Recruitee({"boards": ["clanx"]}).fetch()[0]
    assert job.location == "India - Remote" and "candidates based in India" in job.description
    assert region(job, profile)[0] == "restricted"
    hubs = dict(offer, location="Remote job", locations=[{"name": "Switzerland, Lugano"}, {"name": "UAE, Dubai"}])
    monkeypatch.setattr(http, "get_json", lambda *a, **k: {"offers": [hubs]})
    job = ats_more.Recruitee({"boards": ["acme"]}).fetch()[0]
    assert job.location == "Remote job" and "Listed locations: Switzerland, Lugano; UAE, Dubai" in job.description


def test_linkedin_session_author_is_never_the_age_or_a_button():
    from vigia.sources.linkedin_session import post_author
    assert post_author(["3d •", "Acme Talent", "Recruiting partner"]) == "Acme Talent"
    assert post_author(["Python Devs LATAM", "Join", "Ana Pérez • 2nd", "Recruiter", "3d •"]) == "Ana Pérez"
    assert post_author(["Ana Pérez", "• 3rd+", "Recruiter", "2h"]) == "Ana Pérez"
    assert post_author(["3d •"]) == "LinkedIn post"


def test_linkedin_session_drops_the_page_footer():
    from vigia.sources.linkedin_session import split_posts
    text = ("Feed post\n\nHire Maven\n\n1w\n\nFollow\n\n#Hiring AI Engineer LATAM\n\nSend your CV\n… more\n5\n\u200b\n"
            "Are these results helpful?\nYour feedback helps us improve search results\nLoad more")
    [p] = split_posts(text)
    assert p["text"] == "#Hiring AI Engineer LATAM\nSend your CV"


def test_linkedin_session_matches_links_by_author_when_counts_differ():
    from vigia.sources.linkedin_session import match_urls
    posts = [{"author": "Hire South"}, {"author": "Michelle Landa"}, {"author": "Pablo Galván Leyes"}, {"author": "X"}]
    urls = [("https://www.linkedin.com/posts/pablo-galvan-leyes_activity-2", "2"),
            ("https://www.linkedin.com/posts/michelle-landa_activity-1", "1"),
            ("https://www.linkedin.com/posts/hiring-aiengineer-share-3", "3")]
    got = match_urls(posts, urls)
    assert got[1][1] == "1" and got[2][1] == "2"
    assert got[0] == ("", "") and got[3] == ("", "")  # two left, one link: no guessing
    assert [i for _, i in match_urls(posts[:2] + [{"author": "Y"}], urls)] == ["2", "1", "3"]


def test_freehire_maps_eligibility_and_pages(monkeypatch):
    from vigia.sources import freehire
    def row(i, **kw):
        return {"public_slug": f"job-{i}", "source": "lever", "url": f"https://jobs.lever.co/acme/{i}?utm_source=freehire.me",
                "title": "Backend Engineer", "company": "Acme", "location": "Remote", "countries": [], "regions": [],
                "work_mode": "remote", "posted_at": "2026-10-23T13:58:04Z", "description": "Python. " * 30,
                "enrichment": {"salary_min": 3000, "salary_max": 4000, "salary_currency": "USD", "salary_period": "month"},
                "reality": {"class": "fresh", "repost_count": 3}, **kw}
    pages = {0: [row(i) for i in range(100)], 100: [row(100, countries=["co", "ec"], location="Remote, Colombia"),
                                                   row(101, regions=["global"])]}
    calls = []
    def get_json(source, kind, url, params=None):
        calls.append(params)
        return {"data": pages.get(params["offset"], [])} if params.get("countries") == "ec" else {"data": []}
    monkeypatch.setattr(freehire.http, "get_json", get_json)
    jobs = {j.source_id: j for j in freehire.FreeHire({}).fetch()}
    assert len(jobs) == 102 and [c["offset"] for c in calls if c.get("countries")] == [0, 100]
    assert jobs["job-100"].location == "Remote, Colombia (eligible: Ecuador)"
    assert jobs["job-101"].location == "Remote (Worldwide)"
    assert jobs["job-0"].url == "https://jobs.lever.co/acme/0" and jobs["job-0"].salary == "USD 3,000–4,000/month"
    assert "reposted:3" in jobs["job-0"].tags and all(c["open_within_days"] == 2 for c in calls)

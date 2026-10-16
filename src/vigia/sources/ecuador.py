"""Ecuador's local job boards: Multitrabajos (Bumeran network) and Computrabajo.

The local market: companies in Quito and Guayaquil, on-site, hybrid or remote. Both boards
say the work mode, which is written into `location` so the region rules can use it:
"Quito, Pichincha, Ecuador (Remote)", "... · hybrid", "... · presencial". On-site or hybrid
only passes in the profile's `home_cities`.

Multitrabajos: the search API behind its web app answers once the session holds the cookies
of a normal page load (sent with Origin/Referer, as the browser does). One search over the
Technology area returns every IT posting in the country, with its full text.

Computrabajo: server-rendered search pages (20 cards each); the full text comes from each
posting's schema.org JobPosting, fetched only for titles in scope that are not stored yet.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from vigia import config, db, flags, http
from vigia.models import Job
from vigia.sources.base import Source, job_posting_ld, parse_date, strip_html

MT = "https://www.multitrabajos.com"
CT = "https://ec.computrabajo.com"
MODE = {"remoto": " (Remote)", "híbrido": " · hybrid", "hibrido": " · hybrid",
        "presencial y remoto": " · hybrid", "presencial": " · presencial"}


def _place(city: str, mode: str) -> str:
    city = (city or "").strip()
    where = f"{city}, Ecuador" if city and "ecuador" not in city.lower() else (city or "Ecuador")
    return where + MODE.get((mode or "").strip().lower(), "")


class Multitrabajos(Source):
    name = "multitrabajos"

    def fetch(self) -> list[Job]:
        http.get(self.name, "session", f"{MT}/empleos-area-tecnologia-sistemas-y-telecomunicaciones.html")
        headers = {"x-site-id": self.cfg.get("site_id", "BMEC"), "Origin": MT, "Referer": f"{MT}/"}
        out: dict[str, Job] = {}
        for area in self.cfg.get("areas", ["tecnologia-sistemas-y-telecomunicaciones"]):
            for page in range(int(self.cfg.get("pages", 3))):
                data = http.post_json(
                    self.name, "search", f"{MT}/api/avisos/searchV2?pageSize=100&page={page}&sort=RECIENTES",
                    {"filtros": [{"id": "area", "value": area}], "busquedaExtendida": False,
                     "tipoDetalle": "full", "withHome": False, "internacional": False}, headers=headers)
                rows = (data or {}).get("content") or []
                for j in rows:
                    jid = str(j.get("id"))
                    out[jid] = Job(
                        source=self.name, source_id=jid, title=html.unescape(j.get("titulo") or ""),
                        company="" if j.get("confidencial") else (j.get("empresa") or ""),
                        url=f"{MT}/empleos/{jid}.html",
                        location=_place(j.get("localizacion"), j.get("modalidadTrabajo")),
                        remote=(j.get("modalidadTrabajo") or "").lower() == "remoto",
                        posted_at=_dmy(j.get("fechaHoraPublicacion") or j.get("fechaPublicacion")),
                        description=strip_html(j.get("detalle")),
                    )
                if len(rows) < 100:
                    break
        for job in out.values():
            if not job.company:
                job.company = "Confidencial (Multitrabajos)"
        return list(out.values())


class Computrabajo(Source):
    name = "computrabajo"
    QUERIES = ["desarrollador", "programador", "python", "inteligencia-artificial", "full-stack",
               "backend", "frontend", "react", "ingeniero-de-software", "desarrollador-remoto"]
    CARD_RE = re.compile(r"<article[^>]*data-id='([0-9A-F]+)'.*?</article>", re.S)

    def fetch(self) -> list[Job]:
        profile = config.profile()
        cards: dict[str, Job] = {}
        for q in self.cfg.get("queries", self.QUERIES):
            for page in range(1, int(self.cfg.get("pages", 2)) + 1):
                resp = http.get(self.name, "search", f"{CT}/trabajo-de-{q}",
                                params={"pubdate": self.cfg.get("pubdate", 15), **({"p": page} if page > 1 else {})})
                if resp is None:
                    break
                found = [self.card(m.group(0), m.group(1)) for m in self.CARD_RE.finditer(resp.text)]
                for job in found:
                    if job:
                        cards.setdefault(job.source_id, job)
                if len(found) < 20:
                    break
        wanted = [j for j in cards.values() if flags.title_in_scope(j.title, profile)[0]]
        known = db.known_source_ids(self.name, [j.source_id for j in wanted])
        for job in wanted:
            if job.source_id in known:
                continue
            resp = http.get(self.name, "posting", job.url)
            ld = job_posting_ld(resp.text) if resp is not None else None
            if ld:
                job.description = strip_html(html.unescape(ld.get("description") or ""))
                job.posted_at = parse_date(ld.get("datePosted")) or job.posted_at
            elif resp is not None:
                job.description = page_description(resp.text)
        return list(cards.values())

    def card(self, block: str, oid: str) -> Job | None:
        link = re.search(r'<a class="js-o-link[^"]*" href="([^"#]+)[^"]*"[^>]*>\s*(.*?)\s*</a>', block, re.S)
        if not link:
            return None
        company = re.search(r"offer-grid-article-company-url>\s*(.*?)\s*</a>", block, re.S)
        city = re.search(r'<p class="fs16 fc_base mt5">\s*<span class="mr10">\s*(.*?)\s*</span>', block, re.S)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", block))
        mode = next((m for m in ("Presencial y remoto", "Remoto", "Híbrido", "Presencial")
                     if f"| {m} |" in text or f"|{m}|" in text), "Presencial")
        salary = re.search(r"\|\s*([\d.,]+ US\$[^|]*)\|", text)
        age = re.search(r"\|\s*(Hace [^|]+|Ayer|Hoy|Más de 30 días)\s*\|", html.unescape(text))
        return Job(
            source=self.name, source_id=oid, title=html.unescape(link.group(2)).strip(),
            company=html.unescape(company.group(1)).strip() if company else "",
            url=CT + link.group(1), location=_place(html.unescape(city.group(1)) if city else "", mode),
            remote=mode == "Remoto", posted_at=_ago(age.group(1) if age else ""),
            salary=salary.group(1).strip() if salary else "",
        )


def page_description(page: str) -> str:
    """The posting's text when the page has no JobPosting: the 'Descripción de la oferta'
    section, up to the skills widget that follows the requirements."""
    i = page.find("Descripción de la oferta")
    if i < 0:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page[i + len("Descripción de la oferta"):i + 15000]))
    text = text.split("Aptitudes asociadas a esta oferta")[0].split("Palabras clave:")[0]
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _dmy(value: str | None):
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(value or "", fmt).replace(tzinfo=timezone(timedelta(hours=-5)))
        except ValueError:
            continue
    return None


def _ago(text: str):
    """'Hace 23 horas' / 'Hace 5 días' / 'Ayer' / 'Más de 30 días' → an approximate date."""
    now = datetime.now(timezone.utc)
    t = text.lower()
    if not t:
        return None
    if "hoy" in t or "hora" in t or "minuto" in t:
        return now
    if "ayer" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\s*d[ií]as?", t)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*mes", t)
    return now - timedelta(days=30 * int(m.group(1))) if m else None

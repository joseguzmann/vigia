# Sources research — 2026-10-14

Why so few postings survive, and where to find more that a remote candidate in LATAM can take.

## Diagnosis (from Vigía's own data, 2,352 postings)

- Only **359 (15%)** were open to someone in LATAM. The rest: US/Europe-only, single-country
  "remote", hybrid/onsite. **Supply is the bottleneck, not the filters.**
- Leaks found in the filters: `full-stack` (hyphen) not in scope; company boards keep open
  postings for months, so the 45-day "stale" rule drops live roles on Greenhouse/Ashby/Lever.
- LinkedIn from a home IP: 2–7 requests, then a ~3-hour 429 block, repeatedly.

## Candidate sources, tested

| Source | Access | Tested | LATAM signal |
|---|---|---|---|
| **remoteintech/remote-jobs** (40k★): 886 remote companies with a `region` field | git clone | 630 companies are worldwide/americas; **143 have live public boards** (71 Greenhouse, 49 Ashby, 22 Lever) → `remote-companies-ats.json` | region per company |
| **Strider** (onstrider.com/jobs) | HTML | ~11 open roles, all for LATAM engineers | 100% LATAM |
| **Working Nomads** | JSON API `/api/exposed_jobs/` | 52 jobs, location field | mixed |
| **We Work Remotely** | RSS per category | works, few per category | "Anywhere in the World" |
| **Jobgether** (LATAM-tagged listings, ~1,000+) | HTML | **403**: blocks non-browser clients | strong |
| Workable | `apply.workable.com/api/v1/widget/accounts/<slug>` | 1 of the 630 companies | — |
| Get on Board "remote_local" | job page text | the page says which countries ("must reside in Argentina, Peru, Mexico or Costa Rica") | exact |
| Ashby JSON-LD `applicantLocationRequirements` | job page | present but unreliable (a "LATAM" role listed as Panama) | weak |

Reference implementation with 19 LATAM-oriented sources: IgorPC/latam-job-board (NestJS).

## LinkedIn posts ("#hiring" posts)

- Many LATAM roles are only announced as posts by recruiters and founders.
- LinkedIn's own post search needs login. A search engine index (`site:linkedin.com/posts ...`)
  surfaces them without login, and the post page can then be read logged-out.
- DuckDuckGo's HTML endpoint answers 202 (bot wall). Workable options need an API key:
  Brave Search API (free tier) or Google Programmable Search (100 queries/day free).
- Reference: moeezahmadkhan/linkedin-scraper (Brave + LinkedIn guest pages, no login).

## A second LinkedIn account

LinkedIn's User Agreement allows one personal profile per person; duplicates can be
restricted or removed, and reports say both accounts get flagged when linked (same device /
IP). Automating a logged-in account is the fastest way to get it restricted. Not recommended:
the main account is the candidate's best inbound channel.

Safer equivalent: LinkedIn **job alerts** on the main account (LinkedIn runs the search with
full filters and emails results) parsed from the mailbox.

## More applicant-tracking systems — 2026-10-15

Probed 909 companies (the remoteintech list outside Europe/Asia/Oceania/Africa, plus ~130
LATAM consultancies and product companies) against five more ATS, 5,690 requests. Boards
with at least one live posting: Teamtailor 8 (228 postings), Workable 4 (172), SmartRecruiters
20 (353), Breezy 10 (136), Recruitee 6 (12). Dropped: companies whose board only hires in Asia
(Swiggy, Mercari, Turtlemint, Clootrack, Adaface) and slugs that matched an unrelated company
(`amazon`, `kea`, `embraer` on Breezy).

| ATS | Endpoint | Text |
|---|---|---|
| Teamtailor | `https://<slug>.teamtailor.com/jobs.rss` | in the feed, with locations and `remoteStatus` |
| Workable | `https://apply.workable.com/api/v1/widget/accounts/<slug>?details=true` | in the feed. Cloudflare answers 429 (code 1015) after a burst of requests |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/<slug>/postings` | per posting, `/postings/<id>` |
| Breezy | `https://<slug>.breezy.hr/json` | the job page's schema.org JobPosting |
| Recruitee | `https://<slug>.recruitee.com/api/offers/` | in the feed |
| RemoteOK | `https://remoteok.com/api` | in the feed (some text arrives as mis-decoded UTF-8) |

**First run (2026-10-16 01:16 UTC):** 513 new postings, 4 passed (all RemoteOK). The company
boards are mostly European offices (Avenga, Endava), hybrid roles in Buenos Aires or Medellín,
or old postings left open for years. The run surfaced a rule bug: a location of "Remote job
(Remote)" or "Remoto" was read as a specific place; fixed, and the re-judge moved 93 stored jobs.

## Workday — 2026-10-16

Public JSON behind every career site: `POST https://<tenant>.wd<N>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`
(`{"searchText", "limit", "offset", "appliedFacets"}`) and `GET .../<site><externalPath>` for the full text.

Sites were found with 20 Serper searches (`site:myworkdayjobs.com Ecuador`, `... Quito`, `... LATAM remote
engineer`, ...): 95 sites, 88 answering. Each was searched for development roles: 24 had in-scope roles
in LATAM or remote, 19 kept. **Workday is large companies hiring per office** (Mexico City, Buenos
Aires, Bogotá, São Paulo, US-remote). First run: 437 postings, 373 new, 1,102 requests, no 429s; after
fixing two false signals, 1 passed (a visa role to review). Stord's one "Remote, Latin America" role
asks 5+ years and was 227 days old.

False signals found and fixed: Workday's form field "Relocation Provided: None" read as a relocation
offer (20 Motorola roles marked *extra*), and `#LI-Onsite` / `#LI-Hybrid` in the text now mean
office-bound. Legal-entity names come with a cost-center code ("957100 Motorola Solutions Ireland
Limited"), now dropped.

## Ecuador's local boards — 2026-10-16

- **Multitrabajos** (Bumeran network, site id `BMEC`): the web app calls
  `POST /api/avisos/searchV2?pageSize=100&page=N&sort=RECIENTES` with
  `{"filtros":[{"id":"area","value":"tecnologia-sistemas-y-telecomunicaciones"}], "tipoDetalle":"full", ...}`.
  It answers 403 without the cookies of a page load; with them (plus `Origin`/`Referer` and
  `x-site-id`) it returns every IT posting in the country with its full text, city and
  `modalidadTrabajo` (Presencial / Híbrido / Remoto). 146 postings on the first run.
- **Computrabajo Ecuador**: server-rendered search pages `/trabajo-de-<query>?pubdate=<days>&p=<n>`,
  20 cards each with title, company, city, salary, work mode and age. The full text is the
  posting's JobPosting JSON-LD, or the "Descripción de la oferta" section when a page has none.
  The market is small: "desarrollador" has ~31 open postings, ~9 from the last 15 days.

**First run:** 172 postings, 6 passed (on-site or hybrid in Quito, or remote). Most of the rest are
IT support, infrastructure or analyst roles (title out of scope), or on-site in Guayaquil, Loja, etc.
On-site/hybrid now passes only in `profile.home_cities` (with none set, in `home_country`).

## Indeed — 2026-10-16

No public API; the mobile app's GraphQL endpoint (`apis.indeed.com/graphql`, public app key and
headers, `indeed-co: EC`) returns up to 100 postings per search with their full text, as JobSpy
does. Ecuador: 141 postings for 13 searches over 30 days — the local market (Sofka, EPAM, Devsu,
Chevyplan, KATU…) plus worldwide-remote roles listed in Ecuador (Tether, My VA Support). Colombia,
Mexico and Argentina with the remote filter return mostly on-site roles, so they are left out.
Indeed marks the work mode only when the employer sets it; without it the text decides, and a
local posting that says nothing is on-site. Live test before deploying: 12 passed.

## Torre — 2026-10-16

The old public search (`search.torre.co/opportunities/_search`) answers "Invalid request" to every
format tried, including the filters the current web app sends (`status`, `remote`, a
personalized `bestfor`), and the app's own `/api/search/...` route needs its session. Torre's
robots.txt also disallows `/api/` for bots, so the API is not used.

What robots.txt allows is enough: `sitemap-index.xml` lists numbered sitemaps (the highest numbers
are the newest), each with `torre.ai/post/<id>-<company>-<title>` and a last-modified date. The
slug carries the title, so out-of-scope postings are skipped unfetched. Each posting page embeds a
JobPosting with the text and `applicantLocationRequirements`. Measured: 1,234 postings in the
last 30 days across the newest 3 English and 1 Spanish sitemaps, 115 in scope by slug; of a
sample of 30, 10 listed Ecuador, 12 listed other countries only, 8 listed none.

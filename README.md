# Vigía

**A self-hosted job radar.** Vigía watches job boards and company career pages on a schedule,
judges every posting against your profile, and tells you what is new and worth your time.

*Vigía* is Spanish for lookout: the one who keeps watch and calls out what matters.

```
$ vigia report
# Vigía — new since 2026-10-12 14:55
2284 new jobs: 35 worth applying, 13 worth a look, 2236 filtered out

## Worth applying (35)
### Agentic AI Engineer — coderoad  `13`
Latin America · 3+ yrs · ai · posted 2026-09-22 · via greenhouse
stack: python, langgraph, rag, llm, agent, langsmith, aws
...
## Filtered out (2236)
By reason: restricted 1740, title out of scope 1173, stale 945, too many years 820, ...
```

(A job can be out for several reasons at once, so the counts add up to more than the total.)

## Why

Job boards are built to look full, not to answer "can *I* apply to this?". For a remote
candidate outside the US, most "remote" roles are tied to a country, ask for more years
than stated in the title, are stale, or are a different profession behind a familiar
keyword. Checking that by hand, posting by posting, is the actual cost of a job search.

Vigía does that first pass: it collects broadly and judges strictly, explaining every
verdict, so the person only reads what survives.

## How it works

```
 sources (each on its own schedule)        judge                       store            read
 ┌──────────────────────────────┐   ┌──────────────────────┐   ┌──────────────────┐   ┌──────────────┐
 │ Get on Board                 │   │ title in scope?      │   │ SQLite           │   │ vigia report │
 │ Greenhouse / Ashby / Lever   │──▶│ years required       │──▶│ jobs (deduped    │──▶│ vigia health │
 │ Remotive · Himalayas · Jobicy│   │ where you can work   │   │  across boards)  │   │ vigia        │
 │ HN "Who is hiring?"          │   │ classic ML vs LLM    │   │ runs · requests  │   │   throttle   │
 │ LinkedIn (optional, slow)    │   │ stale · stack score  │   │ state            │   └──────────────┘
 └──────────────────────────────┘   └──────────────────────┘   └──────────────────┘
```

- **Collect broadly, filter late.** Everything is stored. Verdicts are recomputed from the
  config with `vigia reflag`, so you can change what you are looking for without collecting again.
- **Deduplicated across boards.** The same role found on two boards is one job that remembers
  everywhere it appeared.
- **Explainable verdicts.** Each job is `fit`, `review` (a person should look) or `out`, with
  the reasons: `asks 5+ years`, `restricted: location: Colombia`, `office-bound: hybrid`,
  `stale: posted 90 days ago`, `looks like classic ML`.
- **Polite by design.** Every HTTP request is logged with its status and latency. For LinkedIn,
  which rate-limits by IP, Vigía runs one query at a time on a slow rotation, fetches
  descriptions only for jobs that pass the cheap filters, and `vigia throttle` shows the
  429 episodes and how long they lasted.

## Sources

| Source | What it covers | Access |
|---|---|---|
| `getonbrd` | Get on Board, the tech job board most used by LATAM companies | public API |
| `greenhouse`, `ashby`, `lever` | Career pages of companies you choose to watch | public APIs |
| `remotive`, `himalayas`, `jobicy` | Remote job boards | public APIs |
| `hn_whoishiring` | Hacker News monthly "Who is hiring?" thread | Algolia HN API |
| `workingnomads`, `weworkremotely` | Remote job boards | JSON API / RSS |
| `strider` | LATAM-only marketplace; each role lists its accepted countries | job pages (schema.org) |
| `linkedin` | LinkedIn's logged-out job search (optional, off by default) | public pages, rate-limited |
| `linkedin_posts` | LinkedIn **hiring posts**, found via Google's index (Serper), read logged-out | Serper API key + public pages |

All LinkedIn traffic goes through one guest client (`linkedin_guest.py`): no login, a per-run
request budget, and a cooldown shared by every LinkedIn source after a 429. The idea and
several measurements come from [linkedin-scraper](https://github.com/moeezahmadkhan/linkedin-scraper) (MIT).

Titles are matched against `config/vocabulary.yaml`: role families with synonyms in English,
Spanish and Portuguese, on normalized text (accents, hyphens and slashes ignored), so
"Full-Stack", "fullstack" and "Desarrollador Full Stack" all count. The same file generates
the search phrases sent to LinkedIn and Serper.

Adding a source is one file: subclass `Source`, return `Job` objects, register it.

## Quick start

```bash
cp config/vigia.example.yaml config/vigia.yaml   # then edit your profile
echo 'SERPER_API_KEY=...' > .env                    # only for linkedin_posts
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/vigia run          # run every enabled source once
.venv/bin/vigia report       # what is new since the last review
.venv/bin/vigia report --mark   # ...and mark it as reviewed
.venv/bin/vigia health       # last run of each source
```

### With Docker

```bash
cp config/vigia.example.yaml config/vigia.yaml
docker compose up -d --build
docker compose exec vigia vigia report
```

The container runs `vigia serve`: a loop that runs each enabled source when its
`every_minutes` is due. Data lives in the `vigia-data` volume.

## Configuration

`config/vigia.yaml` (git-ignored) holds the profile Vigía judges against:

- `title_include` / `title_exclude`: which titles are in scope
- `eligible_regions`: where you can work from (`latam`, `ecuador`, `worldwide`...)
- `max_years`: jobs asking for more are filtered out
- `max_age_days`: older postings are treated as stale
- `stack`: skills that raise a job's score
- `company_exclude`: companies you never want to see
- `sources.<name>`: on/off, schedule, queries, companies to watch

## Commands

| Command | |
|---|---|
| `vigia serve` | run forever on the configured schedule |
| `vigia run [SOURCE ...]` | run sources once |
| `vigia report [--mark] [--all]` | new jobs since the last review |
| `vigia health [--check]` | status per source; `--check` is the container healthcheck |
| `vigia throttle [SOURCE]` | requests per hour and rate-limit episodes |
| `vigia reflag` | re-judge stored jobs after changing the config |
| `vigia sources` | list sources |
| `vigia web [--host] [--port]` | the web UI (default `127.0.0.1:8000`) |

## Web UI

`vigia web` serves a small private UI over the same database:

- **Jobs**: the judged jobs, freshest first (🆕 = posted in the last 2 days), filterable by
  verdict, source, category, level, age and text, or only the ones not acted on yet.
- **Job**: every field, the full description, the flags, every board it was seen on, and a
  form to record what you did: `applied` (with an experiment variant and a visa/relocation
  mark), `skipped` (with a reason) or `saved`. The outcome of an application is edited later.
- **Experiment**: applications with their outcome, and the share that reached a screen or
  further, by variant, category, level, source and how old the posting was when you applied.
- **Health**: `vigia health` and `vigia throttle` as HTML.

The UI writes only to its own `actions` table (one row per action; the latest one is the job's
state; `age_days` stores the posting's age at action time). It has no login: the Compose
service publishes it on `127.0.0.1:8001` only, so reach it through an SSH tunnel:

```bash
ssh -N -L 8001:127.0.0.1:8001 your-server   # then open http://localhost:8001
```

## Roadmap

- **Phase 2: LLM triage with evals.** Replace the regex judgment with an LLM that reads each
  posting against the candidate's profile (eligibility, real seniority, fit), keep the regex
  version as the baseline, and measure both against a hand-labelled set of postings.
  Traces in Langfuse.
- More sources: Workable, Torre, company discovery from postings that mention LATAM.

## Notes

Vigía only reads public pages and APIs, never logs into any account, and keeps request rates
low. The LinkedIn adapter is optional and off by default; check the terms of any site before
enabling scraping of it.

MIT licensed.

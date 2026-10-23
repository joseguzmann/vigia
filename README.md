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
| `multitrabajos`, `computrabajo` | Ecuador's local boards: the whole IT area / searches by role, with the work mode | web app API / HTML + JSON-LD |
| `workday` | Career sites of large companies, searched for development roles | public JSON |
| `indeed` | Indeed searches per country (Ecuador), via the mobile app's GraphQL API, as [JobSpy](https://github.com/speedyapply/JobSpy) (MIT) does | GraphQL |
| `greenhouse`, `ashby`, `lever` | Career pages of companies you choose to watch | public APIs |
| `teamtailor`, `workable`, `smartrecruiters`, `breezy`, `recruitee`, `bamboohr`, `rippling`, `manatal` | More company career pages, on other applicant-tracking systems | RSS / public APIs (Breezy: job page; BambooHR, Rippling: one detail call per in-scope opening) |
| `remotive`, `himalayas`, `jobicy`, `remoteok` | Remote job boards | public APIs |
| `hn_whoishiring` | Hacker News monthly "Who is hiring?" thread | Algolia HN API |
| `workingnomads`, `weworkremotely` | Remote job boards | JSON API / RSS |
| `torre` | Torre: postings from its public sitemaps and pages, with the countries each accepts (its API is off-limits in robots.txt) | sitemaps + JSON-LD |
| `strider` | LATAM-only marketplace; each role lists its accepted countries | job pages (schema.org) |
| `linkedin` | LinkedIn's logged-out job search (optional, off by default) | public pages, rate-limited |
| `openroles` | Companies and postings from the [openroles](https://openroles.today) dataset (54 ATS, nightly; data CC BY-SA 4.0 by datascry/openroles): new boards are checked once and watched if anything passes; postings on ATS without a Vigía source are read from their page | public data |
| `discover` | Finds companies to watch: Google searches on ATS job pages (Ashby, Greenhouse, Lever, Workable, Teamtailor); boards with jobs in scope are added to their ATS source | Serper API key |
| `remoteyeah` | RemoteYeah listing pages (Latin America, Python, FastAPI, LLMs, Django), job pages read for their JSON-LD | public pages |
| `linkedin_posts` | LinkedIn **hiring posts**, found via Google's index (Serper), read logged-out | Serper API key + public pages |
| `linkedin_posts_apify` | LinkedIn **hiring posts** from LinkedIn's own post search, run by an Apify actor; launched by hand (MCP `search_linkedin_posts`), capped per run and per month | Apify token (paid per post) |

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
echo 'APIFY_TOKEN=...' >> .env                      # only for linkedin_posts_apify
echo 'NTFY_URL=https://ntfy.example/vigia' >> .env   # optional: phone notification per new fit
echo 'NTFY_TOKEN=...' >> .env                       # (ntfy access token allowed to publish)
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
| `vigia mcp` | MCP server on stdio (see below) |
| `vigia app list [--open] [--status S]` | applications |
| `vigia app show N` / `vigia app status N STATUS [--note] [--on DATE]` | one application / move it |
| `vigia app next` | number and experiment variant of the next application |
| `vigia app stats` / `vigia app export` | the experiment as JSON / every application as Markdown |
| `vigia app import FILE.json` | bulk load from an older log |

## Fresh roles first

Discovery (openroles, job-board-aggregator, `discover`) runs about daily and only decides which
companies to watch. Watching is faster: each ATS source reads all its boards every few hours, and
the boards that posted something kept in the last `hot_boards.days` get an extra pass every
`hot_boards.every_minutes`. A new role then goes through the rules and the job reader within
minutes, and a good open one reaches the phone.

## Job reader (LLM ranking)

The rules decide what is in or out; an LLM then reads each new fit, review and extra against the
CV of its family (ai, backend, fullstack) and answers, as structured output: can the candidate be
hired from where they live (with the sentence that decides it), visa sponsorship, years asked,
level, hard requirements the CV lacks, and a 1–10 match with one line of why. `new_jobs` is
ordered by it, and only open roles at or above `notify_min_match` reach the phone; what is found
during quiet hours is held and sent when they end.

Besides the stored listing, the reader gets the job's page fetched at read time (its schema.org
`JobPosting` and visible text; not LinkedIn, whose stored text is already complete). Boards often
shorten the location to "Worldwide" or "Remote"; when the page says otherwise, or answers 404/410,
the reading carries a `warnings` line. LinkedIn posts are read even when the rules filtered them
out, and one the reader finds open and good comes back as "worth a look".

`new_jobs` comes in two queues, each ranked on its own: LinkedIn, posts and job boards first,
company career pages (ATS) second, so a company load's hundreds of roles can't bury the few posts.

Readers are tried in order (`reader.readers`):

- `claude-p` runs the Claude Code CLI in print mode on the subscription it is signed in with
  (`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`; build the image with
  `INSTALL_CLAUDE_CODE=1`). It runs with no tools, no MCP servers and no settings, because the
  prompt carries untrusted text. It runs without `ANTHROPIC_API_KEY` in its environment: with
  both set, Claude Code bills the API instead of the subscription. Subscription use outside interactive Claude Code is governed by
  Anthropic's terms, which have changed during 2026: check them.
- `anthropic` / `openai` call the API through LangChain, with a monthly budget (`api_budget_usd`).

When one fails the next reads the job and the phone gets one alert (at most every
`alert_every_hours`). Each job is read once; readings live in the `job_readings` table.

## Applications

Vigía also keeps the record of the job search: every application, found by Vigía or entered by
hand, with the CV that was sent, the experiment variant, the channel, what was written for it,
and every status change (`preparing → applied → ack → screen → technical → final → offer`, or
`rejected` / `ghost` / `closed`) with its date and where the news came from (email, the user, the
assistant). That history is what measures the experiment: a success is **a step with a person
within 14 days of applying**. Inbound contacts and applications still being prepared are kept
but left out of the numbers. Applications younger than 14 days with no reply yet are shown as
*pending*, not as failures.

The rotation is by application number: odd numbers get variant A1, even numbers A2.

## Email

Optional. The `gmail` source reads your mailbox **read-only** every 15 minutes and classifies
each new message once:

| kind | what | kept |
|---|---|---|
| `alert` | a job board's alert (Torre, Indeed, Himalayas, Computrabajo, a company's "new jobs" digest) | the message, and **its postings go through the filter** like any source's |
| `application` | an ATS, an assessment, or a company you applied to | the message, linked to its application |
| `recruiter` | a LinkedIn InMail | the message |
| `review` | job-related words, no rule sure | the message |
| `other` | everything else | **only the message id**, so it is never read again |

Nothing is sent, labelled, moved or deleted. Pending messages show in the web UI (Emails) and
through the MCP tools `new_emails`, `get_email`, `mark_emails_reviewed` and `set_email_kind`
(correct a classification; `other` erases all but the id). Postings whose page is not served
to a script (Indeed, Himalayas) are kept from the email itself, without text, until another
source brings the full posting.

**Token.** Create an OAuth client of type *Desktop app* in a Google Cloud project with the Gmail
API enabled, run the consent flow once on your own machine with the scope
`https://www.googleapis.com/auth/gmail.readonly`, and save the resulting authorized-user JSON
(`refresh_token`, `client_id`, `client_secret`, `token_uri`) as `secrets/gmail-token.json` next to
`docker-compose.yml` (git-ignored, mounted read-only). Publish the consent screen to
*In production*: while it is in *Testing*, Google expires refresh tokens after 7 days.

## MCP server

`vigia mcp` speaks the Model Context Protocol on stdin/stdout, so an assistant can read the
radar and keep the application log without a port being opened anywhere. From another machine:

```bash
claude mcp add vigia -- ssh your-server docker exec -i vigia vigia mcp
```

Tools: `health`, `new_jobs`, `mark_reviewed`, `search_jobs`, `get_job`, `skip_job`,
`next_application`, `record_application`, `set_application_status`, `update_application`,
`list_applications`, `get_application`, `experiment_stats`, `export_applications_markdown`,
`new_emails`, `get_email`, `mark_emails_reviewed`, `set_email_kind`.
Calls are run one at a time over a single connection to the same SQLite file the collector
writes (WAL, busy timeout), and invalid values come back as errors that name the allowed ones.

## Web UI

`vigia web` serves a small private UI over the same database:

- **Jobs**: the judged jobs, freshest first (🆕 = posted in the last 2 days), filterable by
  verdict, source, category, level, age and text, or only the ones not acted on yet.
- **Job**: every field, the full description, the flags, every board it was seen on, and
  forms to record an application (the next number and its variant are suggested), a skip
  (with a reason) or a save.
- **Applications**: every application with its status, editable in place; each one has a page
  with its details and full status history.
- **Experiment**: cold applications and how many got a step with a person within 14 days, by
  variant, visa flag, family, level, channel and how old the posting was when applying.
- **Health**: `vigia health` and `vigia throttle` as HTML.

The UI writes to `applications` / `application_events` and to `actions` (skips and saves). It has no login: the Compose
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

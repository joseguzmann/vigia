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

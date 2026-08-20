
# RUNBOOK — the weekly and monthly runs

How to actually operate Artist Finder. Written for any assistant or person; nothing here depends
on a particular tool. For *why* the system is built this way, read [`AGENTS.md`](AGENTS.md); for
what it is, read [`README.md`](README.md).

> **This file is the source of truth for the protocol.** A thin Claude Code skill
> (`artist-scout`) and two scheduled tasks exist outside the repository as convenience triggers.
> If any of them ever disagrees with this file, this file wins — they are shortcuts, not the
> specification.

**Fetching is split three ways.** Python fetches the sources that serve structured JSON-LD
(free, no Apify); the assistant fetches the ones that block it or bury their listings in HTML; both write
`data/raw/<date>__<source>.json` and `run_weekly.py` does the rest. Check the `access` field in
`data/sources.json` before reaching for a crawler — an earlier version of this file claimed
Python could not fetch at all, which sent every source through Apify unnecessarily.

---

## 1. Two things to understand first

**A single run is nearly worthless, and saying so is part of the job.** Nothing in Indian
ticketing publishes tickets sold. The signal is entirely in what changes between crawls. Runs
1–2 build a baseline and the report says `trajectory_observable: False`; **do not paper over
it.** Asked "who should we sign" in week one, the honest answer is "nobody yet — here is the
candidate pool". Trajectory comes online at **3 crawls spanning 21 days**. Never skip a week.

**There are two independent scores and they must never be merged.**

| | measures | from |
|---|---|---|
| **momentum** | are they getting hot in India | rooms, added nights, sell-through, platform graduation |
| **export_signal** | would it travel to North America | US/CA search volume, its growth, foreign tour dates |

The Tour Engine measured why: tickets per million followers spans **15×**, sublinear at exponent
0.369. A domestic star can have near-zero diaspora demand. Always quote both, never one number.

---

## 2. The weekly run

### Step 0 — the free structured sources, in Python, FIRST

A probe on 2026-08-19 (`tools/probe_sources.py`) found that **allevents.in, highape.com and
district.in serve plain HTTP with schema.org JSON-LD**. Python fetches those directly — about
1,000 structured events in 90 seconds, no Apify, no cost, no text parsing:

```bash
cd tools && python fetch_listings.py
```

Check `access` in `data/sources.json` before reaching for a crawler. Only `apify-html` and
`apify-browser-proxy` sources need an assistant with a scraping tool. `dead` sources (insider) are skipped.

### Step 1 — the sources that DO need Apify

`data/sources.json` is the list, and each source's `access` field says which route it needs.

**BookMyShow is SUPPLEMENTARY, not primary** — corrected by measurement on 2026-08-19. It is the
biggest ticketing site in India, but it only exposes a 10-item JSON-LD teaser of featured events
(~3 usable rows per page, against 15-64 free ones from AllEvents), and that teaser skews to acts
that are already big. Keep its budget small. The long tail — `townscript`, `allevents`,
`highape`, `meraevents` — is where artists appear FIRST and is worth more of the effort.

**All genres, not just comedy.** Comedy, music, theatre, devotional/bhajan, club nights, magic,
spoken word, sport. New formats appear constantly and the long-tail sites are where they surface
first — `discovery_queries.genres` in `sources.json` has the search vocabulary.

**BookMyShow refuses plain fetching** — confirmed 403 on all five explore paths. Use Apify
`apify/website-content-crawler` with browser rendering and a **residential** proxy. Skillbox,
Townscript and MeraEvents answer fine but publish no JSON-LD, so their listings need HTML
parsing — the cheap crawler is enough for those.

Baseline parse rates from the first real run, for spotting regressions: allevents ~69%,
highape ~65%, district ~41%.

> **All eight sources were probed on 2026-08-19** and carry `access` and `verified_at`.
> BookMyShow is the exception that still needs work: it is reachable only through a browser
> and its CATEGORY PATHS ARE STILL UNMAPPED, so do a discovery pass on it before the first
> strict crawl and write what you find back into `sources.json`.
> Re-run `tools/probe_sources.py` after any source change — that file records what was
> observed, never what was assumed.

**Capture `category` on every row.** It is the *primary* genre signal — a large share of clean
listings ("Kanan Gill: Yes I'm Fine", "Zakir Khan Live in Concert") carry no genre word at all,
and the words they do carry are shared across every kind of performer. Without a category those
rows land in `unknown` and are held out of the ranking.

Write per source:

```
data/raw/<YYYY-MM-DD>__<source_key>.json
{"events": [{"title","venue","city","url","date","status","category","price_min","price_max"}],
 "domains_seen": ["bookmyshow.com", "some-new-platform.in"]}
```

`title` is the only required field. Put the platform's own words in `status` ("Sold Out",
"Filling Fast"). **`domains_seen` is the "new channels" half of the brief** — list every
ticketing domain you met, known or not.

### Step 2 — fold in the Instagram tips

Group DMs are not reachable by any API (personal accounts have no Graph access; business
accounts need Meta App Review, a 24-hour window, and still do not expose group threads). So this
is a paste. If the user gives you names — any time, not just Mondays:

```bash
cd scout && python run_weekly.py --tips "Name One, Name Two" --tips-by "Vihar"
```

**A tipped name is always researched, whatever it scores.** A person noticing somebody is
evidence that arrives *before* any supply-side signal.

### Step 3 — run it

```bash
cd scout && python run_weekly.py --international --write-dossiers
```

### Step 4 — enrich the shortlist only

RISING entities and tipped names, nothing else:

- **Foreign dates.** `--international` calls Bandsintown when an `app_id` is configured. It is
  **thin for comedy** — a blank result means "not found on Bandsintown", *never* "does not tour
  abroad". Fill the gap with SERP checks; `international.serp_queries(name)` has the query set.
- **Instagram.** `apify/instagram-scraper`, verified profiles only. **9 of 24 guessed handles
  were squatters**, including an unverified "Vipul Goyal" with 2 followers.

### Step 5 — report back

Lead with what changed and the RISING list. Quote both scores with their coverage. Close the
tips loop — say what happened to last week's names, or people stop sending them.

---

## 3. The monthly job — search volume

**Separate cadence on purpose.** Google refreshes Keyword Planner monthly; polling weekly redraws
the same figure four times and looks like a stalled artist.

```bash
cd tools && python fetch_search_volume.py --all
```

Needs `data/config.json` (gitignored) — see `config.example.json`. Uses the PPC Guru.ca MCC, so
figures are **exact**, not the bucketed ranges a zero-spend account gets. `--check` probes the
credentials and reports if a response smells bucketed.

- US and Canada are fetched and stored **separately**, never summed.
- **MoM works immediately. True YoY needs ~13 months of these runs** — until then the tool
  reports a within-window trend and labels it as not a full year. Do not call it YoY.
- The **contamination gate** runs automatically: if the "<name> comedian" share is under 20%, the
  bare name's volume belongs to somebody else. Measured: Jaspreet Singh 2%, Aakash Gupta 1%.
  Those artists look enormous in search and do not sell.

---

## 4. Kill list — never ship these

| ❌ Never | ✅ Instead | Why |
|---|---|---|
| "X will sell N tickets in Toronto" | "X is worth forecasting; here is the engine command" | This project produces **no** NA number |
| Merge momentum and export_signal | Quote both | They measure different things from different data; merging breaches the 15× finding |
| Present momentum as measured | "heuristic, not back-tested" | `backtest.py` refuses to run for a reason |
| A score without its coverage | "64/100 on 47% of signal weight" | A score on two signals and one on six are not comparable |
| Call a window trend "year over year" | "+241% across ~9 months (not a full year)" | Google's rolling window cannot contain both endpoints |
| Sum US and CA search volume | Report both, use the stronger | Cardinal rule across all four projects |
| Guess a genre from the title | Capture the platform category | "Zakir Khan Live in Concert" is a comedian; "Zakir Hussain" is a tabla maestro |
| Force a play through name extraction | Let it be a `production` | Otherwise *Mughal-e-Azam: The Musical* enters the watchlist as an artist called Mughal |
| Say "no international dates" | "none found via Bandsintown/SERP" | Bandsintown barely covers Indian comedy |
| Read a big following as a big draw | Check tickets per million followers | Sublinear: 10× the following buys ~2.3× the draw |
| Silently drop unparseable rows | Report the parse rate | A falling rate means an adapter broke — more important than any artist that week |
| Skip a week | Run it | The schedule is the measurement apparatus |

---

## 5. When the user asks "who should we sign?"

1. **Coverage first** — crawls, span, whether trajectory is live at all.
2. **The RISING list**, each with momentum *and* demand, plus the signals driving them.
3. **What is missing** — usually search volume, occasionally a clean alias.
4. **The next command** — the Tour Engine screen, not a booking.

If trajectory is not observable yet, say so in the first sentence and give the candidate pool.

---

## 6. Handing off to the Tour Engine

`--write-dossiers` writes `Artist Tour Engine/artists/<slug>.json` with the Indian evidence in
`notes` and **real US/CA volumes in `search`** when the monthly job has fetched them — that is
what unlocks an E3 forecast. It **merges and never clobbers**: a hand-researched figure or a real
prior result always wins. Productions and festivals are skipped entirely.

Then it is `tour-pnl`'s job:

```bash
cd ../Artist\ Tour\ Engine/engine && python screen.py "ARTIST NAME" --shows 10
```

---

## 7. Maintenance the user will ask for

- **"Add a platform"** → `data/sources.json` (set `tier` carefully — it drives platform
  graduation) and build the adapter into your fetch step.
- **"Add a genre"** → `scout/genres.py`: a detection rule, a room scale, an export weight, and
  vocabulary. An unknown genre is held out of the ranking rather than mis-scored.
- **"This venue is wrong"** → `data/venues_india.json`. Bands matter; exact capacity does not.
- **"We contacted them"** →
  `watchlist.set_status('slug', 'contacted', note='...', owner='Dhaval')`. Statuses:
  `new watching researching contacted negotiating signed passed`. A run never overwrites these.
- **"Is the score any good yet?"** → `python backtest.py`. NOT READY until ~6 months of history.
  That is the correct answer, not a failure.

---

## 8. Before you hand over any run

- All 11 module self-tests print `ALL CHECKS PASS` (no network needed).
- Per-source parse rate reported; anything under 60% flagged and explained.
- No North American ticket number anywhere in your reply.
- Both scores quoted with coverage, and "not calibrated" said wherever momentum appears.


---

## Scheduling it elsewhere

The two schedules currently live in Claude Code and run while that app is open. Nothing about the
pipeline depends on that — every command above is an ordinary Python invocation, so any scheduler
works. On Windows, Task Scheduler:

```
Weekly   Mon 09:30   cd <repo>\tools && python fetch_listings.py
                     cd <repo>\scout && python run_weekly.py --international --write-dossiers
Monthly  3rd 09:40   cd <repo>\tools && python fetch_search_volume.py --all
```

Set `PYTHONIOENCODING=utf-8` in the task environment — the output contains `— ÷ ·`, which crash
on the Windows cp1252 default.

**Keep the two cadences separate.** Google refreshes Keyword Planner monthly; running the volume
job weekly redraws the same figure four times and draws a flat line that reads as a stalled
artist.

## The one step a schedule cannot do

`data/raw/<date>__bookmyshow.json` needs a browser behind a residential proxy, which means an
assistant with a scraping connection, or an `apify_token` in `data/config.json` so
`fetch_listings.py` can start the run itself. Without either, BookMyShow is skipped and the free
sources still run — the weekly job degrades rather than failing.

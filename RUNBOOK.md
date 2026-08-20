
# RUNBOOK — the weekly and monthly runs

How to actually operate Artist Finder. Written for any assistant or person; nothing here depends
on a particular tool. For *why* the system is built this way, read [`AGENTS.md`](AGENTS.md); for
what it is, read [`README.md`](README.md).

> **This file is the source of truth for the protocol.** A thin Claude Code skill
> (`artist-scout`) and two scheduled tasks exist outside the repository as convenience triggers.
> If any of them ever disagrees with this file, this file wins — they are shortcuts, not the
> specification.

**Fetching is split three ways.** Python fetches sources that expose structured feeds — JSON-LD
or District's Next.js `EventData` (free, no Apify); the assistant fetches the ones that block it or
bury its listings in HTML; both write
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

### Operator quick start

1. Double-click [`tools/open_dashboard.cmd`](tools/open_dashboard.cmd) on Windows. It builds the
   current artifact and opens the local/private dashboard at `http://127.0.0.1:8765/`.
2. Check freshness, source health, and the trajectory banner before reading any ranking. One crawl
   means a baseline; **zero RISING is honest** until 3 crawls span 21 days.
3. Confirm BookMyShow or District evidence for a candidate. Long-tail sources are discovery-only.
4. Review US and Canada evidence separately; do not add the geographies. Approve a dossier only
   when the dashboard says Forecast ready. The dossier handoff is the designed input under
   `../Artist Tour Engine/artists/`; this scout never displays a ticket forecast.

The four visible workflow stages are **Discovered → Major-platform confirmed → Diaspora validated
→ Forecast ready**. They are evidence gates, not a merged score.

### Step 0 — the free structured sources, in Python, FIRST

The first probe on 2026-08-19 found that **allevents.in and highape.com serve plain HTTP with
schema.org JSON-LD**. District's accepted adapter now uses direct HTTP Next.js `EventData` on eight
activity routes plus an `/events` JSON-LD fallback. A dated read-only validation on **2026-08-21**
returned **102 unique events, 87 categorized, and 102/102 with venue/date**; counts can drift. The
routine Python fetch remains free and requires no Apify or text parsing for these structured routes:

```bash
cd tools && python fetch_listings.py
```

Check `access` in `data/sources.json` before reaching for a crawler. Only `apify-html` and
`apify-browser-proxy` sources need an assistant with a scraping tool. `dead` sources (insider) are skipped.

### Step 1 — optional browser/HTML sources

`data/sources.json` is the list, and each source's `access` field says which route it needs. Townscript
is postponed and is not part of the routine browser/Apify path.

**BookMyShow and District are the primary validation sources.** BookMyShow remains low-yield in
plain/browser probing: it exposed a 10-item teaser (~3 usable rows per page against 15–64 from
AllEvents), and the teaser skews toward acts that are already big. Keep its browser/Apify route
optional and show `blocked/unconfigured` when it is unavailable. AllEvents, HighApe, MeraEvents and
other long-tail/self-serve platforms are discovery inputs only; a long-tail listing alone cannot
confirm a lifecycle stage. **Townscript is postponed**, not part of the routine weekly path.

**All genres, not just comedy.** Comedy, music, theatre, devotional/bhajan, club nights, magic,
spoken word, sport. New formats appear constantly and the long-tail sites are where they surface
first — `discovery_queries.genres` in `sources.json` has the search vocabulary.

**BookMyShow refuses plain fetching** — confirmed 403 on all five explore paths. Use Apify
`apify/website-content-crawler` with browser rendering and a **residential** proxy. Skillbox and MeraEvents answer fine but publish no JSON-LD, so their listings need HTML parsing
if enabled. Townscript is postponed until a dedicated adapter is owned and tested.

Historical first-run parse rates (2026-08-19), for spotting regressions: allevents ~69%,
highape ~65%, district ~41%. The current District EventData validation is the dated 2026-08-21
coverage above, not this historical 12-row baseline.

> **Source health is evidence, not a promise.** BookMyShow may remain `blocked/unconfigured`
> until its optional browser/Apify route is configured; this is not zero evidence. District has
> the direct EventData/JSON-LD routes above. Re-run `tools/probe_sources.py` after any source change — it records
> what was observed, never what was assumed.

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
- **MoM works immediately. A single rolling 12-month pull cannot produce true YoY** because it
  lacks the same-month prior-year pair. YoY is observable as soon as stored history contains that
  pair; a 48-month pull can provide it immediately. Until then the tool reports a within-window
  trend and labels it as not a full year.
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

The supported unattended path is Windows Task Scheduler. The scheduled-compatible runner keeps
weekly and monthly jobs separate, refreshes the local dashboard artifact, and writes non-secret
status plus logs under `out/automation/` (`status.json` and `*.log`). On Windows, Task Scheduler:

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

## The one optional step a schedule cannot do

`data/raw/<date>__bookmyshow.json` may need a browser behind a residential proxy, which means an
assistant with a scraping connection, or an `apify_token` in `data/config.json` so
`fetch_listings.py` can start the run itself. Without either, the optional BookMyShow route is
reported as blocked/unconfigured; retained data is not erased, the free sources still run, and
source health tells the operator what to retry. Never paste credentials into a report or log.

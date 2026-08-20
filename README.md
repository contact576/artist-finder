# Artist Finder

**Find Indian live acts while they are still cheap to sign.**

Every week this crawls Indian ticketing platforms, tracks how each artist's bookings change over
time, and ranks them by how fast they are *rising* rather than how big they already are. The
credible ones get handed to a calibrated forecaster to size a North American tour.

Built for Millennial Events by way of the settled 2026 Samay Raina tour P&L, which is where
every number this tool leans on was measured.

### The four documents

| File | What it is |
|---|---|
| **README.md** | this — what the project is and where it stands |
| **[AGENTS.md](AGENTS.md)** | **the handbook.** Cardinal rules, architecture, and every trap already hit. Read before changing anything |
| **[RUNBOOK.md](RUNBOOK.md)** | how to operate it — the weekly run, the monthly job, the kill list |
| `CLAUDE.md` | a pointer to AGENTS.md, so Claude Code finds it |

`AGENTS.md` is the name Codex looks for, and Cursor reads it too. It is deliberately **not**
duplicated into `CLAUDE.md` — two copies of a handbook diverge within a fortnight, and a stale
handbook is worse than none because it gets believed.

---

## The problem it solves

By the time an artist is obviously worth booking, they are expensive. The money is in spotting
them about twelve months earlier — while they are still playing 200-seat rooms.

That is hard for one blunt reason:

> **No Indian ticketing platform publishes how many tickets a show sold.** Not BookMyShow, not
> District, not one of them.

So a single crawl gives you a list of shows and no way to tell which matter. What *is* observable
is **change between crawls** — and change is only visible if you keep looking.

| Signal | Why it means something |
|---|---|
| **Room escalation** | 150-seat club in June, 800-seat theatre in August. A promoter books a bigger room only when they believe they can fill it |
| **Added nights** | A second date at the same venue means the first one sold |
| **Platform graduation** | Moving from self-serve listings to a gatekept platform means somebody with money changed their mind |
| **Sell-through speed** | Days from on-sale to "Sold Out" |
| **New ticketing domains** | Whole new channels appearing in the market |

**This is why it runs on a schedule.** The weekly cadence is not convenience — it is the
measuring instrument. Skipping a week destroys measurement that cannot be recovered.

---

## The two scores, and why they are never merged

| | Answers | Computed from |
|---|---|---|
| **momentum** | Are they getting hot *in India*? | rooms, added nights, sell-through, platform graduation |
| **export_signal** | Would it *travel* to North America? | US/CA search volume, its growth, foreign tour dates |

The Artist Tour Engine measured why these must stay apart: **tickets sold per million followers
varies by 15×** across its 30-artist benchmark, and the follower→draw fit is sublinear
(exponent 0.369). Reach in India and ticket sales to the North American diaspora come apart badly.

A domestic star can have almost no diaspora audience. In the first real run a mainstream singer
came out at **momentum 12, demand 6** — on one blended score he would have looked reasonable.

`score.py`'s self-test asserts the two weight sets are disjoint, in both directions.

### The output is an instruction, not a number

`stature` (how big now) × `momentum` (how fast rising) → one of four quadrants:

- **RISING** — small rooms, moving fast. *The signing target.* With demand: call them. Without: research first.
- **EARLY** — small and quiet. Watch.
- **ESTABLISHED** — already big. Likely repped and priced.
- **STALLED** — big rooms, flat. Monitor.

---

## What it refuses to do

These are hard stops in code, not style preferences.

1. **It never quotes a North American ticket number.** That comes from the Artist Tour Engine,
   which is back-tested against 647 real shows. This tool is tested against nothing yet, and
   letting an untested guess borrow a tested tool's credibility is the failure mode to avoid.
2. **It labels its own score uncalibrated, everywhere.** The weights are judgement. `backtest.py`
   is written and *refuses to run* until ~6 months of history exists.
3. **It never scores an unmeasured signal as zero.** An artist found last Tuesday has no
   trajectory — that is *unknown*, not *flat*. Without this the ranking silently becomes a
   ranking of how long we have been watching.
4. **US and CA are never summed.** Same cardinal rule as every sibling project.

---

## Current state — 21 Aug 2026

This is a private scout, not a forecaster. The current baseline is roughly **669 shows · 585
active entities · 449 eligible candidates** from one immutable crawl; with one crawl, **RISING =
0 is the honest result** because trajectory is not observable yet. Google Ads Keyword Planner
history is available for about 449 candidates (276 US values, 264 Canada values, 47 months).

**Working and proven on live data.** 12 modules, ~5,100 lines, all self-tests passing offline.

| | |
|---|---|
| First real crawl (19 Aug) | **997 events**, free, in ~90 seconds |
| First-crawl ledger (historical) | **675 shows · 588 entities** — one snapshot |
| Venue classification | **63%** (up from 43% after mapping real venues) |
| Cross-check | **8 artists from the Tour Engine's own benchmark** found live — Harsh Gujral (4 shows, large + mid theatre), Rahul Dua (7), Varun Grover, Amit Tandon, Vipul Goyal, Abish Mathew |

### Sources, as measured — not assumed

Every source was probed on 2026-08-19 and carries `access` and `verified_at` in
`data/sources.json`.

| Source | Route | Yield |
|---|---|---|
| **AllEvents** | free — plain HTTP + JSON-LD | 15–64/page, venue+date on 100% |
| **HighApe** | free — plain HTTP + JSON-LD | 265 on one page |
| **District** | direct HTTP Next.js EventData on 8 activity routes + `/events` JSON-LD | primary validation; 102 unique / 87 categorized / 102 venue+date (2026-08-21) |
| **BookMyShow** | optional browser/Apify route | primary validation; live route may be blocked |
| **MeraEvents** | free/HTML discovery input | discovery-only |
| Skillbox · Townscript | HTML-only or postponed | discovery-only; Townscript postponed |
| Insider.in | 502 everywhere — **dead, disabled** | — |

> **BookMyShow remains a primary validation source, but its live route is optional and low-yield.**
> The baseline exposed a 10-item JSON-LD teaser of featured events; the real grid renders client-side.
> Across 3 pages: 30 events, only 9 upcoming, and `comedy-shows-bengaluru` returned zero. A
> browser/Apify full-grid route is implemented as a reproducible path but may be blocked until
> configured. District is the other primary validation source; long-tail platforms are discovery
> inputs only and cannot confirm an artist stage by themselves. District's current direct route was
> validated read-only on 2026-08-21: 102 unique events, 87 categorized, and 102/102 with venue/date
> across eight activity routes plus `/events`; counts are dated and can drift.

---

## Architecture

Fetching is split, and everything below the fetch line is deterministic and offline — which is
why every module self-tests with no network and no data.

```
tools/fetch_listings.py     AllEvents/HighApe JSON-LD + District EventData/`/events` JSON-LD
an assistant + Apify        BookMyShow browser route; Skillbox/MeraEvents HTML if enabled
                              Townscript is postponed
        both write ->  data/raw/<date>__<source>.json
scout/run_weekly.py         ingest -> score -> report
```

Apify datasets are readable without a token, so the blocked path needs no new credential: an
assistant starts the run, then `fetch_listings.py --apify-dataset <id>` reads and parses it.

```
data/tips.json              human tips, pasted any time
data/demand.json            MONTHLY search volume + foreign tour dates
data/raw/<date>__*.json     the only network step
  │  snapshot.ingest()       normalise; unparseable rows go to `review`, never silently dropped
  ▼
data/snapshots/<date>.json  RAW, IMMUTABLE — never rewritten, never back-filled
  │  rebuild_ledger()        fold the series into one row per show
  ▼
data/shows.json             first_seen, last_seen, status history
  │  signals -> score        level (day one) + trajectory (needs history)
  ▼
data/watchlist.json         machine fields + human outreach fields (never overwritten by a run)
  ▼
out/scout_<date>.md         the digest
  ▼
../Artist Tour Engine/artists/<slug>.json
```

| Module | Role |
|---|---|
| `genres.py` | genre + entity type, per-genre room scale, export weight. Runs **before** name extraction |
| `gazetteer.py` | canonical artist names, seeded from the Tour Engine's 41-name benchmark |
| `model.py` | listing → entity, room, role. Returns a confidence; below `MIN_TRUST` it goes to review |
| `snapshot.py` | the two stores and their different rules |
| `signals.py` | level vs trajectory, and `observability()` — the gate on the whole trajectory half |
| `demand.py` | the diaspora axis — search volume, MoM/YoY, `export_signal` |
| `score.py` | stature, momentum, quadrant, action |
| `watchlist.py` | persistent state. **Human fields are never overwritten** |
| `tips.py` | the Instagram paste inbox and its loop-closing report |
| `international.py` | foreign tour dates (Bandsintown + SERP) |
| `report.py` | the digest — leads with change, caveats inline |
| `bridge.py` | dossier writer. **Merge, never clobber** |
| `backtest.py` | the honesty hook. Refuses to run until there is enough history |

`tools/` holds `probe_sources.py` (what is reachable and how), `fetch_listings.py` (the crawler),
`fetch_search_volume.py` (the monthly Google Ads job) and `setup_credentials.py`.

---

## Open the private dashboard

On Windows, double-click [`tools/open_dashboard.cmd`](tools/open_dashboard.cmd). It builds the
current local artifact and opens `http://127.0.0.1:8765/`; it does not deploy or expose anything
publicly. The dashboard shows **Discovered → Major-platform confirmed → Diaspora validated →
Forecast ready**, source health, freshness, separate stature/momentum/export signals, and the
next evidence action. Review BookMyShow/District validation before treating a candidate as ready.

The dashboard is local/private by default. Do not publish it without explicit authorization.

## Running it

Self-tests need no network and no data:

```bash
cd scout && python genres.py && python model.py && python signals.py && python score.py
```

All twelve print `ALL CHECKS PASS`.

The weekly run:

```bash
cd tools && python fetch_listings.py
cd ../scout && python run_weekly.py --international --write-dossiers
```

`--date` · `--recompute` (re-score without re-parsing) · `--no-save` · `--tips "A, B" --tips-by NAME`

The **monthly** search-volume job, deliberately separate because Google refreshes Keyword Planner
once a month:

```bash
cd tools && python fetch_search_volume.py --all
```

> Windows: set `PYTHONIOENCODING=utf-8` — the output contains `— ÷ ·` which crash on cp1252.

---

## What is done, and what is next

### Done

- [x] All 12 modules built and self-testing offline
- [x] Every source probed and recorded with measured access route
- [x] Direct structured fetcher — first crawl banked 997 real events, free; District's current
  EventData/JSON-LD route was separately validated read-only on 2026-08-21
- [x] BookMyShow browser/Apify route measured and represented with a reproducible fixture; live
  access remains optional and may be blocked until configured
- [x] Genre awareness across comedy, music, devotional, theatre, club, magic, spoken word
- [x] Two-axis scoring with a disjointness guard
- [x] Instagram tips inbox with loop-closing
- [x] International tour detection
- [x] Weekly + monthly schedules created
- [x] First real snapshot banked

### Next — in order

1. **Let three weeks pass.** Trajectory needs **3 crawls spanning 21 days**. Until then the
   report and dashboard say so in their first line and give a candidate pool, not a shortlist.
2. **Configure BookMyShow only if its extra coverage is worth the browser/Apify cost.** A blocked
   optional route is shown as source health, never as zero evidence.
3. **Keep Townscript postponed** until its adapter can be owned, tested, and labeled discovery-only.
4. **Grow the venue map.** 33% of rooms are still unmapped — a 264-venue long tail that shrinks
   every week.
5. **Around month six, run `backtest.py`.** It will finally have enough history to say whether
   the momentum weights predict anything. If they do not, re-derive them rather than leaving a
   score that looks measured and is not.

### Known limitations, stated plainly

- One artist can still fragment into several entities when a show name is glued to their name
  with no separator. `gazetteer.py` fixes it for the 41 known artists; the general case needs
  the roster to grow.
- Venue capacities are approximate and mostly unverified. The code scores the *band*, which
  survives a 30% error — anything depending on the exact seat count is a bug.
- Genre is `unknown` for ~37% of rows, mostly from sources that publish no category. Those are
  held out of the ranking rather than mis-scored.

---

## Picking this up in another tool

The repository is self-contained. `AGENTS.md` and `RUNBOOK.md` carry everything, all commands are
plain Python with no absolute paths, and there are no dependencies beyond the standard library.

The one-click dashboard launcher is in the repo and runs locally. Two operational items do **not**
live in the repo and will not travel with a clone:

- **The Claude Code skill** (`artist-scout`) — a convenience trigger. `RUNBOOK.md` is the actual
  protocol and supersedes it.
- **The two schedules** — Windows Task Scheduler tasks are the supported unattended path. The
  runner writes non-secret status and log metadata to `out/automation/status.json` and
  `out/automation/*.log`; `RUNBOOK.md` has the install and dry-run commands.

One genuine coupling to know about: this project reads `../Artist Tour Engine/` by relative path,
for the benchmark gazetteer and for writing dossiers. Both degrade gracefully if it is absent —
`bridge.engine_available()` returns False and `gazetteer` falls back to `data/aliases.json` — but
the handoff to the forecaster is the point of the whole thing, so keep them side by side.

## Relationship to the other projects

This is one of four coupled repositories. It lives as a **sibling folder** to `Artist Tour Engine`
because it reads that engine by relative path — for the benchmark gazetteer and for writing
dossiers into `../Artist Tour Engine/artists/`.

| Project | Role |
|---|---|
| **Artist Finder** (this) | finds artists early, scores momentum and export potential |
| `Artist Tour Engine` | the calibrated forecaster — 647-show benchmark, back-tested bands |
| `Samay Raina 2027 PnL Prediction` | one artist's tour forecast and proposal |
| `Samay Raina Settlement 2026` | the settled accounting every ratio was measured from |

The handoff is deliberately narrow: this project writes a dossier and stops. The North American
number comes from the engine, off US/CA search volume — which is why the action for a RISING
artist is *research*, never *book them*.

---

## Security

`data/config.json` is gitignored and always was — it holds a Google Ads developer token, an OAuth
client secret and a refresh token for a manager account with 49 live advertiser accounts.
`data/config.example.json` is the tracked template and carries no values.

`tools/setup_credentials.py` handles entry: it hides secrets as you type, validates each value's
shape, never echoes anything back, and writes only to the ignored file. `--show` reports what is
set without revealing any of it.

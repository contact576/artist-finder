# AGENTS.md — the handbook

**Read this before changing anything.** It is the working handbook for this repository, written
for any coding assistant: Codex, Claude Code, Cursor, or a person. `CLAUDE.md` is a pointer to
this file, not a second copy.

Everything below was measured, not assumed. Where a number appears, it came from a real run, and
the date is given so you know how stale it might be.

## What this project is

**Find Indian live acts while they are still cheap to sign, and hand the credible ones to the
calibrated forecaster.** It crawls Indian ticketing platforms on a schedule, tracks how each
artist's bookings change over time, and ranks them by how fast they are rising rather than by
how big they already are.

It is a **scout, not a forecaster**. It produces no North American ticket number, ever. That
comes from `../Artist Tour Engine/`, which is back-tested; this project's job ends at writing a
dossier and saying "worth researching".

The fourth project in the family. `../Samay Raina Settlement 2026/` (settled accounting),
`../Samay Raina 2027 PnL Prediction/` (one artist's forecast) and `../Artist Tour Engine/`
(the forecaster) are **read-only** from here, with one deliberate exception: `bridge.py` writes
dossiers into `Artist Tour Engine/artists/`, which is that engine's designed input mechanism.

---

## Cardinal rules

- **Nothing here is a ticket count.** No Indian platform publishes one. Every signal is a proxy
  for what a promoter *believes* — the room they booked, whether they added a night, whether a
  gatekept platform took them on. Real, because someone risked money on it; weaker than a sales
  figure; never presented as one.
- **Indian traction does not imply diaspora demand, and this is measured, not cautious.** The
  Tour Engine found tickets per million followers spanning **15×** across its roster, with a
  sublinear follower→draw fit (exponent **0.369**): ten times the following buys about 2.3×
  the draw. Reach in India and sales to the North American diaspora come apart badly. A high
  score here means *research this person*, never *they will sell N tickets in Toronto*.
- **TWO SCORES, NEVER MERGED.** `momentum` (score.py) is India-side rate of rise — rooms, added
  nights, sell-through, platform graduation. `export_signal` (demand.py) is diaspora-side —
  US/CA search volume, its growth, foreign tour dates. Different modules, different data, and
  `score._selftest` asserts their weight sets are **disjoint**. Merging them would let a
  domestic star with no diaspora audience outrank someone who already sells out London — which
  is exactly what the 15× spread says happens. The end-to-end fixture keeps a worked example:
  a mainstream singer at momentum 12 / demand 6.
- **US and CA search volume are never summed.** Where one number is needed the STRONGER geo
  carries it. Same rule as the three sibling projects.
- **Genre changes what "big" means.** 800 seats is a milestone for a comedian and an off-night
  for a playback singer, so each genre has its own stature curve in `genres.ROOM_SCALE`. Genre
  does **not** modulate momentum — a room getting bigger is a room getting bigger, and there is
  no data yet to justify saying otherwise.
- **The momentum score is NOT calibrated and every emission says so** (`calibrated: False`).
  The weights in `score.py` are judgement. Ground truth — who actually broke out — only exists
  in hindsight, so `backtest.py` is built and deliberately refuses to run until roughly six
  months of history exists. Do not quietly delete that guard to make the tool look finished.
- **An unobservable signal is never scored as zero.** Momentum renormalises over the signals
  actually available for that artist. Without this, the ranking becomes a ranking of how long
  we have been watching, and a genuinely new name loses to a stale one.
- **Bands, not seat counts.** Venue capacities in `data/venues_india.json` are mostly
  unverified. The code scores the *band* (club → mid theatre), which survives a 30% error in
  any single capacity. Anything that depends on the exact number is a bug.

## The schedule IS the measurement apparatus

This is the thing to understand before changing anything.

A single crawl yields a list of shows and no idea which matter. All the information is in the
**difference between crawls**: a bigger room, a second night, a status flipping to sold out, a
name appearing on a gatekept platform for the first time. None of that exists in one snapshot.

Consequences that follow directly:

| | |
|---|---|
| **Day one is a baseline, not a shortlist** | `signals.observability()` reports `trajectory_observable: False` until **3 crawls spanning 21 days**, and the report leads with that in a block quote rather than burying it |
| **Level works immediately, trajectory does not** | `level()` (room, cities, platform) is single-crawl computable. `trajectory()` is not. `score.stature` vs `score.momentum` keeps them apart |
| **Missing a week costs history, not just freshness** | gaps widen the eras that `_split_era` compares |
| **Snapshots are immutable** | an improved parser does **not** rewrite old snapshots. Otherwise the series stops recording what we knew when, and the future back-test becomes circular |

## The quadrant is the product

Two numbers become one instruction. `stature` = how big now; `momentum` = how fast rising.

| Quadrant | Meaning | Action |
|---|---|---|
| **RISING** | small rooms, moving fast | **the signing target** — research US/CA search volume, then run the engine |
| EARLY | small and quiet | watch |
| ESTABLISHED | already big, still rising | likely repped and priced already |
| STALLED | big rooms, flat | monitor |

**`STATURE_CEILING = 75` is load-bearing.** At 55 a textbook breakout — club to mid theatre —
immediately read as ESTABLISHED, so the act of breaking out disqualified an artist from being
called a breakout. If RISING ever goes empty for weeks while momentum is clearly firing, check
this constant first.

## The fetch/compute split — corrected by the first real probe

The original claim here was "Python cannot crawl". That was too strong, and the probe on
2026-08-19 disproved it. Python cannot call an MCP tool; it can absolutely make HTTP requests.
AllEvents and HighApe answer a plain request with schema.org JSON-LD. District uses a direct HTTP
Next.js `EventData` route on eight activity pages plus an `/events` JSON-LD fallback. A read-only
validation run on **2026-08-21** returned **102 unique events, 87 categorized, and 102/102 with
venue/date**; counts can drift as listings change:

```
tools/fetch_listings.py       allevents · highape (JSON-LD); district (EventData + /events JSON-LD)
the agent + Apify             bookmyshow (403, needs browser + residential proxy)
                              skillbox · meraevents (HTML-only); townscript (postponed)
        both write ->  data/raw/<date>__<source>.json
scout/run_weekly.py           ingests → scores → reports
```

`data/sources.json` carries an `access` field per source saying which route applies, and
`tools/probe_sources.py` re-derives it. BookMyShow and District are the primary validation
sources. AllEvents, HighApe, MeraEvents and other long-tail/self-serve platforms are discovery
inputs only; Townscript is postponed until a deliberate adapter project. **Prefer direct JSON-LD
wherever a site offers it** — it is free, structured, needs no LLM and cannot drift the way a
text parser does.

Everything below the fetch line is deterministic and offline, which is why every module has a
self-test that needs no network. A failed crawl degrades to "no new data this week" instead of
a broken pipeline, and a fixed crawl can be re-ingested for the same date safely —
`snapshot.ingest()` replaces that source's slice of the day.

## Operator surface

The active operator product is the private **Artist Search Intelligence** dashboard defined in
`KEYWORD_INTELLIGENCE_SPEC.md`. It is generated with `tools/build_dashboard.py`, opened with
`tools/open_dashboard.cmd`, and bound to `127.0.0.1` only. Do not deploy it publicly without
explicit authorization.

The dashboard toggles **India | USA | Canada**, with every market stored and calculated separately.
It shows Google-estimated monthly searches, absolute and percentage MoM, 3- and 12-month averages,
YoY, tracked-roster genre totals, risers, artist detail, the new-name review inbox, mapping health,
and monthly automation status. Every request explicitly uses `GOOGLE_SEARCH_AND_PARTNERS`, but
that does not mean all YouTube views or all activity across Google products. USA and Canada are
never summed.

The durable name system is `data/artist_roster.json` plus `data/artist_candidates.json`. Keyword
Planner related ideas expose topics and possible gaps; they do not prove an artist identity.
Periodic evidence-backed roster research and human tips add directory candidates; the monthly job
refreshes Keyword Planner ideas but does not pretend that an API phrase is a researched person.
Only a dated public identity review can promote a candidate into verified genre totals. The
dashboard opens on **Verified + researched** so the operator can inspect the long tail, while every
summary total, genre ranking, and gainer list still uses **verified artists only**.

The monthly flow is: refresh niche keyword ideas → retain them in the review inbox → fetch 48
months of Search + Partners history for the curated/verified roster in India/USA/Canada → validate
mappings → write an immutable run manifest → rebuild the dashboard. Evidence-backed roster
maintenance is a separate deliberate research task. The older ticketing scout, immutable
snapshots, and dossier boundary remain preserved, but ticketing data no longer drives the active
dashboard.

## Pipeline

```
data/tips.json                      ← human tips, pasted any time
data/demand.json                    ← MONTHLY search volume + foreign dates
data/raw/<date>__<source>.json      ← the fetch step writes these (only network step)
  │  snapshot.ingest()               normalise + park unparseable rows in `review`
  ▼
data/snapshots/<date>.json          RAW, IMMUTABLE — never rewritten, never back-filled
  │  snapshot.rebuild_ledger()       fold the series into one row per show
  ▼
data/shows.json                     DERIVED — first_seen, last_seen, status history
  │  signals.for_all()               level (day one) + trajectory (needs history)
  │  score.rank()                    stature, momentum, quadrant, action
  ▼
data/watchlist.json                 STATE — machine fields + human outreach fields
  │  report.render()
  ▼
out/scout_<date>.md                 the digest
  │  bridge.upsert()   (RISING only)
  ▼
../Artist Tour Engine/artists/<slug>.json
```

| Module | Role |
|---|---|
| `genres.py` | genre + entity type, per-genre room scale, vocabulary, export weight. **Runs before name extraction** |
| `gazetteer.py` | canonical artist names, seeded from the Tour Engine's 41-name benchmark. Stops one performer fragmenting into three entities |
| `../tools/probe_sources.py` | which sources are reachable and how. Records observations into `data/source_probe.json` |
| `../tools/fetch_listings.py` | direct structured fetch for JSON-LD and District EventData routes |
| `../tools/setup_credentials.py` | interactive credential entry. Secrets never pass through chat |
| `model.py` | listing → entity, room, role. **The hard part.** Returns a confidence; below `MIN_TRUST` the row goes to review rather than into the data |
| `demand.py` | the diaspora axis — stored search volume, MoM/YoY, `export_signal` |
| `tips.py` | the Instagram paste inbox and its loop-closing report |
| `international.py` | foreign tour dates (Bandsintown + SERP), canonical countries |
| `../tools/fetch_search_volume.py` | Google Ads API client. **MONTHLY, outside the weekly path** |
| `snapshot.py` | the two stores and their different rules; `show_id()` identity |
| `signals.py` | level vs trajectory, and `observability()` — the gate on the whole trajectory half |
| `score.py` | stature, momentum, quadrant, export-readiness checklist |
| `watchlist.py` | persistent state; **human fields are never overwritten by a run** |
| `report.py` | the digest — leads with change, caveats inline not in a footer |
| `bridge.py` | dossier writer. **Merge, never clobber**; `--force` deliberately does not exist |
| `backtest.py` | the honesty hook. Refuses to run until there is enough history |
| `run_weekly.py` | the one command |

## Traps already hit — do not reintroduce

- **Confidence and role are different uncertainties.** An early version multiplied them, so
  "Comedy Night ft. \<real person\>" fell below the trust floor and a clean name was thrown away
  over a billing ambiguity. `confidence` measures *did we read the name right*;
  `role`/`role_certain` carry *are they headlining*.
- **Peak room ≠ stature.** Using the artist's biggest-ever room let one support slot on an arena
  bill make a club act read as an arena act. `_robust_room()` uses the highest band with **≥2
  shows**, keeps the peak beside it, and flags a peak resting on a single booking.
- **Lineup slots are not headline evidence.** Stature prefers `role == 'headline'` shows and
  only falls back to all shows when there are none.
- **An unclassified venue is `None`, never "small".** `classify_venue()` returning `None` means
  unknown; trajectory skips those rooms rather than scoring them zero.
- **A title-keyed `show_id` fakes announcements.** Promoters edit titles mid-sale ("Live"
  becomes "Live - FINAL SHOW"). Identity is URL first, then a hash of source+venue+city+date —
  never the title.
- **`first_seen` on the very first crawl is censored.** It records when *we* started looking,
  not when the market did. `first_seen_censored` marks it and `is_new` refuses to fire on it.
- **A title does not carry its genre.** "Kanan Gill: Yes I'm Fine" and "Zakir Khan Live in
  Concert" contain no genre word, and "live in concert" is used by comedians and singers alike —
  an early version filed Zakir Khan, a stand-up, as a mainstream musician. The **platform's own
  category is the primary signal**; the crawler must capture it, and `unknown` is an acceptable
  answer that gets held out of the ranking rather than guessed.
- **Not every event has a performer.** A play, a musical or a festival brand is the entity
  itself. `entity_type` is decided BEFORE extraction and productions skip it, which is what stops
  *Mughal-e-Azam: The Musical* being recorded as an artist called Mughal.
- **Tip matching is on name TOKENS, not substrings.** People tip nicknames — "Bassi" for Anubhav
  Singh Bassi. A substring version with a length guard rejected exactly that, the commonest shape
  a real tip takes. Tokens must be ≥4 chars and the match must be UNIQUE, so "Singh" matches
  nobody rather than the wrong person.
- **A single Google 12-month window cannot produce year-over-year.** Its last month and first
  month are only ~11 months apart, and the same-month-last-year figure is absent from that one
  response. `yoy()` becomes observable as soon as OUR accumulated history contains the latest
  month and the same month in the prior year; a 48-month pull can provide that pair immediately.
  Until then `window_trend()` is offered and says "NOT a full year" in its own note.
- **A category page is supposed to be a subset of `/all`** — comparing the two proves nothing.
  To tell whether a site's category segment actually filters, compare two DIFFERENT category
  pages against each other. Measured on AllEvents: Mumbai comedy-vs-music overlap 0% (real
  filtering), Bengaluru 92% (the segment is ignored and the same list is served whatever you
  ask for). Believing Bengaluru's label would file every musician in the city as a comedian.
- **Generic-title matching must be word-bounded.** `'jam' in title` matches "Dino James",
  "Jamshed" and "Jaipur", and billed a solo headliner as one name on a jam night.
- **The artist can be on EITHER side of a dash.** "SAMAY RAINA - Still Alive Tour" puts them
  first; "Noor-e-Ishq- Harshdeep Kaur" puts them second. Try both, keep whichever looks like a
  person.
- **"X by Y" was unhandled and cost real artists.** "Love, Death & Ketchup by Varun Grover",
  "Aise Kaise by Amit Tandon" — without a `by` marker the SHOW NAME is read as the performer.
- **One performer fragmenting into several entities is not cosmetic.** "Vipul Goyal Unleashed",
  "Vipul Goyal-Unleashed" and "Vipul Goyal" were three entities with a third of the shows each,
  which splits the very escalation trajectory this tool exists to measure. `gazetteer.py` fixes
  it for known names; the general case still needs the roster to grow.
- **Re-running a date must REPLACE its history row, not append one.** Retrying a crawl, fixing a
  parser or regenerating a report all re-run a date, and an append-only history turned each into
  a duplicate — 7,072 lines of them from one re-run. It matters because that history IS the
  back-test set: duplicated same-day rows would weight one week several times over and corrupt
  the exact measurement it exists to enable.
- **The field is `entities`, not `artists`.** It holds performers *and* productions, and a field
  meaning two things depending on a sibling field is the `venue_exp` trap from the settlement
  project. `snapshot.entities_of()` still reads the old key, because snapshots are immutable.

## Commands

Self-tests need no network and no data; run them after any change:

```bash
cd "Artist Finder/scout" && PYTHONIOENCODING=utf-8 "C:/Users/dapat/AppData/Local/Programs/Python/Python312/python.exe" model.py
```

Each of `genres.py` `model.py` `snapshot.py` `signals.py` `demand.py` `score.py` `watchlist.py`
`tips.py` `international.py` `report.py` `bridge.py` runs standalone and prints
`ALL CHECKS PASS` — eleven modules, no network required.

The weekly run:

```bash
cd "Artist Finder/scout" && PYTHONIOENCODING=utf-8 "C:/Users/dapat/AppData/Local/Programs/Python/Python312/python.exe" run_weekly.py --write-dossiers
```

`--date YYYY-MM-DD` · `--recompute` (skip ingest) · `--no-save` (dry run) · `--write-dossiers`
· `--tips "A, B" --tips-by NAME` · `--international`.

The MONTHLY search-volume job, deliberately not part of the weekly run:

```bash
cd "Artist Finder/tools" && PYTHONIOENCODING=utf-8 "C:/Users/dapat/AppData/Local/Programs/Python/Python312/python.exe" fetch_search_volume.py --all
```

Needs `data/config.json`, which is **gitignored** — it holds a Google Ads developer token and an
OAuth refresh token for an MCC with 49 live advertiser accounts. `config.example.json` is the
tracked template. `--check` probes credentials and reports if a response looks bucketed, which
means the request hit an account without real spend and the figures are unusable.

## Environment

- **stdlib only.** No pandas, no requests, no openpyxl. Deliberate: the compute half must run
  anywhere. The half that needs a browser or a scraping API belongs to whichever assistant
  is driving, not to Python.
- Everything prints `— · ÷`, which crash on Windows cp1252. Always `PYTHONIOENCODING=utf-8`,
  and plain `python` is not on PATH in Git Bash — use the full interpreter path.
- All paths derive from each module's own location. **Keep it that way** — a hardcoded absolute
  path is what silently broke all 28 scripts in the settlement project when it moved.

## Version control

The git root is the **parent** folder, published as the private `contact576/samay-tour-pnl`, and
its `.gitignore` **denies everything and allow-lists what may be tracked**. This project is
allow-listed as `/Artist Finder/`, with `data/raw/` and `out/` excluded — raw crawls are
reproducible bulk and the digests are generated.

`data/snapshots/` **is tracked on purpose.** It is the accumulated measurement and the only
thing here that cannot be regenerated; losing it resets the tool to day one.

## Measured state — first real run, 2026-08-19

The numbers a new session should start from. If a later run differs sharply from these, that is
a regression to investigate, not a new baseline to accept.

| | |
|---|---|
| Events fetched, free, in ~90s | **997** |
| Banked into the ledger | **675 shows · 588 entities** |
| Parse rate | allevents **69%** · highape **65%** · district **41%** |
| Venue classification | **63%** (was 43% before the venue map was grown from real data) |
| Genre `unknown` | **37%**, mostly from sources publishing no category — held out of the ranking |
| Cross-check | **8 artists from the Tour Engine's 41-name benchmark** found live at plausible rooms |

**District's current route was validated read-only on 2026-08-21:** its eight activity routes use
Next.js `EventData`, with `/events` as a JSON-LD fallback, yielding 102 unique events, 87 with
categories, and venue/date on all 102. The count is dated and can drift. **BookMyShow is a primary
validation source with a measured low-yield live route.** It is India's
biggest ticketing site, but what it exposed in the baseline was a 10-item JSON-LD teaser; the real
grid renders client-side. Across 3 rendered pages: 30 events, only **9 upcoming**, and
`comedy-shows-bengaluru` returned zero — roughly **3 usable rows per page** against AllEvents'
15-64 free ones. The full-grid browser/Apify route is therefore optional and may be blocked until
configured. District remains the other primary validation source; long-tail platforms are still
valuable discovery inputs but cannot by themselves confirm a stage.

## Current retained state — 2026-08-21

The older ticketing state remains preserved: the second immutable snapshot contains 1,101 live
listings and the corrected derived ledger contains 801 shows / 687 entities. It is no longer the
active dashboard driver.

The 2026-08-21 live Search Intelligence run requested 48 months for **711 curated/verified names**
in India, USA, and Canada with `GOOGLE_SEARCH_AND_PARTNERS` explicit. Each geo mapped 710: 698
exact and 12 labelled unambiguous close variants, with one missing and zero ambiguous mappings.
Because the current USA response ends one month earlier than India/Canada, the default comparable
display month is June 2026 rather than manufacturing a July zero.

The full registry has **1,173 records**: 44 verified, 667 evidence-sourced directory candidates,
and 461 legacy crawl phrases excluded from the default scope. Only the 44 verified artists enter
summary totals, genre rankings, and gainer lists. The related-keyword inbox has 1,439 phrases from
30 retained discovery runs. Those phrases are discovery clues, not 1,439 artists. Coverage is
therefore still building and current genre totals must not be presented as complete market size.

## Two ways to reach a blocked source, neither needing a new credential

Apify datasets are readable over plain HTTP **without a token**, and an assistant with an Apify
connection can start actor runs. So the default split is:

    the agent   starts the run, hands over the dataset id
    Python      `fetch_listings.py --apify-dataset <id>` reads it, parses, writes data/raw/

Setting `apify_token` in `data/config.json` additionally lets Python start the run itself. That
is tidier for unattended use and adds no capability.

## Still open

- **District has a dated live read-only validation.** On 2026-08-21 its direct EventData activity
  routes plus `/events` fallback returned 102 unique events, 87 categorized, and 102/102 with
  venue/date. Re-run source validation because counts and routes can drift.
- **BookMyShow's full live grid remains optional/blocked until its browser/Apify route is configured.**
  Its reproducible fixture and source-health state remain available; no missing BMS rows are treated
  as zero evidence. Indian ticketing consolidated hard in 2024–25 (District absorbing Paytm
  Insider) and any source can move again.
- **Venue capacities are unverified.** Bands make that tolerable, not correct.
- **Only the roster gives real ground truth.** When Millennial actually books one of these
  artists, the settled result belongs in the Tour Engine as an `actual`. Twenty of those beat a
  thousand crawls.

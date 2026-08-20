# Artist Finder product specification

## Purpose and boundary

Artist Finder is a private scouting tool for finding Indian live acts worth
researching early. It does not forecast attendance or ticket sales. A displayed
booking, venue band, platform presence, added date, or sold-out status is
promoter-side evidence and never proof of tickets sold.

The product hands a selected, evidence-backed artist dossier to the designed
input boundary in `../Artist Tour Engine/artists/`. The Tour Engine, not this
product, owns North American forecasting.

## Operator and decisions

The primary operator is a nontechnical scout or talent-research lead. In one
session they need to decide:

1. Which newly discovered acts merit verification on primary platforms.
2. Which verified acts have enough US/Canada diaspora evidence to research for
   export.
3. Which acts have complete enough evidence to hand to the Tour Engine.
4. Whether the underlying data is fresh and observable enough to support the
   decision, or whether to wait, correct, or re-run a source.

The dashboard must make "do nothing yet" a clear, valid outcome whenever
source coverage, confidence, or trajectory history is insufficient.

## Lifecycle

The interface shows one current stage per eligible entity and the evidence
needed for the next stage. Stages are workflow states, not a merged ranking.

| Stage | Meaning | Entry evidence | Next action |
|---|---|---|---|
| Discovered | A credible entity was found by any input. | Parsed above the trust floor, or a human tip awaiting review. | Confirm a future/present booking on BookMyShow or District. |
| Major-platform confirmed | The act has primary-platform booking evidence. | A matching BookMyShow or District listing with traceable URL/date; entity ambiguity is not silently accepted. | Review US and Canada demand and foreign-date evidence. |
| Diaspora validated | Primary-platform evidence plus usable diaspora evidence. | US or CA search evidence and/or qualifying foreign-date evidence, with its freshness and observability shown. | Complete/approve a dossier for the Tour Engine. |
| Forecast ready | A dossier can be safely written to the Tour Engine input. | Required identity, source evidence, and export-readiness fields are present; dossier write outcome is recorded. | Hand off; do not display a forecast here. |

Long-tail/self-serve platforms are discovery inputs only. BookMyShow and
District are the primary validation sources. A long-tail listing alone must
never mark an entity Major-platform confirmed.

## Dashboard views

### Overview

Show four lifecycle counts, data freshness, source-health summary, and an
observability banner. The default action list prioritises records needing
primary-platform confirmation, diaspora review, correction, or a dossier
decision. It must not imply that the largest score is the best signing choice.

### Artist explorer

Provide a searchable, sortable table with filters for lifecycle stage, genre,
entity type, city, platform/source, primary-validation status, venue band,
data-confidence/review state, and data freshness. The table includes separate
columns for stature, India-side momentum, and diaspora-side `export_signal`.
No combined score is shown or used for default ordering.

Momentum is a rate-of-rise proxy. It can be displayed only with its available
signal inputs and trajectory observability state. Stature represents current
India-side booking level and is distinct from momentum. `export_signal` is a
separate diaspora-side research signal and is not a ticket forecast.

### Artist detail

The detail view contains:

- Lifecycle stage, next required evidence, and an explicit action.
- A separate score panel for stature, momentum, and `export_signal`, including
  confidence/availability and score caveats.
- A chronological evidence ledger: source, platform role, listing URL, event
  date, city, venue band, status, first/last seen, parsing confidence, and any
  correction/review note.
- Primary-platform confirmation evidence, clearly separated from discovery
  evidence.
- Diaspora evidence with US and Canada values displayed separately. When a
  single representative value is needed, label the stronger geography used;
  never add US and CA values.
- Foreign dates, human watchlist/outreach fields, dossier readiness, and the
  latest dossier-write result. Human fields remain preserved across runs.

Venue evidence uses only the named room band (for example club or mid theatre),
never a capacity-derived assertion.

### Source health and operations

Show each source's purpose (primary validation or discovery), last attempted
and successful fetch, rows fetched/accepted/reviewed, parser/coverage signals,
failure reason, and next safe action. It must make a blocked or low-yield
BookMyShow route visible rather than treating its absence as zero evidence.

Show weekly and monthly job status separately: last run, result, log location,
artifact date, next scheduled run if known, and whether credentials were
available. Credential values, tokens, refresh tokens, and raw secrets are never
rendered or logged.

## Evidence and truth model

Every displayed factual claim is traceable to a dated snapshot, stored demand
record, human correction, or job result. Immutable snapshots are never edited
to repair a parser; corrections and review overlays remain separately
traceable. Unknown means unknown, not zero.

The dashboard must distinguish these states in both summary and detail views:

| State | Required presentation |
|---|---|
| No data yet | "No eligible records yet" with the missing input and how to create it. |
| Unknown | A neutral `Unknown` badge; excluded inputs are named, never converted to 0. |
| Review required | State why (such as low parse confidence, ambiguous identity, or unclassified venue) and link to the underlying row/evidence. |
| Source error | State source, attempt time, safe failure summary, retained prior-data date, and retry path. |
| Stale data | Show data age and last successful artifact; do not call it current. |
| No observable trajectory | Explain that three crawls spanning 21 days are required and show crawl count/span. Momentum must not claim a live trend before then. |
| No diaspora evidence | State that export readiness is unvalidated, not that demand is zero. |
| No rising candidates | State that this is expected with insufficient trajectory history; do not manufacture a RISING result. |

The report and dashboard repeat the central caveat near any relevant score:
platform and room signals are booking proxies, Indian traction does not imply
North American diaspora demand, and no ticket counts are produced here.

## Privacy and operating boundary

The dashboard is local/private by default and must not be deployed publicly
without explicit authorization. It exposes no credentials, unredacted job
environment, API responses containing secrets, or private outreach notes beyond
the operator's local environment. Raw crawls and source URLs remain local
operational evidence; generated dashboard content is limited to the approved
derived fields.

## Definition of done and acceptance tests

The implementation is complete when a nontechnical operator can open one local
launcher and all checks below pass:

1. The dashboard loads from the current generated artifact without console or
   runtime errors and is usable at desktop and narrow-window widths.
2. Overview, lifecycle funnel, explorer, artist detail, source health, data
   freshness, and automation status render from traceable generated data.
3. Search and every listed filter work together; sorting never creates or
   displays a merged momentum/export score.
4. A fixture or current record proves the lifecycle transitions and shows the
   exact next-evidence requirement for each stage.
5. An artist detail proves US and CA values remain separate, venue bands are
   textual bands, and evidence links/dates match the generated source data.
6. A one-snapshot/insufficient-history fixture proves trajectory is marked
   unavailable until at least three crawls spanning 21 days; no false RISING
   result is emitted.
7. Empty, unknown, review, stale, and failed-source states render exactly as
   described above and never substitute zeros or silent omissions.
8. A scheduled-compatible weekly/monthly path refreshes dashboard artifacts,
   preserves credential isolation, and writes a useful non-secret result log.
9. End-to-end verification demonstrates the boundary from fetch/ingest through
   scoring, watchlist, report, dashboard, and a dossier-ready handoff without
   claiming the dossier is a forecast.
10. Offline module self-tests, Python compilation, `git diff --check`, and a
    secret scan pass; no immutable snapshot is rewritten by dashboard work.

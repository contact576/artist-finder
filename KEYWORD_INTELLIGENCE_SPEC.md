# Artist Search Intelligence — locked product specification

## Product decision

The operator surface is a monthly search-intelligence dashboard. Ticketing-platform
listings, room bands, stature, momentum, and the four ticketing lifecycle stages do not
drive this dashboard. Existing scouting data is preserved, but it is not mixed into the
search metrics.

The product answers three questions:

1. Which verified Indian artists are searched most in India, the USA, and Canada?
2. Which artists and genres are gaining or losing search interest month over month?
3. Which niches have new names or roster gaps that need identity review?

This remains a research tool. Search volume is an interest proxy, not ticket sales,
audience size, or a North American ticket forecast.

## Source and network contract

- Historical metrics come from Google Ads Keyword Planner.
- Every request explicitly uses `GOOGLE_SEARCH_AND_PARTNERS`.
- India, USA, and Canada are requested, stored, and displayed separately.
- USA and Canada are never summed into a North America value.
- The dashboard shows the number returned by Google and labels it **Google-estimated
  monthly searches**. Google describes these volumes as approximate and can consolidate
  near-exact variants.
- Search Partners can include eligible YouTube search results and Watch-page inventory.
  The metric is not all YouTube searches, views, or activity across every Google channel.
- The requested network, language, geo, Google month, fetch time, mapped keyword,
  close variants, and mapping state are retained with every result.

## Artist and niche registry

The system of record is a durable artist registry, not a temporary list derived from
events. Every artist has:

- stable ID and canonical display name;
- aliases and one approved measurement keyword;
- primary genre and optional niche tags;
- lifecycle state: `candidate`, `verified`, `inactive`, `merged`, or `rejected`;
- identity evidence URL, evidence date, and review note;
- contamination status and the reason for a qualified keyword when one is required;
- first-seen, verified-at, and last-reviewed dates.

The niche registry is editable. Initial families include stand-up comedy, poetry/spoken
word, magic/variety, mainstream music, independent music, hip-hop/rap, Punjabi music,
devotional/bhajan, Bhajan Jamming, classical, ghazal/qawwali, and theatre/production.
Niches can be added without changing scoring code.

### Monthly roster refresh

1. Seed each niche with verified artists and niche phrases.
2. Ask Keyword Planner for related keyword ideas for those seeds. These ideas expose
   topics and search language; they are not assumed to be artist names.
3. Run a dated, evidence-backed web roster sweep for each niche. Compare names found
   in current public sources with the registry and place new names in the candidate
   inbox. Human tips may enter the same inbox at any time.
4. Compare Keyword Planner ideas with the roster to identify niche gaps and useful
   qualified queries, while keeping generic topic phrases out of the artist registry.
5. Reject generic phrases, venues, songs, productions, and ambiguous names. Verify a
   real performer and genre from dated public evidence before activating the name.
6. Merge aliases into one stable artist record. Never create separate artists for
   spelling, spacing, or tour-title variants.
7. Retain inactive and rejected records so they are not rediscovered every month.
8. Show coverage health by niche: verified artists, new candidates, review backlog,
   rejected ideas, and last discovery run.

Keyword Planner automatically measures verified names and can prioritize unverified
candidates, but it cannot by itself discover a complete artist roster. The web sweep is
the name-discovery layer and requires dated source evidence before promotion. This process
is designed to keep the roster current without claiming that it contains every artist in
the world. The honest claim is: every artist included in verified totals is identified,
verified, and traceable.

## Keyword measurement rule

Each artist contributes exactly one approved keyword to rankings and genre totals.

- Use the canonical name when it uniquely identifies the artist.
- If it is contaminated or generic, use a reviewed qualified query such as
  `name comedian` and label that choice visibly.
- Store aliases and returned close variants for audit, but do not add their volumes.
- If Google maps more than one requested name to the same result or returns an ambiguous
  close variant, hold it out for review rather than silently duplicate or assign volume.
- A zero returned by Google is different from a missing, ambiguous, or failed result.

## Metrics

All metrics are calculated independently for the selected geo.

### Primary artist metrics

- **Latest monthly searches:** most recent Google month for the approved keyword.
- **Absolute monthly change:** latest month minus prior month.
- **MoM change:** `(latest - prior) / prior`, unavailable when the prior month is zero
  or absent.
- **3-month average:** mean of the latest three available months.
- **12-month average:** mean of the latest twelve available months.
- **YoY change:** latest month versus the same calendar month one year earlier, only
  when both months exist.
- **Rank:** ordinal rank within the selected geo and current genre filter.

Risers are shown in two lists: largest absolute gains and largest percentage gains.
The percentage list uses a configurable minimum prior-month volume so tiny bases do not
dominate.

### Genre metrics

- **Tracked-roster volume:** sum of one approved keyword per verified artist in that
  genre and geo.
- **Median artist volume:** the midpoint verified artist, resistant to one superstar.
- **Genre MoM:** change in the tracked-roster total over two comparable months.
- **Top-five share:** share of tracked-roster volume held by the five largest artists.
- **Coverage:** verified names, active measured names, and review backlog.

Tracked-roster volume is not unique people and is sensitive to roster coverage. It is
always displayed beside coverage, never as an absolute market-size estimate.

## Dashboard

### Global controls

- market toggle: **India | USA | Canada**;
- Google month selector;
- genre/niche filter;
- artist status filter;
- search by canonical name or alias.

### Overview

- latest available Google month and last successful refresh;
- verified and measured artist counts;
- top genres by tracked-roster volume and median artist volume;
- biggest absolute gainers and percentage gainers;
- new candidates and items needing review;
- clear `Google Search + Partners` network badge and proxy caveat.

### Genres

A sortable genre table and chart with tracked-roster volume, median artist volume,
MoM, 12-month trend, top artist, top-five share, verified roster count, and backlog.

### Artists

A sortable table with artist, genre/niche, approved keyword, latest monthly searches,
absolute change, MoM, 3-month average, 12-month average, YoY, rank, 12-month sparkline,
identity status, mapping quality, and last updated date.

### Artist detail

The artist page shows separate India, USA, and Canada panels; the monthly series;
approved keyword and variants; genre evidence; identity evidence; contamination notes;
and data freshness. The three geographies may be compared visually but are never added.

### Roster and data health

- candidate review queue, newly verified names, merges, rejections, and niche gaps;
- latest discovery and metrics run by geo;
- mapped, missing, ambiguous, consolidated, and failed keyword counts;
- credential presence only, never credential values;
- scheduled-task result and log path;
- explicit unknown, unavailable, and stale states.

## Monthly operating flow

1. Refresh niche keyword ideas and build the candidate inbox.
2. Verify or reject candidates; approved names join the durable registry.
3. Pull historical metrics for every verified artist and review candidate in India,
   USA, and Canada with Search + Partners enabled. Candidates remain visibly unverified
   and do not enter genre totals until approved.
4. Validate keyword mapping, close variants, missing results, and account bucketing.
5. Append an immutable monthly measurement record and rebuild derived metrics.
6. Generate the private local dashboard and record automation status.
7. Review risers, genre movement, candidates, and data-health warnings.

## Definition of done for the rebuild

- India, USA, and Canada toggles show separate data and never produce a combined value.
- Search + Partners is explicitly requested and visible in source metadata.
- Fixture and live validation prove correct monthly, MoM, 3-month, 12-month, and YoY
  calculations, including zero and missing states.
- One approved keyword per artist prevents variant double counting.
- Candidate discovery, identity review, alias merge, rejection, and inactivity are
  reproducible and retain provenance.
- Dashboard tables, charts, filters, search, detail views, responsive layout, and empty
  states pass browser verification with no runtime errors.
- The monthly scheduled path refreshes metrics and the dashboard, produces useful logs,
  and exposes no credentials.
- Operator documentation explains exactly where to open the dashboard and how to review
  new names.

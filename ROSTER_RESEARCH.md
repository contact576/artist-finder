# Curated roster research — 2026-08-21

## Scope and status

`data/artist_roster.json` now has two deliberately separate populations:

- `research_state: public_identity_review` / `status: verified` — an individual public source was reviewed. These alone contribute to verified genre totals.
- `research_state: directory_candidate` / `status: candidate` — a canonical name was added from an official festival, organiser, or industry-directory sweep. It is suitable for review and optional keyword measurement, but is **not** proof of identity, genre, availability, or demand.

Legacy crawler/watchlist rows are retained as history with `research_state: legacy_unreviewed` and `curated: false`; they are not the researched market map. This prevents scraped event titles and generic phrases from being presented as artists without deleting prior evidence.

## Source hierarchy

1. Individual official artist/act/label pages and named primary ticketing profiles — may support `verified` after a dated human review.
2. Official festival, venue, government, or credible industry-directory pages — support only `directory_candidate` unless an individual page is reviewed.
3. Keyword Planner ideas, legacy crawl names, social posts, and generic event labels — discovery only.

Current collection-level evidence retained in `data/niches.json`:

- [NH7 Weekender 2026](https://nh7.in/) — current named music and comedy lineup.
- [Jahan-e-Khusrau, Ministry of Tourism](https://utsav.gov.in/public/view-event/muzaffar-alis-jahan-e-khusrau-2) — dated 2025 Sufi, qawwali, folk, and poetry performers.
- [SIFAS Festival of Arts 2026](https://www.sifas.org/festival26.html) — detailed dated classical-music programme and artists.
- [EEMA Artiste Collective 2025](https://eemaindia.com/pdf/final-eema-artiste-deck-july-2025.pdf) — industry dossier; used as candidate-level discovery, not a verification claim.
- [Indian Magicians Directory](https://www.indianmagicians.com/magiciansdirectory/index.php) — directory-level magic discovery only; retrieval should be refreshed before any promotion.

## Deliberate evidence ceilings

The magic/mentalism list is held at 25 directory candidates: the public directory was unavailable during the review, so no claim of current completeness is made. Theatre/musicals contains productions, companies, and stage practitioners as candidates; `Rajadhiraaj` remains explicitly a production rather than a verified artist. Names not supported by an individual source must stay candidate even when familiar.

## Review queue rules

Before promotion, verify the exact canonical name and category on a dated public artist, label, venue, or primary ticketing page; retain aliases; then set `status: verified`, `research_state: public_identity_review`, and the individual source URL. USA and Canada keyword results remain distinct once measurement is run.

# Roster research — 8 September 2026

## Integrated review outcome

The sections below retain the first research pass, not the final release count. Parent verification and the additional major-artist review produced 48 reviewed identities: 34 with qualifying India performance evidence, two explicit international exceptions, eight archived deceased/memorial records, and four held for stronger evidence. The full 1,190-record registry remains preserved; unreviewed names are not treated as non-performers. Sources and accepted decisions are in the dated `reviewed_changes_2026-09-08_*.json` manifests. Later checks confirmed KR$NA's performance through a Getty editorial caption, Atif's through a post-event Toronto report, and Rahat's through an APP post-event Karachi report. These establish performance, not ticket sales or North American draw.

## Direct answer

The roster is materially under-covered in current Indian hip-hop and has five confirmed deceased people still eligible-looking as candidates. The biggest actionable omissions are Hanumankind, Raftaar, KING/King Rocco, Prabh Deep, KR$NA, Dhanji, Chaar Diwaari, Yashraj, and the rapper Paradox. They should enter the review inbox as candidates, not be automatically counted in verified totals.

The local listing ledger is not evidence that a listed artist performed. I searched it read-only only to understand coverage: zero matches do not mean no shows; matches do not override the independently sourced performed-event records below.

The machine-readable review is [data/research/2026-09-08_artist_review.json](data/research/2026-09-08_artist_review.json). It has a dated record, source URL, event date/city, source class, identity/contamination note, and recommended action for every item.

## High-priority missing adds

| Artist | Performed evidence in window | Why it belongs in review | Search rule |
|---|---|---|---|
| Hanumankind | Royal Enfield MotoVerse, Vagator, Goa — 21 Nov 2025 (date inferred from the 23 Nov report's Friday-night wording) | Distinct Indian rapper and clear current stage evidence | `Hanumankind` |
| Raftaar | Lollapalooza India, Mumbai — 9 Mar 2025 | Getty editorial caption says he performed | `Raftaar rapper` |
| KING / King Rocco | YouTube Fanfest India, Mumbai — 1 Aug 2024 | Official post-event video | never bare `King`; use `King Rocco` or `King rapper India` |
| Prabh Deep | Jameson Connects, Pune — 31 Mar 2024 | Promoter post-event recap | `Prabh Deep` |
| KR$NA | Lollapalooza India, Mumbai — 9 Mar 2025 | Past-concert/setlist records; spot-check primary evidence first | `KR$NA rapper` |
| Dhanji | Lollapalooza India, Mumbai — 8 Mar 2025 | Official Lollapalooza post-event video confirms 2025 performance; date mapped from lineup | `Dhanji rapper` |
| Chaar Diwaari | Zomaland, Mumbai — 16 Feb 2025 | Post-event industry recap | `Chaar Diwaari` |
| Yashraj | G-Eazy India Tour, Mumbai — 15 Feb 2024 | Two post-event reports name him as support | `Yashraj rapper` |
| Paradox | Chandigarh college gig — Sep 2024 (day not published) | Artist interview; do not merge with existing `paradox-punch` | `Paradox rapper` |

Additional event-backed discovery candidates: T.ill Apes, Frizzell D'Souza, GMinxr, Panjabi MC and Shreya Priyam Roy. `Derric & Nida` should remain a billed-duo research lead: neither single given name is safe to promote separately.

## Existing records worth moving through normal review

Seedhe Maut, Karan Aujla, SickFlip, Anyasa, Anuv Jain, Zaeden, and The Yellow Diary each have qualifying performed-event evidence. This only establishes a real recent stage appearance, not North American ticket demand or a right to merge the existing near-duplicates.

Specific correctness issues:

- `divine` has a 16 Feb 2025 Mumbai performance record, but the existing candidate is classified `punjabi_music`. The source identifies the billed act only; complete a normal identity check before changing the genre. Do not measure bare `Divine`.
- `anuv-jain` and `anuv-jain-live` need a merge/role decision; no punctuation-normalized duplicate scan catches this semantic duplicate.
- `the-yellow-diary` and `yellow-diary` likewise need a merge/identity decision.
- The existing `paradox-punch` is not evidence of the rapper Paradox. Keep them separate until human identity evidence connects them, if it ever does.

## Required exclusions

Exclude these immediately from measurement/event scoring after the parent verifies the cited sources: `kk`, `remembering-kk`, `sidhu-moose-wala`, `pankaj-udhas`, `prabha-atre`, and `rahat-indori`. Their deaths are source-backed in the JSON. The roster does not currently contain Lata Mangeshkar or Kishore Kumar as exact canonical names, so no action was proposed for them.

## Pakistani exceptions and Chahat

Atif Aslam and Rahat Fateh Ali Khan meet the requested *exception* path only provisionally: both have recent international stage evidence, and both have India-linked/Bollywood repertoire. This is not ticket-demand proof. For Atif, the historical Scotiabank Arena page plus a Ticketmaster attendee record still merits a promoter/venue post-event-media spot-check before a high-confidence status change. Rahat's Live Nation past setlist is stronger than an announcement but should receive the same spot-check.

`Chahat` cannot be added or measured. Current results collide with Chahat Fateh Ali Khan, Chahat Vig, Chahat Kakkar, tracks, and generic uses. No uniquely attributable qualifying performance was found. The correct action is to retain the ambiguity until the intended person's full name or a primary artist/promoter URL is supplied.

## Full-roster risk snapshot

Read-only count at review time: 1,174 records; 46 verified and 1,128 candidates. 461 are `legacy_unreviewed`, 664 `directory_candidate`, and 1,128 keywords are still pending review. 1,173 records have `contamination_status: unchecked`; 462 have no `entity_type`. These are review-capacity and measurement-quality risks, not evidence that those acts lack recent performances.

No raw audience, sales, or ticket claims are made here. The cited event recaps prove a stage appearance where stated; announcements and future listings were not used as performance evidence.

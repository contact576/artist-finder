# Booking shortlist — operating guide

Open the existing protected site: https://artist-search-intelligence.vercel.app/
Local operation remains at http://127.0.0.1:8765/.

## What the lists mean

- **Shortlist:** confirmed identity plus source-linked evidence of an actually performed
  India event in the rolling last three calendar years. This is eligibility for research,
  not a recommendation to book or a prediction of ticket sales.
- **Review:** missing, ambiguous, expired or insufficient evidence. This does not mean the
  artist has never performed. An exact performance date is required; month-only accounts
  remain review items until resolved.
- **Exceptions:** Atif Aslam and Rahat Fateh Ali Khan only, explicitly requested by the
  operator, with recent source-backed foreign performance evidence.
- **Archived:** reversibly removed from active measurement; history is retained. A Restore
  action restores the roster state, not eligibility for someone confirmed deceased.

Favorites are independent of eligibility and identity filters so a saved artist remains
findable. The star updates immediately, indicates pending persistence, and reports failures.
Favorites do not influence analytic totals. Add artist creates a review candidate; it does
not certify their identity or stage activity. Edit opens identity/category/keyword controls.
Remove archives without erasing measurement history. Reopen Archived to restore.

## Search-quality rules

Booking evidence and search reliability are separate. Generic terms such as King, Divine,
Raftaar, Badshah and Paradox require reviewed artist-specific queries. A qualified query is
narrower than the bare name and is not a comparable estimate of the whole artist audience.
No derived search-share percentage is claimed without measuring that comparison.

Totals, gainers and genre charts require verified identity, eligible/exception status,
current query review and valid mapping in the individual market. Changing a keyword holds
the old query's history out; it cannot be relabelled under the new term. Raw history remains
retained. Google estimates include music/news curiosity, not just concert intent.

## Research and maintenance

The evidence source of truth is data/artist_reviews.json. Dated research packs and explicit
change manifests are in data/research/. Each decision retains URLs, review date, event date,
country, city and performed/announced/uncertain status. A year-old review expires and needs
rechecking. Unknown performance or attendance is never treated as zero.

The initial September pass is a targeted review, not a completed audit of every artist.
Unreviewed names stay out of the default shortlist and analytic totals. A supported stage
appearance is not proof of sustained touring, headline draw, fee affordability, or US/Canada
demand. Prefer multiple dated headline shows and independently verified sales/venue evidence
before advancing an early-mover dossier. Search-only gainers remain research leads.

Google measurements run in GitHub Actions on the first of each month at 04:30 UTC.
The separate Codex **Monthly artist shortlist research** automation runs on the second at
10:00 local time, rotates through the backlog, checks emerging acts and updates this task
only for meaningful findings or failures. It depends on the Codex automation host being
available. Email delivery is not configured. Controversy-driven attention requires dated
credible reporting and must never be presented as ticket demand.

Quota errors now retry with bounded waits. If keyword discovery still fails, existing-artist
measurement continues and the hosted status reports discovery as incomplete. Authentication
and measurement failures still fail the job; they do not publish fabricated zeros.

## Safe research imports and release

Use tools/import_booking_reviews.py MANIFEST for a dry run and add --write only for a
reviewed plan. Existing rows require expected_current for name/category/keyword/status.
An already applied manifest is a no-op, preserving later operator edits. Aliases and other
operator metadata cannot be overwritten by the import. New evidence retains previous
identity evidence. Do not reuse a manifest ID to revise a decision.

Run module self-tests, tools/test_booking_import.py and tools/test_planner_retry.py, then
build and browser-test the dashboard. Publish both roster and sidecar together in one Git
commit; never deploy an interrupted import. Preserve existing protected login/CSRF and
GitHub-backed favorites. Never include credentials, raw crawls or local test state in a
deployment.

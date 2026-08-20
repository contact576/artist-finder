# End-to-end demo evidence

Run the deterministic, isolated fixture from the repository root:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& 'C:\Users\dapat\AppData\Local\Programs\Python\Python312\python.exe' tools\demo_e2e.py
```

The script is explicitly **FIXTURE/DEMO ONLY**. It creates a temporary data
root, does not crawl a live source, and never writes a dossier to the sibling
Artist Tour Engine. It verifies three immutable fixture snapshots spanning at
least 21 days, then uses the real ingest, ledger, signals, score, lifecycle,
watchlist, report, and static-dashboard artifact APIs.

The manager records the separate live, read-only demonstration output below
after it is run. Do not replace this fixture evidence with a claim about live
trajectory unless the live store itself has three crawls spanning 21 days.

## Recorded acceptance run — 2026-08-21

### Live retained-data path

The scheduled-compatible command completed successfully from 03:42:21 to
03:44:07 Asia/Kolkata:

```powershell
python tools\run_automation.py weekly --date 2026-08-21
```

- Fetch produced 1,101 listings: AllEvents 712, HighApe 288, District 101.
  BookMyShow was unconfigured and skipped without erasing retained evidence.
- Ingest accepted/reviewed AllEvents 494/218, HighApe 174/114, and District
  68/33. The run banked the 2026-08-21 immutable snapshot beside 2026-08-19.
- The initial integrated run reported 816 shows, 708 entities, two crawls over
  two days, and zero RISING records. The final correction overlay was then
  applied with `run_weekly.py --recompute --no-save`, which changed no snapshot
  and deterministically produced 801 shows and 687 entities.
- Snapshot SHA-256 remained
  `00ED2EC07828F7579EEDB6FBEF93CC6224A55377EDE416010846F796571CE4F1`
  for 2026-08-19 and
  `9D991BC9CDD43317E6A152B7B9FDD9896B52A9E0BF13F65433435815D2EE226D`
  for 2026-08-21 through final recompute and demo checks.
- Live trajectory is correctly unavailable: two crawls over two days is below
  the required three crawls spanning 21 days. India momentum is Unknown for
  every live candidate; it is not treated as zero or flat.
- The live dashboard contains 513 eligible artists: 494 Discovered, 19
  Major-platform confirmed, zero Diaspora validated, and zero Forecast ready.
  Twenty records are held for explicit review. Stature, India momentum, and
  diaspora `export_signal` remain separate fields.
- The weekly path invoked the designed `--write-dossiers` boundary. With zero
  RISING records, it wrote no Artist Tour Engine dossier and emitted no
  forecast.

### Isolated trajectory fixture

`python tools\demo_e2e.py` completed in a temporary directory and proved the
otherwise-unobservable trajectory path without touching live stores:

- Three immutable snapshots spanning 30 days; trajectory observable.
- Five raw fixture listings accepted and zero held for parse review.
- Separate scores: stature 71, India momentum 31, diaspora `export_signal` 49.
- Lifecycle reached Forecast ready through attested District evidence.
- US search 1,800 and Canada search 900 remained separate; representative
  display was US 1,800 under the stronger-geography rule, never a sum.
- Report and five dashboard artifacts were generated in temporary state. The
  dossier was previewed only; no sibling repository write occurred.

### Dashboard browser verification

The generated live artifact at `out/dashboard/index.html` was served by
`tools/dashboard_server.py` on `127.0.0.1` only and verified in Chromium:

- `/` returned HTTP 200; a raw encoded traversal request returned HTTP 403.
- Desktop load showed 513/513 records; search for Rajat Sood returned one row;
  the Major-platform confirmed filter returned exactly 19 rows.
- Artist detail opened without console or page errors. It showed India
  momentum as Unknown, kept US 720 and Canada 70 separate, and labelled both
  numeric search values unvalidated because the query identity failed the
  contamination gate.
- BookMyShow displayed Unknown fetched/accepted/review metrics and
  `Requires Route`, never zero evidence.
- At 390 × 844 the document width remained 390, filters and dialog stayed
  inside the viewport, and the wide evidence table was locally scrollable.

### Honest blocks and retry path

- Live BookMyShow full-grid coverage remains blocked until an Apify/browser run
  supplies a dataset or the optional local token is configured. A completed
  dataset can be imported without a token using
  `python tools\fetch_listings.py --apify-dataset <DATASET_ID> --source bookmyshow --date YYYY-MM-DD`.
- Townscript is postponed and disabled. Long-tail sources remain discovery-only.
- Live trajectory is awaiting calendar history; the fixture result is not live
  evidence and is never displayed as such.

## Required recorded fields

- Command and date run.
- Fetch/ingest source outcomes and exact snapshot date(s).
- Crawl count/span and trajectory observability state.
- Separate India-side momentum and diaspora-side `export_signal` values.
- Separate US and Canada values; if one display value is used, its stronger
  geography must be named. They must never be summed.
- Watchlist/report/dashboard artifact path and browser verification result.
- Dossier boundary result, including whether any sibling write occurred.
- Any blocked primary route, particularly BookMyShow, with the safe retry path.

No record in this file may contain a North American ticket count or present a
fixture result as live evidence.

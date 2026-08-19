"""The weekly run. One command, start to finish.

THE HANDOFF THAT MAKES THIS TESTABLE.
Python cannot call an MCP tool, so it cannot crawl. The split is therefore strict:

    Claude (the `artist-scout` skill)  fetches, and drops raw listings into
                                       data/raw/<date>__<source>.json
    this file                          ingests, computes, scores, reports

Everything below the fetch line is deterministic and runs offline, which is why every module
here has a self-test that needs no network. It also means a failed crawl degrades to "no new
data this week" rather than a broken pipeline, and a fixed crawl can be re-ingested for the
same date without corrupting anything — ingest() replaces a source's slice of the day.

TWO CADENCES, DELIBERATELY DIFFERENT.
This job is weekly, because room escalation and added nights change week to week. Search volume
is NOT fetched here: Google refreshes Keyword Planner monthly, so tools/fetch_search_volume.py
runs monthly and this job simply reads what it stored. Matching the poll to the instrument is
the difference between a real trend and four copies of the same number.

    python run_weekly.py                 ingest today's raw files, score, write the report
    python run_weekly.py --date 2026-08-19
    python run_weekly.py --no-save       compute and print, write nothing
    python run_weekly.py --recompute     skip ingest, just rebuild from existing snapshots
    python run_weekly.py --tips "A, B"   add tipped names before scoring
    python run_weekly.py --international check foreign dates for RISING entities
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge         # noqa: E402
import demand         # noqa: E402
import international  # noqa: E402
import model          # noqa: E402
import report         # noqa: E402
import score          # noqa: E402
import signals        # noqa: E402
import snapshot       # noqa: E402
import tips           # noqa: E402
import watchlist      # noqa: E402

RAW = os.path.join(model.BASE, 'data', 'raw')


def ingest_raw(date):
    """Pull in every data/raw/<date>__<source>.json the fetch step left behind."""
    os.makedirs(RAW, exist_ok=True)
    health, domains = [], set()
    for path in sorted(glob.glob(os.path.join(RAW, f'{date}__*.json'))):
        src = os.path.basename(path)[len(date) + 2:-5]
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
        rows = payload.get('events', payload) if isinstance(payload, dict) else payload
        health.append(snapshot.ingest(rows, src, date))
        if isinstance(payload, dict):
            domains |= set(payload.get('domains_seen') or [])
    new_domains = snapshot.note_platform_candidates(domains, date) if domains else []
    return health, new_domains


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--no-save', action='store_true')
    ap.add_argument('--recompute', action='store_true')
    ap.add_argument('--tips', help='comma-separated names tipped by the team this week')
    ap.add_argument('--tips-by', help='who flagged them')
    ap.add_argument('--international', action='store_true',
                    help='check foreign tour dates for RISING entities (needs a bandsintown app_id)')
    ap.add_argument('--write-dossiers', action='store_true',
                    help='create/top up Artist Tour Engine dossiers for RISING artists')
    a = ap.parse_args(argv)

    print(f'Artist Scout — India · run {a.date}')
    print('=' * 66)

    if a.tips:
        added = tips.add([n.strip() for n in a.tips.split(',')], by=a.tips_by, on=a.date)
        print(f'  tips: {len(added)} new name(s) added')

    health, new_domains = ([], [])
    if not a.recompute:
        health, new_domains = ingest_raw(a.date)
        if not health:
            print(f'\n  no raw files matched data/raw/{a.date}__*.json')
            print('  (the fetch step writes them; use --recompute to score existing snapshots)')
        for h in health:
            flag = '  <-- LOW' if h['parse_rate'] < 0.6 else ''
            print(f"  ingest {h['source']:14} raw={h['raw']:4}  kept={h['kept']:4}  "
                  f"review={h['review']:3}  parse={int(h['parse_rate']*100):3}%{flag}")
        if new_domains:
            print(f'  new ticketing domains: {", ".join(new_domains)}')

    led = snapshot.rebuild_ledger()
    obs = signals.observability(led)
    print(f"\n  ledger: {len(led['shows'])} shows · {obs['n_snapshots']} crawls · "
          f"{obs['span_days']}d span · "
          f"trajectory={'LIVE' if obs['trajectory_observable'] else 'NOT YET'}")
    if not obs['trajectory_observable']:
        print(f'  {obs["why"]}')

    sigs = signals.for_all(led, asof=a.date)
    if not sigs:
        print('\n  no entities in the ledger yet — nothing to score.')
        return 0

    entities = snapshot.by_entity(led)
    tip_matches = tips.match_against_ledger(entities)
    for m in tip_matches:
        if m['match'] and not a.no_save:
            tips.resolve(m['tip']['id'], 'matched', m['match'], 'already in the ledger')
    if tip_matches:
        print(f"  tips: {sum(1 for m in tip_matches if m['match'])}/{len(tip_matches)} "
              f"matched to tracked entities")

    dem = demand.load()
    ranked = score.rank(sigs, dem=dem)
    bylevel = {s['slug']: s['level'] for s in sigs}
    for r in ranked:
        r['_level'] = bylevel[r['slug']]

    # A tipped entity is researched regardless of what it scored. A human noticing somebody is
    # evidence that arrives before any supply-side signal, and filtering it through a heuristic
    # would discard the earliest thing we have.
    tipped = tips.tipped_slugs()
    shortlist = [r for r in ranked
                 if r['quadrant'] == 'RISING' or r['slug'] in tipped]

    deltas = watchlist.diff(ranked, asof=a.date)
    print(f"\n  {len(ranked)} entities · new={len(deltas['new'])} "
          f"moved={len(deltas['moved'])} surging={len(deltas['momentum_jump'])} "
          f"cooling={len(deltas['momentum_drop'])}")
    rising = [r for r in ranked if r['quadrant'] == 'RISING']
    print(f'  RISING: {len(rising)}' + (f" — {', '.join(r['name'] for r in rising[:6])}"
                                        if rising else ''))
    tip_only = [r for r in shortlist if r['quadrant'] != 'RISING']
    if tip_only:
        print(f"  tipped but not RISING (researched anyway): "
              f"{', '.join(r['name'] for r in tip_only[:6])}")

    if a.international and shortlist:
        print('\n  international check:')
        for r in shortlist:
            res = international.check(r['slug'], r['name'], asof=a.date)
            print(f"    {r['name'][:26]:28} {res['note']}  [{res['api_note']}]")
        dem = demand.load()
        ranked = score.rank(sigs, dem=dem)
        for r in ranked:
            r['_level'] = bylevel[r['slug']]

    snap = (snapshot.load_snapshot(a.date)
            if a.date in snapshot.snapshot_dates() else {})
    text = report.render(ranked, deltas, obs,
                         platform_candidates=snap.get('platform_candidates'),
                         health=health, asof=a.date,
                         tips_report=tips.loop_report(), tip_matches=tip_matches)
    path = report.write(text, a.date)
    print(f'\n  report -> {path}')

    if a.write_dossiers and shortlist:
        print('\n  dossiers:')
        for r in shortlist:
            sig = next(s for s in sigs if s['slug'] == r['slug'])
            p, action, changed = bridge.upsert(r, sig, asof=a.date)
            print(f"    {r['name'][:24]:26} {action:24} {', '.join(changed) or 'no change'}")
        artists = [r for r in shortlist if r.get('kind') in (None, 'artist', 'dj_night')]
        if artists:
            print('\n  next, per shortlisted artist:')
            print(f'    {bridge.screen_command(artists[0]["slug"])}')

    if a.no_save:
        print('\n  --no-save: watchlist not written.')
    else:
        watchlist.save(watchlist.apply(ranked, asof=a.date))
        print(f'  watchlist -> {watchlist.PATH}')

    missing_vol = [r for r in rising if not r['export']['has_search']]
    if missing_vol:
        print(f'\n  {len(missing_vol)} RISING entit(ies) have no search volume yet. '
              f'Run the MONTHLY job:')
        print('    cd ../tools && python fetch_search_volume.py --all')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

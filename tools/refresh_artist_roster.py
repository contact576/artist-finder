"""Seed and refresh the monthly artist/niche registry.

The live discovery step uses Google Ads Keyword Planner related ideas for each
configured niche. It never promotes an idea automatically: songs, genres,
venues, and generic phrases remain in ``data/artist_candidates.json`` until a
dated identity review approves a real performer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
SCOUT = os.path.join(BASE, 'scout')
if SCOUT not in sys.path:
    sys.path.insert(0, SCOUT)

import roster  # noqa: E402
import fetch_search_volume as planner  # noqa: E402


def seed() -> dict:
    _, result = roster.seed_from_watchlist()
    print(f'  roster seed: {result["added"]} added · {result["verified"]} verified · '
          f'{result["retained"]} retained · {result["total"]} total')
    return result


def discover(niche_id: str | None = None, limit_ideas: int = 200) -> dict:
    niches = [row for row in roster.load_niches()
              if not row.get('ui_only') and row.get('seed_keywords')
              and (niche_id is None or row['id'] == niche_id)]
    if niche_id and not niches:
        raise SystemExit(f'unknown niche: {niche_id}')
    cfg = planner.load_config()
    if not cfg.get('developer_token'):
        raise SystemExit('config google_ads.developer_token is missing')
    token = planner.access_token(cfg)
    totals = dict(niches=0, returned=0, added=0, updated=0)
    for index, niche in enumerate(niches):
        if index:
            time.sleep(1.1)
        raw = planner.keyword_ideas(cfg, token, niche.get('seed_keywords') or [],
                                    planner.GEO['in'], page_size=max(1, limit_ideas))
        rows = planner.parse_idea_rows(raw)
        rows.sort(key=lambda row: (-(row.get('avg_monthly_searches') or 0), row['text'].casefold()))
        rows = rows[:limit_ideas]
        _, result = roster.add_discovery_candidates(niche['id'], rows)
        print(f'  {niche["name"]}: {len(rows)} ideas · {result["added"]} new · '
              f'{result["updated"]} refreshed')
        totals['niches'] += 1
        totals['returned'] += len(rows)
        totals['added'] += result['added']
        totals['updated'] += result['updated']
    return totals


def reject(candidate_id: str, note: str) -> dict:
    inbox = roster.load_candidates()
    row = inbox['candidates'].get(candidate_id)
    if not row:
        raise SystemExit(f'unknown candidate: {candidate_id}')
    row.update(state='rejected', review_note=note,
               reviewed_at=dt.date.today().isoformat())
    roster.save_candidates(inbox)
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Refresh the durable artist and niche roster.')
    parser.add_argument('--seed', action='store_true',
                        help='merge eligible legacy names without auto-verifying them')
    parser.add_argument('--discover', action='store_true',
                        help='fetch India keyword ideas into the review inbox')
    parser.add_argument('--niche', help='limit discovery to one niche id')
    parser.add_argument('--limit-ideas', type=int, default=200,
                        help='maximum related ideas retained per niche (default 200)')
    parser.add_argument('--approve-candidate')
    parser.add_argument('--reject-candidate')
    parser.add_argument('--name')
    parser.add_argument('--genre')
    parser.add_argument('--evidence-url')
    parser.add_argument('--keyword', help='approved measurement keyword; defaults to canonical name')
    parser.add_argument('--note', help='required reason when rejecting a candidate')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        return roster._selftest()
    if args.limit_ideas < 1 or args.limit_ideas > 1000:
        parser.error('--limit-ideas must be between 1 and 1000')

    did_work = False
    if args.seed:
        seed()
        did_work = True
    if args.discover:
        totals = discover(args.niche, args.limit_ideas)
        print(f'  discovery total: {totals["niches"]} niches · {totals["returned"]} ideas · '
              f'{totals["added"]} new candidates')
        did_work = True
    if args.approve_candidate:
        if not all((args.name, args.genre, args.evidence_url)):
            parser.error('--approve-candidate requires --name, --genre, and --evidence-url')
        row = roster.approve_candidate(args.approve_candidate, args.name, args.genre,
                                       args.evidence_url, measurement_keyword=args.keyword)
        print(f'  approved -> {row["name"]} ({row["slug"]}) · keyword: '
              f'{row["measurement_keyword"]}')
        did_work = True
    if args.reject_candidate:
        if not args.note:
            parser.error('--reject-candidate requires --note')
        row = reject(args.reject_candidate, args.note)
        print(f'  rejected -> {row["text"]}')
        did_work = True
    if not did_work:
        parser.error('choose --seed, --discover, --approve-candidate, or --reject-candidate')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

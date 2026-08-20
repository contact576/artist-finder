"""Run an isolated, deterministic Artist Finder end-to-end fixture.

This is a demonstration harness, not a production crawl.  It writes only to a
``TemporaryDirectory`` and deliberately never calls ``bridge.upsert``.  The
printed trajectory, lifecycle and dossier results are therefore all labelled
FIXTURE/DEMO ONLY and cannot be mistaken for live evidence.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
BASE = HERE.parent
SCOUT = BASE / 'scout'
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

import bridge  # noqa: E402
import demand  # noqa: E402
import lifecycle  # noqa: E402
import report  # noqa: E402
import score  # noqa: E402
import signals  # noqa: E402
import snapshot  # noqa: E402
import watchlist  # noqa: E402
import build_dashboard  # noqa: E402
import dashboard  # noqa: E402


ASOF = '2026-07-01'
FIXTURE_SOURCES = dict(sources=[
    dict(key='district', domain='district.in', role='primary_validation'),
    dict(key='bookmyshow', domain='in.bookmyshow.com', role='primary_validation'),
    dict(key='allevents', domain='allevents.in', role='discovery_only'),
])
FIXTURE_IDENTITY_REVIEWS = dict(schema_version=1, reviews={
    'neel-sharma': dict(name='Neel Sharma', status='verified', reviewed_at='2026-07-01',
                         source_evidence=('FIXTURE/DEMO ONLY: District title explicitly names '
                                          'the performer in a future primary listing')),
})


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_digest(path: Path) -> dict[str, str]:
    """Read-only fingerprint used to prove the live stores were untouched."""
    if not path.exists():
        return {}
    if path.is_file():
        return {path.name: _digest(path)}
    return {
        item.relative_to(path).as_posix(): _digest(item)
        for item in sorted(path.rglob('*')) if item.is_file()
    }


def _live_fingerprint() -> dict[str, dict[str, str]]:
    paths = {
        'snapshots': BASE / 'data' / 'snapshots',
        'ledger': BASE / 'data' / 'shows.json',
        'watchlist': BASE / 'data' / 'watchlist.json',
        'demand': BASE / 'data' / 'demand.json',
        'engine_dossiers': Path(bridge.DOSSIERS),
    }
    return {label: _tree_digest(path) for label, path in paths.items()}


def _months(start_year: int, start_month: int, values: list[int]) -> list[dict[str, int]]:
    year, month = start_year, start_month
    rows = []
    for value in values:
        rows.append(dict(year=year, month=month, searches=value))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return rows


def _event(title: str, venue: str, city: str, url: str, date: str,
           status: str, category: str = 'Comedy') -> dict[str, str]:
    """A fetch-shaped row that goes through model.normalise_event via ingest()."""
    return dict(title=title, venue=venue, city=city, url=url, date=date,
                status=status, category=category)


def _fixture_runs() -> dict[str, dict[str, list[dict[str, str]]]]:
    # Three dated observations spanning 30 days.  The venue records are named
    # rooms, while scoring uses only their resulting room bands.
    return {
        '2026-06-01': {
            'allevents': [_event('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                                  'https://allevents.in/demo/neel-1', '2026-09-15', 'Available')],
        },
        '2026-06-15': {
            'allevents': [
                _event('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                       'https://allevents.in/demo/neel-1', '2026-09-15', 'Sold Out'),
                _event('Neel Sharma Live', 'That Comedy Club', 'Bengaluru',
                       'https://allevents.in/demo/neel-2', '2026-09-20', 'Available'),
            ],
        },
        '2026-07-01': {
            'district': [
                _event('Neel Sharma: The Next Chapter', 'Sophia Bhabha Hall', 'Mumbai',
                       'https://www.district.in/events/demo-neel-3', '2026-10-10', 'Available'),
                _event('Neel Sharma: The Next Chapter', 'Sophia Bhabha Hall', 'Mumbai',
                       'https://www.district.in/events/demo-neel-4', '2026-10-11', 'Available'),
            ],
        },
    }


def _assert_no_ticket_forecast(value: Any) -> None:
    """The fixture must never manufacture a NA ticket count or combined score."""
    text = json.dumps(value, ensure_ascii=False).lower()
    forbidden = ('north_american_tickets', 'na_ticket', 'ticket_forecast', 'combined_score')
    assert not any(token in text for token in forbidden), 'fixture emitted a prohibited ticket/combined field'


def _install_temp_paths(root: Path) -> dict[str, str]:
    previous = dict(
        snapdir=snapshot.SNAPDIR,
        ledger=snapshot.LEDGER,
        demand=demand.PATH,
        watchlist=watchlist.PATH,
        report_out=report.OUT,
    )
    snapshot.SNAPDIR = str(root / 'snapshots')
    snapshot.LEDGER = str(root / 'shows.json')
    demand.PATH = str(root / 'demand.json')
    watchlist.PATH = str(root / 'watchlist.json')
    report.OUT = str(root / 'reports')
    return previous


def _restore_paths(previous: dict[str, str]) -> None:
    snapshot.SNAPDIR = previous['snapdir']
    snapshot.LEDGER = previous['ledger']
    demand.PATH = previous['demand']
    watchlist.PATH = previous['watchlist']
    report.OUT = previous['report_out']


def run_demo() -> dict[str, Any]:
    """Execute all mutable steps under a temporary root and return compact evidence."""
    before = _live_fingerprint()
    with tempfile.TemporaryDirectory(prefix='artist-finder-demo-') as temp:
        root = Path(temp)
        previous = _install_temp_paths(root)
        try:
            summaries = []
            for observed_on, sources in _fixture_runs().items():
                for source, rows in sources.items():
                    summaries.append(snapshot.ingest(rows, source, observed_on))

            immutable = _tree_digest(root / 'snapshots')
            ledger = snapshot.rebuild_ledger()
            assert immutable == _tree_digest(root / 'snapshots'), 'ledger rebuild rewrote a fixture snapshot'
            assert ledger['n_snapshots'] == 3, 'fixture needs exactly three immutable crawls'

            # Deliberately distinct volumes.  The representative value is US;
            # it is selected as the stronger geography, not a US+CA sum.
            demand.record_search('neel-sharma', 'us', 1800,
                                 _months(2025, 7, [500, 560, 620, 680, 750, 840, 940,
                                                     1040, 1180, 1320, 1480, 1640, 1800]),
                                 name='Neel Sharma', source='FIXTURE keyword planner')
            demand.record_search('neel-sharma', 'ca', 900,
                                 _months(2025, 7, [300, 330, 360, 410, 450, 500, 560,
                                                     620, 690, 740, 780, 830, 900]),
                                 name='Neel Sharma', source='FIXTURE keyword planner')
            demand.record_contamination('neel-sharma', 1.0, name='Neel Sharma')
            demand.record_international('neel-sharma', [
                dict(country='United Kingdom', city='London', date='2026-10-20',
                     source='FIXTURE foreign-date')
            ], status='found', note='FIXTURE/DEMO ONLY')

            obs = signals.observability(ledger)
            assert obs['trajectory_observable'], 'three crawls spanning 30 days must be observable'
            sigs = signals.for_all(ledger, ASOF)
            ranked = score.rank(sigs, dem=demand.load())
            candidate = next(row for row in ranked if row['slug'] == 'neel-sharma')
            candidate['_level'] = next(item['level'] for item in sigs if item['slug'] == 'neel-sharma')
            assert candidate['calibrated'] is False, 'fixture cannot present momentum as calibrated'
            assert candidate['momentum'] is not candidate['export_signal'], 'scores were merged'

            lifecycle_rows = lifecycle.build(ledger, demand.load(), ASOF, FIXTURE_SOURCES,
                                             FIXTURE_IDENTITY_REVIEWS)
            lifecycle_row = next(row for row in lifecycle_rows if row['slug'] == 'neel-sharma')
            diaspora = lifecycle_row['evidence']['diaspora']
            assert lifecycle_row['primary_confirmed'], 'District fixture booking must confirm primary evidence'
            assert lifecycle_row['stage'] == 'Forecast ready', 'clean primary + separate search evidence must be ready'
            assert diaspora['us']['avg_monthly'] == 1800 and diaspora['ca']['avg_monthly'] == 900
            assert diaspora['representative']['geo'] == 'us', 'stronger geography must be labelled'
            assert diaspora['representative']['avg_monthly'] == 1800, 'US and Canada volumes must not be summed'

            deltas = watchlist.diff(ranked, wl=dict(artists={}), asof=ASOF)
            saved_watchlist = watchlist.apply(ranked, wl=dict(artists={}), asof=ASOF)
            watchlist.save(saved_watchlist)
            health = [dict(source=row['source'], raw=row['raw'], kept=row['kept'],
                           review=row['review'], parse_rate=row['parse_rate']) for row in summaries]
            digest = report.render(ranked, deltas, obs, health=health, asof=ASOF)
            report_path = Path(report.write(digest, ASOF))
            assert report_path.is_file(), 'fixture report was not written'
            assert 'Trajectory is not measurable yet' not in digest, 'observable demo emitted stale caveat'

            # This uses the same artifact writer and browser payload boundary as
            # the real dashboard, but its payload is explicitly fixture-only.
            dashboard_payload = dashboard.fixture_payload()
            artifact_dir = root / 'dashboard-fixture'
            artifacts = build_dashboard.write_artifacts(dashboard_payload, artifact_dir)
            assert (artifact_dir / 'dashboard-data.json').is_file() and len(artifacts) == 5
            assert dashboard_payload.get('mode') == 'fixture', 'dashboard fixture lost its label'

            # Construction only: no bridge.upsert call, no Artist Tour Engine write.
            dossier = bridge.new_dossier('Neel Sharma', bridge.GENRE_MAP['comedy'])
            assert dossier['search']['us_monthly'] is None and dossier['actuals'] == []
            _assert_no_ticket_forecast(dict(candidate=candidate, lifecycle=lifecycle_row,
                                            dashboard=dashboard_payload, dossier=dossier))

            evidence = dict(
                label='FIXTURE/DEMO ONLY — no live source, snapshot, or dossier write',
                snapshots=dict(count=obs['n_snapshots'], span_days=obs['span_days'],
                               immutable=True, trajectory_observable=obs['trajectory_observable']),
                ingest=dict(raw=sum(item['raw'] for item in summaries),
                            accepted=sum(item['kept'] for item in summaries),
                            review=sum(item['review'] for item in summaries)),
                scores=dict(stature=candidate['stature']['value'],
                            india_momentum=candidate['momentum']['value'],
                            diaspora_export_signal=candidate['export_signal']['value'],
                            combined_score_emitted=False, calibrated=False),
                lifecycle=dict(stage=lifecycle_row['stage'], primary_confirmed=True,
                               primary_source='district', next_action=lifecycle_row['next_action']),
                diaspora=dict(us_monthly=1800, ca_monthly=900,
                              representative_geo='us', representative_value=1800,
                              summed=False),
                outputs=dict(report=str(report_path.relative_to(root)),
                             dashboard_artifacts=len(artifacts), dossier='preview only; no sibling write'),
            )
        finally:
            _restore_paths(previous)
    after = _live_fingerprint()
    assert before == after, 'live project data, snapshots, or dossiers changed during fixture demo'
    return evidence


def main() -> int:
    try:
        evidence = run_demo()
    except (AssertionError, OSError, ValueError, KeyError, StopIteration) as exc:
        print(f'FIXTURE/DEMO FAILED: {exc}')
        return 1
    print('FIXTURE/DEMO ONLY — isolated temporary state; no live crawl or dossier write.')
    print('  immutable snapshots: {count} across {span_days} days; trajectory observable={trajectory_observable}'.format(
        **evidence['snapshots']))
    print('  ingest: {raw} raw → {accepted} accepted; {review} review'.format(**evidence['ingest']))
    print('  scores (separate): stature={stature}; India momentum={india_momentum}; diaspora export_signal={diaspora_export_signal}'.format(
        **evidence['scores']))
    print('  lifecycle: {stage} via {primary_source}; primary_confirmed={primary_confirmed}'.format(
        **evidence['lifecycle']))
    print('  diaspora: US={us_monthly}; CA={ca_monthly}; representative={representative_geo} {representative_value}; summed={summed}'.format(
        **evidence['diaspora']))
    print('  outputs: report={report}; dashboard_artifacts={dashboard_artifacts}; dossier={dossier}'.format(
        **evidence['outputs']))
    print('  all invariants pass: no North American ticket count, no merged score, no live-store mutation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

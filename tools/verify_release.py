"""Offline release gate: deterministic modules, hosted security, and retained data invariants."""
from __future__ import annotations
import collections
import json
import os
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]
MODULES = ('genres', 'model', 'snapshot', 'signals', 'demand', 'score',
           'watchlist', 'tips', 'international', 'report', 'bridge', 'roster',
           'booking_eligibility', 'search_dashboard')


def main():
    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    commands = [[sys.executable, f'scout/{name}.py'] for name in MODULES]
    commands += [
        [sys.executable, 'api/index.py', '--selftest'],
        [sys.executable, 'tools/dashboard_server.py', '--selftest'],
        [sys.executable, 'tools/build_dashboard.py', '--selftest'],
        [sys.executable, 'tools/fetch_search_volume.py', '--self-test'],
        [sys.executable, 'tools/hosted_refresh_guard.py', '--self-test'],
        [sys.executable, 'tools/test_planner_retry.py'],
        [sys.executable, 'tools/test_booking_import.py'],
    ]
    results = []
    for command in commands:
        run = subprocess.run(command, cwd=BASE, env=env, capture_output=True,
                             encoding='utf-8', errors='replace', timeout=90)
        results.append(dict(test=' '.join(command[1:]), passed=run.returncode == 0))
        if run.returncode:
            print(run.stdout[-3000:] + run.stderr[-3000:])
            print(json.dumps(results, indent=2))
            return 1
    sys.path.insert(0, str(BASE / 'scout'))
    import search_dashboard
    payload = search_dashboard.build_payload()
    counts = collections.Counter(row['booking_eligibility']['status'] for row in payload['artists'])
    for row in payload['artists']:
        if row['status'] != 'verified' or row['booking_eligibility']['status'] not in ('eligible', 'exception'):
            assert not row['summary_eligible'], row['slug']
        for geo, metrics in row['geos'].items():
            actual = ' '.join(str(metrics.get('keyword') or '').split()).casefold()
            query = ' '.join(str(row.get('measurement_keyword') or '').split()).casefold()
            if actual != query:
                assert not metrics['series'], (row['slug'], geo, 'old keyword data leaked')
    report = dict(passed=True, tests=results, artist_records=len(payload['artists']),
                  booking_status_counts=dict(counts),
                  summary_eligible=sum(row['summary_eligible'] for row in payload['artists']))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

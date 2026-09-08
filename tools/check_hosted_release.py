"""Read-only authenticated smoke check; run with `vercel env run -e production --`.

Secrets stay in memory. No password, cookie, token, response body or environment
value is printed. A signed maintenance session exercises the existing auth gate;
it does not change access settings or test the human password-entry flow.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('--vercel', action='store_true', help='Use authenticated Vercel CLI transport for a protected candidate')
    args = parser.parse_args()
    if not args.url.startswith('https://'):
        raise ValueError('HTTPS URL required')
    spec = importlib.util.spec_from_file_location('hosted_release', BASE / 'api/index.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    token, _ = app._make_session()

    def get(path, authenticated=True):
        headers = {'Accept-Encoding': 'gzip'}
        if authenticated:
            headers['Cookie'] = app.COOKIE_NAME + '=' + token
        if args.vercel:
            cli = shutil.which('vercel.cmd') or shutil.which('vercel')
            config = '\n'.join('header = ' + json.dumps(k + ': ' + v) for k, v in headers.items())
            run = subprocess.run([cli, 'curl', path, '--deployment', args.url,
                                  '--', '--silent', '--show-error', '--compressed',
                                  '--write-out', '\n%{http_code}', '--config', '-'],
                                 input=config, capture_output=True, text=True,
                                 encoding='utf-8', errors='replace', timeout=120)
            if run.returncode:
                raise RuntimeError('Protected request failed; transport details withheld')
            body, _, status = run.stdout.rpartition('\n')
            return int(status), body.encode('utf-8')
        request = Request(args.url.rstrip('/') + path, headers=headers)
        try:
            response = urlopen(request, timeout=90)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                raw = gzip.decompress(raw)
            return response.status, raw

    status, _ = get('/api/dashboard', False)
    assert status == 401, 'Unauthenticated dashboard did not require login'
    status, raw = get('/api/dashboard')
    assert status == 200, 'Authenticated dashboard failed'
    payload = json.loads(raw)
    rows = {row['slug']: row for row in payload['artists']}
    assert len(rows) >= 1190, 'Updated roster not deployed'
    assert rows['kk']['status'] == 'inactive', 'KK not archived'
    assert rows['hanumankind']['booking_eligibility']['status'] == 'eligible'
    assert rows['atif-aslam']['booking_eligibility']['status'] == 'exception'
    for row in rows.values():
        if row['status'] != 'verified' or row['booking_eligibility']['status'] not in ('eligible', 'exception'):
            assert not row['summary_eligible'], 'Unqualified artist leaked into totals'
    status, raw = get('/dashboard-data.json')
    assert status == 200, 'Static-compatible dashboard endpoint failed'
    other = {row['slug']: (row['status'], row['measurement_keyword']) for row in json.loads(raw)['artists']}
    assert other == {slug: (row['status'], row['measurement_keyword']) for slug, row in rows.items()}, 'Roster read paths disagree'
    status, raw = get('/api/v1/favorites')
    assert status == 200 and isinstance(json.loads(raw).get('favorites'), list), 'Favorites read failed'
    print(json.dumps({'passed': True, 'records': len(rows), 'booking_status_counts': dict(collections.Counter(row['booking_eligibility']['status'] for row in rows.values())), 'summary_eligible': sum(row['summary_eligible'] for row in rows.values()), 'data_health': payload.get('data_health', {})}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Never dump request objects or upstream responses containing credentials.
        print('Hosted smoke check failed: ' + (str(error) if isinstance(error, AssertionError) else type(error).__name__))
        raise SystemExit(1)

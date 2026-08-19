"""Google Keyword Planner volumes for the watchlist. MONTHLY, not weekly.

WHY THIS IS NOT PART OF THE WEEKLY RUN.
Google refreshes Keyword Planner once a month. Polling it weekly returns the same figure four
times and draws a flat line that looks like a stalled artist. The cadence has to match the
instrument: this runs monthly, the weekly job reads what it stored.

WHY THERE IS NO google-ads LIBRARY IMPORT.
The Google Ads API is REST as well as gRPC, and everything here needs is one endpoint —
`customers/{id}:generateKeywordHistoricalMetrics`. Stdlib urllib does it in forty lines, against
a `pip install google-ads` that drags in protobuf and grpcio. This project is stdlib-only for
exactly this kind of reason.

WHY THE MCC MATTERS.
Keyword Planner returns precise figures for accounts with real spend and coarse, heavily rounded
ones for accounts without. PPC Guru.ca (1632013729) has 49 live accounts, so the numbers are
usable — which is the difference between an input the Artist Tour Engine can forecast from and a
number that only looks like one. `--check` reports when the response smells bucketed.

SETUP — data/config.json, which is GITIGNORED because these are credentials:

    {"google_ads": {
       "developer_token":   "...",          apply at ads.google.com/aw/apicenter on the MCC
       "client_id":         "...",          OAuth client, Google Cloud console
       "client_secret":     "...",
       "refresh_token":     "...",          one-time OAuth consent, offline access
       "login_customer_id": "1632013729",   the MCC, digits only
       "customer_id":       "1632013729",
       "api_version":       "v21"}}

    python fetch_search_volume.py --all              every watchlist entity
    python fetch_search_volume.py --names "A,B"      specific names
    python fetch_search_volume.py --check            credentials + one probe, writes nothing
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BASE, 'scout'))

import demand     # noqa: E402
import model      # noqa: E402
import watchlist  # noqa: E402

CONFIG = os.path.join(BASE, 'data', 'config.json')
TOKEN_URL = 'https://oauth2.googleapis.com/token'
DEFAULT_VERSION = 'v21'
TIMEOUT = 45

# Google Ads geo target constants. Kept beside the currencies they imply so nobody wires the
# two together later — US and CA volumes are reported separately, always.
GEO = {'us': '2840', 'ca': '2124'}
LANG_EN = '1000'


def load_config():
    if not os.path.exists(CONFIG):
        raise SystemExit(
            f'\nNo credentials at {CONFIG}\n'
            'Create it with a "google_ads" block — see this file\'s docstring.\n'
            'The developer token is applied for at ads.google.com/aw/apicenter, signed in to\n'
            'the PPC Guru.ca manager account. Approval takes a few days.\n')
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f).get('google_ads') or {}


def _post(url, data, headers=None, form=False):
    body = (urllib.parse.urlencode(data).encode() if form
            else json.dumps(data).encode('utf-8'))
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Content-Type',
                   'application/x-www-form-urlencoded' if form else 'application/json')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:900]
        raise SystemExit(f'\nGoogle API {e.code} {e.reason}\n{detail}\n')


def access_token(cfg):
    for k in ('client_id', 'client_secret', 'refresh_token'):
        if not cfg.get(k):
            raise SystemExit(f'config google_ads.{k} is missing')
    tok = _post(TOKEN_URL, dict(client_id=cfg['client_id'],
                                client_secret=cfg['client_secret'],
                                refresh_token=cfg['refresh_token'],
                                grant_type='refresh_token'), form=True)
    return tok['access_token']


def historical_metrics(cfg, token, keywords, geo_id):
    """One call, one geo. Returns the API's keyword->metrics list."""
    ver = cfg.get('api_version', DEFAULT_VERSION)
    cid = str(cfg['customer_id']).replace('-', '')
    url = (f'https://googleads.googleapis.com/{ver}/customers/{cid}'
           f':generateKeywordHistoricalMetrics')
    headers = {'Authorization': f'Bearer {token}',
               'developer-token': cfg['developer_token']}
    if cfg.get('login_customer_id'):
        headers['login-customer-id'] = str(cfg['login_customer_id']).replace('-', '')
    payload = {
        'keywords': list(keywords),
        'geoTargetConstants': [f'geoTargetConstants/{geo_id}'],
        'language': f'languageConstants/{LANG_EN}',
        'keywordPlanNetwork': 'GOOGLE_SEARCH_AND_PARTNERS',
    }
    return _post(url, payload, headers).get('results', []) or []


def parse_metrics(result):
    """API row -> (avg_monthly, [{'year','month','searches'}] oldest first)."""
    m = result.get('keywordMetrics') or {}
    avg = m.get('avgMonthlySearches')
    avg = int(avg) if avg is not None else None
    months = {'JANUARY': 1, 'FEBRUARY': 2, 'MARCH': 3, 'APRIL': 4, 'MAY': 5, 'JUNE': 6,
              'JULY': 7, 'AUGUST': 8, 'SEPTEMBER': 9, 'OCTOBER': 10, 'NOVEMBER': 11,
              'DECEMBER': 12}
    series = []
    for v in (m.get('monthlySearchVolumes') or []):
        mo = months.get(str(v.get('month', '')).upper())
        if not mo:
            continue
        series.append(dict(year=int(v['year']), month=mo,
                           searches=int(v.get('monthlySearches') or 0)))
    series.sort(key=lambda x: (x['year'], x['month']))
    return avg, series


def looks_bucketed(series):
    """Coarse data means the API is answering from an account without real spend.

    A genuine 12-month series varies. All-identical values, or every value a round multiple of
    1,000, is the signature of a restricted response — and a forecast built on it would be
    quietly wrong rather than loudly broken.
    """
    vals = [s['searches'] for s in series if s['searches']]
    if len(vals) < 6:
        return False, 'too few months to judge'
    if len(set(vals)) == 1:
        return True, 'every month identical — restricted/bucketed response'
    if all(v % 1000 == 0 for v in vals):
        return True, 'every month a round 1,000 — restricted/bucketed response'
    return False, 'varies month to month — looks like real data'


def entity_names(args):
    if args.names:
        return [(model.slugify(n.strip()), n.strip()) for n in args.names.split(',') if n.strip()]
    wl = watchlist.load()
    out = [(s, e.get('name') or s) for s, e in (wl.get('artists') or {}).items()
           if not e.get('do_not_pursue')]
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='every watchlist entity')
    ap.add_argument('--names', help='comma-separated names instead of the watchlist')
    ap.add_argument('--check', action='store_true', help='probe credentials, write nothing')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args(argv)

    cfg = load_config()
    if not cfg.get('developer_token'):
        raise SystemExit('config google_ads.developer_token is missing — apply on the MCC first')

    print('Keyword Planner — US and Canada, reported SEPARATELY (never summed)')
    print('=' * 68)
    token = access_token(cfg)
    print(f'  auth ok · customer {cfg.get("customer_id")} · '
          f'login-customer {cfg.get("login_customer_id")} · {cfg.get("api_version", DEFAULT_VERSION)}')

    if a.check:
        res = historical_metrics(cfg, token, ['zakir khan comedian'], GEO['us'])
        if not res:
            print('  probe returned no rows — check the developer token access level')
            return 1
        avg, series = parse_metrics(res[0])
        bucketed, why = looks_bucketed(series)
        print(f'  probe "zakir khan comedian" US: avg={avg:,}/mo over {len(series)} months')
        print(f'  data quality: {"BUCKETED — " if bucketed else "OK — "}{why}')
        print('\n  --check: nothing written.')
        return 0

    targets = entity_names(a)
    if a.limit:
        targets = targets[:a.limit]
    if not targets:
        print('  nothing on the watchlist yet — run the weekly scout first.')
        return 0

    print(f'  {len(targets)} entities\n')
    flagged = 0
    for slug, name in targets:
        # The contamination gate from the tour-pnl protocol: the qualified variant tells us
        # whether the bare name's volume is even this person's. Measured failures: Jaspreet
        # Singh 2%, Aakash Gupta 1% — both look huge in search and do not sell tickets.
        kws = [name, f'{name} comedian', f'{name} tickets']
        line = f'  {name[:30]:32}'
        for geo in ('us', 'ca'):
            try:
                rows = historical_metrics(cfg, token, kws, GEO[geo])
            except SystemExit as e:
                print(f'{line} {geo.upper()} FAILED {e}')
                continue
            byk = {}
            for r in rows:
                avg, series = parse_metrics(r)
                byk[(r.get('text') or '').lower()] = (avg, series)
            bare = byk.get(name.lower(), (None, []))
            qual = byk.get(f'{name} comedian'.lower(), (None, []))
            if bare[0] is None:
                line += f' {geo.upper()}=—'
                continue
            demand.record_search(slug, geo, bare[0], bare[1], name=name,
                                 source=f'keyword planner {cfg.get("api_version", DEFAULT_VERSION)}')
            bucketed, why = looks_bucketed(bare[1])
            if bucketed:
                flagged += 1
            line += f' {geo.upper()}={bare[0]:,}/mo' + ('  [BUCKETED]' if bucketed else '')
            if geo == 'us' and bare[0] and qual[0] is not None:
                share = qual[0] / bare[0] if bare[0] else None
                demand.record_contamination(slug, round(share, 4) if share else 0.0, name=name)
                if share is not None and share < demand.CONTAMINATION_FLOOR:
                    line += f'  ⚠ only {share:.0%} qualified — name is contaminated'
        print(line)

    print(f'\n  stored -> {demand.PATH}')
    if flagged:
        print(f'  ⚠ {flagged} response(s) looked bucketed. That means the API answered from an\n'
              f'    account without real spend — check customer_id / login_customer_id.')
    print('  Growth: MoM is available now; true YoY needs ~13 months of these monthly runs.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

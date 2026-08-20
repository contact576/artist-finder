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
       "api_version":       "v25"}}

    python fetch_search_volume.py --all              every watchlist entity
    python fetch_search_volume.py --names "A,B"      specific names
    python fetch_search_volume.py --check            credentials + one probe, writes nothing
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import datetime as dt
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BASE, 'scout'))

import demand     # noqa: E402
import model      # noqa: E402
import watchlist  # noqa: E402

CONFIG = os.path.join(BASE, 'data', 'config.json')
TOKEN_URL = 'https://oauth2.googleapis.com/token'
DEFAULT_VERSION = 'v25'
TIMEOUT = 45

# Google Ads geo target constants. Kept beside the currencies they imply so nobody wires the
# two together later — US and CA volumes are reported separately, always.
GEO = {'us': '2840', 'ca': '2124'}
QUALIFIER = {
    'comedy': 'comedian',
    'music_mainstream': 'singer',
    'music_indie': 'singer',
    'music_classical': 'musician',
    'music_unspecified': 'singer',
    'devotional': 'singer',
    'spoken_word': 'poet',
    'magic_variety': 'magician',
    'edm_club': 'dj',
    'theatre': 'actor',
}

LANG_EN = '1000'
MONTH_NAMES = ('JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE',
               'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER')


def history_range(months=48, today=None):
    """The last N completed calendar months, expressed in Google Ads API enums."""
    today = today or dt.date.today()
    end_month = today.month - 1
    end_year = today.year
    if end_month == 0:
        end_month, end_year = 12, end_year - 1
    end_index = end_year * 12 + end_month - 1
    start_index = end_index - max(1, months) + 1
    sy, sm0 = divmod(start_index, 12)
    return dict(start=dict(year=sy, month=MONTH_NAMES[sm0]),
                end=dict(year=end_year, month=MONTH_NAMES[end_month - 1]))


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


def historical_metrics(cfg, token, keywords, geo_id, history_months=48):
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
        'keywordPlanNetwork': 'GOOGLE_SEARCH',
        'historicalMetricsOptions': {
            'yearMonthRange': history_range(history_months),
        },
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



def keyword_key(value):
    """Canonical comparison key for requested keywords and API text."""
    return ' '.join(str(value or '').split()).casefold()


def map_historical_metrics(requested_keywords, rows):
    """Map only requested keywords to historical-metric rows without silent overwrites.

    Google may return close variants shared by several queries. Exact returned text always wins
    for an exact request. A close variant is used only when it is the sole candidate for a
    requested key; otherwise the key is left unmapped and the collision is returned explicitly.
    """
    requested = {}
    for keyword in requested_keywords:
        key = keyword_key(keyword)
        if key:
            requested.setdefault(key, str(keyword))

    exact, variants = {key: [] for key in requested}, {key: [] for key in requested}
    for index, row in enumerate(rows or []):
        text = row.get('text')
        text_key = keyword_key(text)
        parsed = parse_metrics(row)
        candidate = dict(index=index, text=text, parsed=parsed)
        if text_key in exact:
            exact[text_key].append(candidate)
        for variant in row.get('closeVariants') or []:
            variant_key = keyword_key(variant)
            if variant_key in variants and variant_key != text_key:
                variants[variant_key].append(candidate)

    mapped, matches, collisions, missing = {}, {}, {}, []
    for key, original in requested.items():
        direct = exact[key]
        fallback = variants[key]
        if len(direct) == 1:
            candidate = direct[0]
            mapped[key] = candidate['parsed']
            matches[key] = dict(mode='exact', requested=original,
                                result_text=candidate['text'], row_index=candidate['index'])
            # Fallback rows are recorded so an API change cannot quietly replace this exact row.
            if fallback:
                collisions[key] = dict(reason='exact result retained over close-variant candidate',
                                       requested=original, result_texts=[x['text'] for x in fallback],
                                       nonblocking=True)
        elif len(direct) > 1:
            collisions[key] = dict(reason='multiple exact API rows', requested=original,
                                   result_texts=[x['text'] for x in direct], nonblocking=False)
            missing.append(key)
        elif len(fallback) == 1:
            candidate = fallback[0]
            mapped[key] = candidate['parsed']
            matches[key] = dict(mode='close_variant', requested=original,
                                result_text=candidate['text'], row_index=candidate['index'])
        elif len(fallback) > 1:
            collisions[key] = dict(reason='multiple close-variant API rows', requested=original,
                                   result_texts=[x['text'] for x in fallback], nonblocking=False)
            missing.append(key)
        else:
            missing.append(key)
    return dict(mapped=mapped, matches=matches, collisions=collisions, missing=missing)


def _selftest():
    def row(text, avg, close=()):
        return dict(text=text, closeVariants=list(close), keywordMetrics=dict(
            avgMonthlySearches=avg,
            monthlySearchVolumes=[dict(year='2026', month='JULY', monthlySearches=str(avg))]))

    exact = map_historical_metrics(['Alpha', 'Beta'], [
        row('Alpha', 10, ['Beta']), row('Beta', 20),
    ])
    fallback = map_historical_metrics(['Spelling'], [row('Speling', 30, ['Spelling'])])
    collision = map_historical_metrics(['Shared'], [
        row('First Result', 40, ['Shared']), row('Second Result', 50, ['Shared']),
    ])
    duplicate = map_historical_metrics(['Echo'], [row('Echo', 60), row('Echo', 70)])
    checks = [
        ('exact response survives a conflicting close variant',
         exact['mapped']['alpha'][0] == 10 and exact['mapped']['beta'][0] == 20 and
         exact['matches']['beta']['mode'] == 'exact'),
        ('exact-versus-close collision is explicit',
         exact['collisions']['beta']['nonblocking'] is True),
        ('one unambiguous close variant is a labelled fallback',
         fallback['mapped']['spelling'][0] == 30 and
         fallback['matches']['spelling']['mode'] == 'close_variant'),
        ('ambiguous close variants are not mapped',
         'shared' not in collision['mapped'] and 'shared' in collision['collisions']),
        ('duplicate exact results are not silently overwritten',
         'echo' not in duplicate['mapped'] and 'echo' in duplicate['collisions']),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


def entity_names(args):
    if args.names:
        return [(model.slugify(n.strip()), n.strip(), 'unknown') for n in args.names.split(',') if n.strip()]
    wl = watchlist.load()
    out = [(s, e.get('name') or s, e.get('genre') or 'unknown')
           for s, e in (wl.get('artists') or {}).items()
           if not e.get('do_not_pursue') and e.get('active', True) and e.get('candidate_eligible', True)
           and e.get('kind', 'artist') in ('artist', 'dj_night')]
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='every watchlist entity')
    ap.add_argument('--names', help='comma-separated names instead of the watchlist')
    ap.add_argument('--check', action='store_true', help='probe credentials, write nothing')
    ap.add_argument('--self-test', action='store_true',
                    help='run pure keyword-result mapping checks; no credentials or API call')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--history-months', type=int, default=48,
                    help='completed months to request (default 48; Google supports up to 4 years)')
    a = ap.parse_args(argv)
    if a.self_test:
        return _selftest()

    cfg = load_config()
    if not cfg.get('developer_token'):
        raise SystemExit('config google_ads.developer_token is missing — apply on the MCC first')

    print('Keyword Planner — US and Canada, reported SEPARATELY (never summed)')
    print('=' * 68)
    token = access_token(cfg)
    print(f'  auth ok · customer {cfg.get("customer_id")} · '
          f'login-customer {cfg.get("login_customer_id")} · {cfg.get("api_version", DEFAULT_VERSION)}')

    if a.check:
        res = historical_metrics(cfg, token, ['zakir khan comedian'], GEO['us'], a.history_months)
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
    # Google supports up to 10,000 keywords per request. Batch the whole roster instead of
    # making two calls per artist; Keyword Planning is limited to 1 request/second per CID.
    plans = []
    all_keywords = []
    for slug, name, genre in targets:
        qualifier = QUALIFIER.get(genre)
        qualified_keyword = f'{name} {qualifier}' if qualifier else None
        kws = [name, f'{name} tickets']
        kws = list(dict.fromkeys(kws + ([qualified_keyword] if qualified_keyword else [])))
        plans.append(dict(slug=slug, name=name, genre=genre, qualifier=qualifier,
                          qualified_keyword=qualified_keyword))
        all_keywords.extend(kws)
    all_keywords = list(dict.fromkeys(all_keywords))
    print(f'  {len(all_keywords)} keyword variants in geo batches\n')

    fetched, fetched_matches = {}, {}
    request_count = 0
    batch_size = 9000
    for geo in ('us', 'ca'):
        response_rows = []
        for offset in range(0, len(all_keywords), batch_size):
            if request_count:
                time.sleep(1.1)
            chunk = all_keywords[offset:offset + batch_size]
            response_rows.extend(historical_metrics(cfg, token, chunk, GEO[geo], a.history_months))
            request_count += 1
        mapping = map_historical_metrics(all_keywords, response_rows)
        fetched[geo] = mapping['mapped']
        fetched_matches[geo] = mapping['matches']
        blocking = [item for item in mapping['collisions'].values() if not item['nonblocking']]
        exact_count = sum(1 for item in mapping['matches'].values() if item['mode'] == 'exact')
        fallback_count = sum(1 for item in mapping['matches'].values()
                             if item['mode'] == 'close_variant')
        print(f'  {geo.upper()} batch: {len(mapping["mapped"])} mapped '
              f'({exact_count} exact, {fallback_count} unambiguous close variants)')
        if blocking:
            print(f'  ⚠ {geo.upper()} skipped {len(blocking)} ambiguous keyword mapping(s); '
                  'no result was silently chosen.')


    flagged = 0
    demand_data = demand.load()
    for plan in plans:
        slug = plan['slug']
        name = plan['name']
        qualifier = plan['qualifier']
        qualified_keyword = plan['qualified_keyword']
        line = f'  {name[:30]:32}'
        for geo in ('us', 'ca'):
            byk = fetched[geo]
            bare = byk.get(keyword_key(name))
            qual = byk.get(keyword_key(qualified_keyword)) if qualified_keyword else None
            if bare is None:
                line += f' {geo.upper()}=—'
                continue
            match = fetched_matches[geo].get(keyword_key(name)) or {}
            demand.record_search(slug, geo, bare[0], bare[1], name=name,
                                 source=f'keyword planner {cfg.get("api_version", DEFAULT_VERSION)}',
                                 alias_used=(match.get('result_text')
                                             if match.get('mode') == 'close_variant' else None),
                                 data=demand_data, save_now=False)
            bucketed, why = looks_bucketed(bare[1])
            if bucketed:
                flagged += 1
            line += f' {geo.upper()}={bare[0]:,}/mo' + ('  [BUCKETED]' if bucketed else '')
            if geo == 'us' and bare[0] and qual is not None and qual[0] is not None:
                share = qual[0] / bare[0] if bare[0] else None
                demand.record_contamination(slug, round(share, 4) if share else 0.0, name=name,
                                            data=demand_data, save_now=False)
                if share is not None and share < demand.CONTAMINATION_FLOOR:
                    line += f'  ⚠ only {share:.0%} qualified — name is contaminated'
                if qualifier:
                    line += f'  [{qualifier} qualifier]'
        print(line)
    demand.save(demand_data)
    print(f'\n  stored -> {demand.PATH}')
    if flagged:
        print(f'  ⚠ {flagged} response(s) looked bucketed. That means the API answered from an\n'
              f'    account without real spend — check customer_id / login_customer_id.')
    print('  Growth: MoM and true YoY are available from multi-year history on the first run.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

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

WHY THE ACCOUNT CONTEXT MATTERS.
Keyword Planner returns Google-estimated volumes and may round or consolidate close variants.
Account context can affect how coarse those estimates are. `--check` probes one known keyword for
obviously restricted output; the dashboard always labels every stored number as an estimate.

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
import roster     # noqa: E402
import watchlist  # noqa: E402

CONFIG = os.path.join(BASE, 'data', 'config.json')
TOKEN_URL = 'https://oauth2.googleapis.com/token'
DEFAULT_VERSION = 'v25'
TIMEOUT = 45

# Google Ads geo target constants. Every market remains a separate measurement series.
GEO = {'in': '2356', 'us': '2840', 'ca': '2124'}
GEO_ORDER = ('in', 'us', 'ca')
NETWORK = 'GOOGLE_SEARCH_AND_PARTNERS'
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


def _request_headers(cfg, token):
    headers = {'Authorization': f'Bearer {token}',
               'developer-token': cfg['developer_token']}
    if cfg.get('login_customer_id'):
        headers['login-customer-id'] = str(cfg['login_customer_id']).replace('-', '')
    return headers


def historical_payload(keywords, geo_id, history_months=48):
    return {
        'keywords': list(keywords),
        'geoTargetConstants': [f'geoTargetConstants/{geo_id}'],
        'language': f'languageConstants/{LANG_EN}',
        'keywordPlanNetwork': NETWORK,
        'historicalMetricsOptions': {
            'yearMonthRange': history_range(history_months),
        },
    }


def historical_metrics(cfg, token, keywords, geo_id, history_months=48):
    """One call, one geo, explicitly Google Search plus Search Partners."""
    ver = cfg.get('api_version', DEFAULT_VERSION)
    cid = str(cfg['customer_id']).replace('-', '')
    url = (f'https://googleads.googleapis.com/{ver}/customers/{cid}'
           f':generateKeywordHistoricalMetrics')
    return _post(url, historical_payload(keywords, geo_id, history_months),
                 _request_headers(cfg, token)).get('results', []) or []


def keyword_ideas(cfg, token, seed_keywords, geo_id, page_size=1000):
    """Related keyword ideas for a niche. Returned phrases still require identity review."""
    ver = cfg.get('api_version', DEFAULT_VERSION)
    cid = str(cfg['customer_id']).replace('-', '')
    url = (f'https://googleads.googleapis.com/{ver}/customers/{cid}'
           f':generateKeywordIdeas')
    payload = {
        'geoTargetConstants': [f'geoTargetConstants/{geo_id}'],
        'language': f'languageConstants/{LANG_EN}',
        'keywordPlanNetwork': NETWORK,
        'keywordSeed': {'keywords': list(seed_keywords)},
        'pageSize': min(10000, max(1, int(page_size))),
    }
    response = _post(url, payload, _request_headers(cfg, token))
    return response.get('results', []) or []


def parse_idea_rows(rows):
    out = []
    for row in rows or []:
        text = ' '.join(str(row.get('text') or '').split())
        if not text:
            continue
        metrics = row.get('keywordIdeaMetrics') or {}
        avg = metrics.get('avgMonthlySearches')
        out.append(dict(
            text=text,
            avg_monthly_searches=(int(avg) if avg is not None else None),
            competition=metrics.get('competition'),
            close_variants=list(row.get('closeVariants') or []),
        ))
    return out


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
    """Flag an unusually flat or heavily rounded series for operator attention.

    The pattern can come from coarse Google estimates or genuinely stable low-volume demand.
    It is a series-quality warning, not an account-access diagnosis by itself.
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
        candidate = dict(index=index, text=text, parsed=parsed,
                         close_variants=list(row.get('closeVariants') or []))
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
                                result_text=candidate['text'], row_index=candidate['index'],
                                close_variants=candidate['close_variants'])
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
                                result_text=candidate['text'], row_index=candidate['index'],
                                close_variants=candidate['close_variants'])
        elif len(fallback) > 1:
            collisions[key] = dict(reason='multiple close-variant API rows', requested=original,
                                   result_texts=[x['text'] for x in fallback], nonblocking=False)
            missing.append(key)
        else:
            missing.append(key)
    return dict(mapped=mapped, matches=matches, collisions=collisions, missing=missing)


def _latest_measurement(series):
    rows = sorted(series or [], key=lambda row: (row.get('year', 0), row.get('month', 0)))
    if not rows:
        return None
    last = rows[-1]
    value = last.get('searches')
    return dict(month=f"{int(last['year']):04d}-{int(last['month']):02d}", value=value,
                state=('explicit_zero' if value == 0 else 'usable_series'))


def compact_measurement_record(plans, fetched, fetched_matches, mappings, run_at):
    """Build the immutable run manifest without copying the 48-month measurement series."""
    artists, months = [], []
    for plan in plans:
        keyword = plan['measurement_keyword']
        key = keyword_key(keyword)
        geos = {}
        for geo in GEO_ORDER:
            metric = (fetched.get(geo) or {}).get(key)
            match = (fetched_matches.get(geo) or {}).get(key) or {}
            mapping = mappings.get(geo) or {}
            collision = (mapping.get('collisions') or {}).get(key) or {}
            if match:
                state = match.get('mode') or 'mapped'
                latest = _latest_measurement(metric[1] if metric else [])
                if latest:
                    months.append(latest['month'])
            elif collision and not collision.get('nonblocking'):
                state, latest = 'ambiguous', None
            else:
                state, latest = 'missing', None
            geos[geo] = dict(mapping_state=state, result_text=match.get('result_text'),
                             close_variants=list(match.get('close_variants') or []), latest=latest)
        artists.append(dict(slug=plan['slug'], name=plan['name'],
                            approved_keyword=keyword, geos=geos))
    completed = dt.date.today().replace(day=1) - dt.timedelta(days=1)
    return dict(schema_version=1, kind='keyword_search_measurement', run_at=run_at,
                google_month=(max(months) if months else completed.strftime('%Y-%m')), network=NETWORK,
                language=LANG_EN, geos=list(GEO_ORDER), artists=artists)

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
    compact = compact_measurement_record([dict(slug='alpha', name='Alpha', measurement_keyword='Alpha')],
                                        {'in': exact['mapped']}, {'in': exact['matches']},
                                        {'in': exact}, '2026-08-21T12:00:00+05:30')
    payload = historical_payload(['Alpha'], GEO['in'], 12)
    ideas = parse_idea_rows([dict(text=' Example Artist ', keywordIdeaMetrics=dict(
        avgMonthlySearches='123', competition='LOW'))])
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
        ('Search plus Partners is explicit in every metrics request',
         payload['keywordPlanNetwork'] == 'GOOGLE_SEARCH_AND_PARTNERS'),
        ('India is a first-class separate geography', GEO_ORDER == ('in', 'us', 'ca')),
        ('compact manifest retains latest only, not 48-month history',
         compact['artists'][0]['geos']['in']['latest']['month'] == '2026-07' and
         compact['artists'][0]['approved_keyword'] == 'Alpha' and compact['google_month'] == '2026-07'),
        ('keyword idea parser retains a reviewable phrase and volume',
         ideas == [dict(text='Example Artist', avg_monthly_searches=123,
                       competition='LOW', close_variants=[])]),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


def entity_names(args):
    if args.names:
        return [(model.slugify(n.strip()), n.strip(), 'unknown', n.strip())
                for n in args.names.split(',') if n.strip()]
    registry = roster.load()
    if registry.get('artists'):
        return [(row['slug'], row['name'], row.get('primary_genre') or 'unknown',
                 row['measurement_keyword'])
                for row in roster.measurement_artists(
                    registry, include_candidates=getattr(args, 'include_candidates', False))]
    wl = watchlist.load()
    out = [(s, e.get('name') or s, e.get('genre') or 'unknown', e.get('name') or s)
           for s, e in (wl.get('artists') or {}).items()
           if not e.get('do_not_pursue') and e.get('active', True) and e.get('candidate_eligible', True)
           and e.get('kind', 'artist') in ('artist', 'dj_night')]
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true', help='every verified roster artist')
    ap.add_argument('--names', help='comma-separated names instead of the verified roster')
    ap.add_argument('--include-candidates', action='store_true',
                    help='also measure unverified roster candidates; they remain excluded from '
                         'verified genre totals')
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

    print('Keyword Planner — India, USA, Canada kept separate')
    print('=' * 68)
    print(f'  network: {NETWORK} (Google Search + Search Partners)')
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
        print(f'  data quality: {"COARSE/FLAT — " if bucketed else "OK — "}{why}')
        print('\n  --check: nothing written.')
        return 0

    targets = entity_names(a)
    if a.limit:
        targets = targets[:a.limit]
    if not targets:
        print('  no measurable roster artists yet — seed and review the artist registry first.')
        return 0

    print(f'  {len(targets)} roster artists\n')
    # Google supports up to 10,000 keywords per request. Batch the whole roster instead of
    # making two calls per artist; Keyword Planning is limited to 1 request/second per CID.
    plans = []
    all_keywords = []
    for slug, name, genre, measurement_keyword in targets:
        plans.append(dict(slug=slug, name=name, genre=genre,
                          measurement_keyword=measurement_keyword))
        all_keywords.append(measurement_keyword)
    all_keywords = list(dict.fromkeys(all_keywords))
    print(f'  {len(all_keywords)} approved measurement keywords in geo batches\n')

    fetched, fetched_matches, mappings = {}, {}, {}
    mapping_health = {}
    request_count = 0
    batch_size = 9000
    for geo in GEO_ORDER:
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
        mappings[geo] = mapping
        blocking = [item for item in mapping['collisions'].values() if not item['nonblocking']]
        exact_count = sum(1 for item in mapping['matches'].values() if item['mode'] == 'exact')
        fallback_count = sum(1 for item in mapping['matches'].values()
                             if item['mode'] == 'close_variant')
        mapping_health[geo] = dict(requested=len(all_keywords), mapped=len(mapping['mapped']),
                                   exact=exact_count, close_variant=fallback_count,
                                   ambiguous=len(blocking), missing=len(mapping['missing']))
        print(f'  {geo.upper()} batch: {len(mapping["mapped"])} mapped '
              f'({exact_count} exact, {fallback_count} unambiguous close variants)')
        if blocking:
            print(f'  ⚠ {geo.upper()} skipped {len(blocking)} ambiguous keyword mapping(s); '
                  'no result was silently chosen.')


    run_at = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    measurement_record = compact_measurement_record(plans, fetched, fetched_matches, mappings, run_at)
    # Fail before demand.json is changed if an identical run timestamp already has a manifest.
    measurement_path = demand.assert_measurement_available(run_at)
    flagged = 0
    demand_data = demand.load()
    for plan in plans:
        slug = plan['slug']
        name = plan['name']
        measurement_keyword = plan['measurement_keyword']
        line = f'  {name[:30]:32}'
        for geo in GEO_ORDER:
            key = keyword_key(measurement_keyword)
            metric = fetched[geo].get(key)
            match = fetched_matches[geo].get(key) or {}
            if metric is None:
                collision = (mappings[geo].get('collisions') or {}).get(key) or {}
                mapping_mode = 'ambiguous' if collision and not collision.get('nonblocking') else 'missing'
                demand.record_search(slug, geo, None, [], name=name,
                    source=f'keyword planner {cfg.get("api_version", DEFAULT_VERSION)}',
                    data=demand_data, save_now=False, keyword=measurement_keyword,
                    network=NETWORK, mapping_mode=mapping_mode,
                    result_text=None, close_variants=[], language=LANG_EN,
                    geo_target=GEO[geo], fetched_at=run_at)
                line += f' {geo.upper()}=—'
                continue
            demand.record_search(
                slug, geo, metric[0], metric[1], name=name,
                source=f'keyword planner {cfg.get("api_version", DEFAULT_VERSION)}',
                alias_used=(match.get('result_text')
                            if match.get('mode') == 'close_variant' else None),
                data=demand_data, save_now=False, keyword=measurement_keyword,
                network=NETWORK, mapping_mode=match.get('mode'),
                result_text=match.get('result_text'),
                close_variants=match.get('close_variants'), language=LANG_EN,
                geo_target=GEO[geo], fetched_at=run_at)
            bucketed, why = looks_bucketed(metric[1])
            if bucketed:
                flagged += 1
            display = f'{metric[0]:,}/mo' if metric[0] is not None else 'Unknown'
            line += f' {geo.upper()}={display}' + ('  [COARSE]' if bucketed else '')
        print(line)
    demand.record_run(dict(kind='keyword_historical_metrics', network=NETWORK,
                           language=LANG_EN, history_months=a.history_months,
                           recorded_at=run_at, geos=mapping_health, roster_artists=len(plans),
                           keyword_count=len(all_keywords), coarse_series=flagged),
                      data=demand_data, save_now=False)
    demand.save(demand_data)
    # Manifest follows a fully successful pull/save and contains no credential/config material.
    demand.write_search_measurement(measurement_record,
                                    directory=os.path.dirname(measurement_path))
    print(f'\n  stored -> {demand.PATH}')
    print(f'  immutable measurement -> {measurement_path}')
    if flagged:
        print(f'  Note: {flagged} artist/market series were flat or heavily rounded. They remain\n'
              f'    Google estimates; this pattern alone does not diagnose account access.')
    print('  Growth: MoM and true YoY are available from multi-year history on the first run.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

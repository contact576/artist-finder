"""The demand side — would anyone in North America actually buy a ticket?

WHY THIS IS A SEPARATE MODULE AND A SEPARATE SCORE.
Everything in signals.py is SUPPLY-side: rooms booked, nights added, platforms reached. All of
it happens in India and all of it measures what an Indian promoter believes. This module holds
the other half — search volume in the US and Canada, its growth, and whether a foreign promoter
has already put money on the artist.

The two must never be added together. The Artist Tour Engine measured why: tickets per million
followers spans 15x across its roster and the follower->draw fit is sublinear (exponent 0.369).
Reach in India and sales to the diaspora come apart badly. Fold them into one number and a
domestic star with no diaspora audience outranks an artist who already sells out London.

So `momentum` (score.py) answers *are they getting hot*, `export_signal` (here) answers *would
it travel*, and the report shows both. RISING plus a strong export signal is a call today;
RISING with nothing here is interesting and not urgent.

US AND CANADA ARE NEVER SUMMED. Same cardinal rule as every sibling project. Where one number
is needed, the STRONGER of the two normalised sub-scores carries it — never the total.

WHAT "YEAR OVER YEAR" HONESTLY MEANS HERE.
Google returns a rolling 12-month window. Comparing its last month to its first is a ~11-month
gap, not a year, and the same-month-last-year figure simply is not in that one response. True
YoY becomes observable as soon as OUR OWN stored history contains the latest month and the same
month in the prior year; a 48-month pull can satisfy that immediately. Until that pair exists,
`yoy()` reports observable=False and `window_trend()` is offered instead, labelled for what it
is. A 12-month window alone still cannot produce YoY.
"""
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

PATH = os.path.join(model.BASE, 'data', 'demand.json')
MEASUREMENTS_DIR = os.path.join(model.BASE, 'data', 'search_measurements')
_MEASUREMENT_SECRET_WORDS = ('token', 'secret', 'password', 'authorization', 'cookie', 'api_key', 'developer_token', 'client_secret', 'refresh_token')

# Search intelligence tracks all three geographies independently. ``GEOS`` remains the
# diaspora-only pair used by export_signal and the Tour Engine handoff; adding India to that
# loop would quietly turn home-market awareness into diaspora evidence.
SEARCH_GEOS = ('in', 'us', 'ca')
GEOS = ('us', 'ca')
GEO_CRITERIA = {'in': '2356', 'us': '2840', 'ca': '2124'}  # Google Ads geo targets

# Markets where a show is mostly sold to the South Asian diaspora. A date in one of these is
# far better evidence for a North American tour than a date in, say, Mumbai — it is the same
# audience, just a different city.
DIASPORA_MARKETS = {
    'united kingdom', 'uk', 'england', 'australia', 'new zealand', 'canada',
    'united states', 'usa', 'united arab emirates', 'uae', 'singapore', 'malaysia',
    'qatar', 'kuwait', 'bahrain', 'oman', 'south africa', 'ireland', 'netherlands', 'germany',
}

# Below this share the bare name belongs to somebody else and its volume is junk. Measured in
# the Tour Engine research: Jaspreet Singh 2%, Aakash Gupta 1% (an unrelated singer). Both look
# enormous in search and do not sell tickets.
CONTAMINATION_FLOOR = 0.20

# Weights for export_signal. Judgement, like score.W — and labelled the same way.
W = dict(volume=50, growth=25, international=25)


def load():
    if not os.path.exists(PATH):
        return dict(updated_at=None, entities={})
    with open(PATH, encoding='utf-8') as f:
        return json.load(f)


def save(d):
    d['updated_at'] = dt.datetime.now().isoformat(timespec='seconds')
    tmp = PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, PATH)
    return PATH


def _measurement_safe(value):
    """Reject credentials before an immutable measurement artifact reaches disk."""
    if isinstance(value, dict):
        for key, item in value.items():
            if any(word in str(key).casefold() for word in _MEASUREMENT_SECRET_WORDS):
                raise ValueError(f'search measurement contains prohibited key: {key}')
            _measurement_safe(item)
    elif isinstance(value, list):
        for item in value:
            _measurement_safe(item)
    elif isinstance(value, str) and any(word in value.casefold() for word in
                                         ('bearer ', 'developer_token=', 'refresh_token=')):
        raise ValueError('search measurement contains credential-like text')


def measurement_path(run_at, directory=None):
    """Stable per-run path; a collision is an error, never an overwrite."""
    stamp = ''.join(ch for ch in str(run_at) if ch.isalnum())
    if not stamp:
        raise ValueError('search measurement needs a run timestamp')
    return os.path.join(directory or MEASUREMENTS_DIR, f'search_measurement_{stamp}.json')


def assert_measurement_available(run_at, directory=None):
    path = measurement_path(run_at, directory)
    if os.path.exists(path):
        raise FileExistsError(f'immutable search measurement already exists: {path}')
    return path


def write_search_measurement(record, directory=None):
    """Atomically persist one compact, non-secret, immutable Keyword Planner run manifest.

    The artifact intentionally contains only per-artist mapping evidence and each geo's latest
    month/value. The 48-month source series stays solely in demand.json/history.
    """
    if not isinstance(record, dict) or record.get('kind') != 'keyword_search_measurement':
        raise ValueError('invalid search measurement record')
    for field in ('run_at', 'google_month', 'network', 'language', 'geos', 'artists'):
        if field not in record:
            raise ValueError(f'search measurement missing {field}')
    if tuple(record.get('geos') or ()) != SEARCH_GEOS:
        raise ValueError('search measurement geographies must be India, USA, Canada separately')
    if not isinstance(record.get('artists'), list):
        raise ValueError('search measurement artists must be a list')
    _measurement_safe(record)
    path = assert_measurement_available(record['run_at'], directory)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(record, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write('\n')
    # If another run created the final path between our first check and replace, retain both
    # histories by failing and leaving the temporary file removable instead of clobbering it.
    if os.path.exists(path):
        os.remove(tmp)
        raise FileExistsError(f'immutable search measurement already exists: {path}')
    os.replace(tmp, path)
    return path

def _entry(d, slug, name=None):
    e = d.setdefault('entities', {}).setdefault(slug, dict(
        slug=slug, name=name or slug, aliases=[],
        search={g: dict(avg_monthly=None, monthly=[], fetched_at=None, source=None)
                for g in SEARCH_GEOS},
        history={g: [] for g in SEARCH_GEOS},   # our own accumulating record, for true YoY
        contamination=dict(qualified_share=None, checked_at=None),
        trends=dict(direction=None, slope=None, fetched_at=None),
        international=dict(dates=[], checked_at=None, status='not_checked',
                           note=None, method=None)))
    # Backward-compatible schema migration in memory. Existing retained demand files have only
    # US and CA; they are not rewritten until the next deliberate monthly save.
    e.setdefault('search', {})
    e.setdefault('history', {})
    for geo in SEARCH_GEOS:
        e['search'].setdefault(geo, dict(avg_monthly=None, monthly=[], fetched_at=None,
                                        source=None))
        e['history'].setdefault(geo, [])
    if name and not e.get('name'):
        e['name'] = name
    return e


# ------------------------------------------------------------------ recording

def record_search(slug, geo, avg_monthly, monthly, name=None, source=None, alias_used=None,
                  data=None, save_now=True, keyword=None, network=None,
                  mapping_mode=None, result_text=None, close_variants=None,
                  language=None, geo_target=None, fetched_at=None):
    """Store one geo's Keyword Planner result.

    `monthly` is a list of {'year', 'month', 'searches'} oldest first. Appending each fetch into
    `history` preserves each month so true year-over-year can compare the latest month with the
    same month one year earlier. A single rolling 12-month response never contains that pair,
    but a longer pull can provide it immediately.
    """
    if geo not in SEARCH_GEOS:
        raise ValueError(f'geo must be one of {SEARCH_GEOS}')
    d = data if data is not None else load()
    e = _entry(d, slug, name)
    e['search'][geo] = dict(avg_monthly=avg_monthly, monthly=list(monthly or []),
                            fetched_at=(fetched_at or dt.datetime.now().astimezone().isoformat(timespec='seconds')), source=source,
                            alias_used=alias_used, keyword=keyword,
                            network=network, mapping_mode=mapping_mode,
                            result_text=result_text,
                            close_variants=list(close_variants or []),
                            language=language, geo_target=geo_target)
    seen = {(h['year'], h['month']) for h in e['history'][geo]}
    for m in (monthly or []):
        if (m['year'], m['month']) not in seen:
            e['history'][geo].append(dict(m))
    e['history'][geo].sort(key=lambda m: (m['year'], m['month']))
    if save_now:
        save(d)
    return e['search'][geo]


def record_run(run, data=None, save_now=True):
    """Retain non-secret monthly request/mapping health for dashboard observability."""
    d = data if data is not None else load()
    row = dict(run or {})
    row.setdefault('recorded_at', dt.datetime.now().astimezone().isoformat(timespec='seconds'))
    d.setdefault('runs', []).append(row)
    d['runs'] = d['runs'][-36:]
    if save_now:
        save(d)
    return row


def record_contamination(slug, qualified_share, name=None, data=None, save_now=True):
    d = data if data is not None else load()
    e = _entry(d, slug, name)
    e['contamination'] = dict(qualified_share=qualified_share,
                              checked_at=dt.date.today().isoformat())
    if save_now:
        save(d)
    return e['contamination']


def record_trend(slug, direction, slope=None, name=None):
    """Google Trends direction ONLY. Trends returns a 0-100 relative index, never a volume, and
    storing it beside real volumes without this separation is how a relative index ends up in a
    forecast."""
    d = load()
    e = _entry(d, slug, name)
    e['trends'] = dict(direction=direction, slope=slope,
                       fetched_at=dt.date.today().isoformat())
    save(d)
    return e['trends']


def record_international(slug, dates, name=None, status=None, note=None, method=None):
    """`dates` = [{'country','city','date','source','url'}]. Replaces the stored list."""
    d = load()
    e = _entry(d, slug, name)
    dates = list(dates or [])
    e['international'] = dict(dates=dates, checked_at=dt.date.today().isoformat(),
                              status=status or ('found' if dates else 'checked_none'),
                              note=note, method=method)
    save(d)
    return e['international']


# ------------------------------------------------------------------ growth

def _series(e, geo):
    return sorted(e.get('history', {}).get(geo) or [], key=lambda m: (m['year'], m['month']))


def mom(slug, geo, d=None):
    """Last month vs the one before, within the current window. Available immediately."""
    d = d or load()
    e = (d.get('entities') or {}).get(slug)
    s = _series(e, geo) if e else []
    if len(s) < 2 or not s[-2]['searches']:
        return dict(value=None, observable=False, note='need two consecutive months')
    v = (s[-1]['searches'] - s[-2]['searches']) / s[-2]['searches']
    return dict(value=round(v, 4), observable=True,
                note=f"{s[-2]['searches']:,} -> {s[-1]['searches']:,} "
                     f"({s[-1]['year']}-{s[-1]['month']:02d})")


def yoy(slug, geo, d=None):
    """TRUE year-over-year: this month against the same month last year.

    Needs the latest month and the same month one year earlier in our accumulated history. A
    single 12-month Google window cannot contain both, but a 48-month pull can. Returns
    observable=False until the pair exists rather than substituting the window slope and
    calling it YoY.
    """
    d = d or load()
    e = (d.get('entities') or {}).get(slug)
    s = _series(e, geo) if e else []
    if not s:
        return dict(value=None, observable=False, note='no history')
    last = s[-1]
    want = (last['year'] - 1, last['month'])
    prior = next((m for m in s if (m['year'], m['month']) == want), None)
    if prior is None or not prior['searches']:
        return dict(value=None, observable=False,
                    note=f'no {want[0]}-{want[1]:02d} figure yet — needs the same month '
                         f'from the prior year (have {len(s)} stored months)')
    v = (last['searches'] - prior['searches']) / prior['searches']
    return dict(value=round(v, 4), observable=True,
                note=f"{prior['searches']:,} ({want[0]}) -> {last['searches']:,} "
                     f"({last['year']})")


def window_trend(slug, geo, d=None):
    """Last 3 months vs first 3 of the stored window. NOT year-over-year — say so."""
    d = d or load()
    e = (d.get('entities') or {}).get(slug)
    s = _series(e, geo) if e else []
    if len(s) < 6:
        return dict(value=None, observable=False, note='need 6+ months')
    first = [m['searches'] for m in s[:3] if m['searches']]
    last = [m['searches'] for m in s[-3:] if m['searches']]
    if not first or not last or not sum(first):
        return dict(value=None, observable=False, note='zero baseline')
    a, b = sum(first) / len(first), sum(last) / len(last)
    span = len(s) - 3
    return dict(value=round((b - a) / a, 4), observable=True,
                note=f'{a:,.0f} -> {b:,.0f} across ~{span} months (NOT a full year)')


# ------------------------------------------------------------------ export signal

def _volume_score(v):
    """1k/mo -> 0, 10k -> 50, 100k -> 100. Log, because draw scales sublinearly with reach."""
    if not v or v <= 0:
        return None
    return max(0.0, min(100.0, (math.log10(v) - 3.0) / 2.0 * 100.0))


def _international_score(dates):
    if not dates:
        return 0.0, 'no foreign dates found'
    countries = {(x.get('country') or '').strip().lower() for x in dates}
    countries.discard('')
    dia = countries & DIASPORA_MARKETS
    if len(countries) >= 2 and dia:
        return 90.0, f'{len(dates)} dates across {len(countries)} countries incl. ' \
                     f'{", ".join(sorted(dia)[:3])}'
    if dia:
        return 70.0, f'{len(dates)} date(s) in {", ".join(sorted(dia)[:3])}'
    return 40.0, f'{len(dates)} foreign date(s), none in a core diaspora market'


def export_signal(slug, d=None):
    """0-100: would this travel? Renormalised over observable parts, like momentum.

    US and CA are scored SEPARATELY and the stronger carries the volume component. They are
    never added — a 6k/mo US artist and a 6k/mo Canadian one is not a 12k artist.
    """
    d = d or load()
    e = (d.get('entities') or {}).get(slug)
    if not e:
        return dict(value=None, observable=False, coverage=0.0, parts=[],
                    note='no demand data fetched for this entity yet')

    parts, used, total_w = [], 0.0, 0.0

    # --- volume, stronger geo wins
    per_geo, best, best_geo = {}, None, None
    for g in GEOS:
        v = (e.get('search', {}).get(g) or {}).get('avg_monthly')
        sc = _volume_score(v)
        per_geo[g] = dict(avg_monthly=v, score=sc)
        if sc is not None and (best is None or sc > best):
            best, best_geo = sc, g

    share = (e.get('contamination') or {}).get('qualified_share')
    contaminated = share is not None and share < CONTAMINATION_FLOOR
    if best is not None:
        adj = best * 0.3 if contaminated else best
        note = (f'{best_geo.upper()} {per_geo[best_geo]["avg_monthly"]:,}/mo'
                + (f' — CONTAMINATED ({share:.0%} qualified), discounted' if contaminated else ''))
        parts.append(dict(signal='volume', weight=W['volume'], unit=round(adj / 100, 3),
                          contribution=round(adj / 100 * W['volume'], 2), observable=True,
                          note=note))
        used += adj / 100 * W['volume']
        total_w += W['volume']
    else:
        parts.append(dict(signal='volume', weight=W['volume'], unit=None, contribution=None,
                          observable=False, note='no search volume fetched'))

    # --- growth: true YoY if we have it, else the window trend, clearly labelled
    gsig, glabel = None, None
    for g in GEOS:
        y = yoy(slug, g, d)
        if y['observable']:
            gsig, glabel = y, f'YoY {g.upper()}'
            break
    if gsig is None:
        for g in GEOS:
            w = window_trend(slug, g, d)
            if w['observable']:
                gsig, glabel = w, f'window trend {g.upper()} (not a full year)'
                break
    if gsig and gsig['observable']:
        u = max(0.0, min(1.0, (gsig['value'] + 0.25) / 1.25))   # -25% -> 0, +100% -> full
        parts.append(dict(signal='growth', weight=W['growth'], unit=round(u, 3),
                          contribution=round(u * W['growth'], 2), observable=True,
                          note=f'{glabel}: {gsig["value"]:+.0%} — {gsig["note"]}'))
        used += u * W['growth']
        total_w += W['growth']
    else:
        parts.append(dict(signal='growth', weight=W['growth'], unit=None, contribution=None,
                          observable=False, note='not enough monthly history yet'))

    # --- international validation
    intl = (e.get('international') or {})
    dates = intl.get('dates') or []
    status = intl.get('status')
    if not status:
        status = 'found' if dates else ('checked_none' if intl.get('checked_at') else 'not_checked')
    if status == 'found' and dates:
        sc, note = _international_score(intl.get('dates'))
        parts.append(dict(signal='international', weight=W['international'],
                          unit=round(sc / 100, 3),
                          contribution=round(sc / 100 * W['international'], 2),
                          observable=True, note=note))
        used += sc / 100 * W['international']
        total_w += W['international']
    else:
        note = intl.get('note') or {
            'not_checked': 'not checked yet',
            'unconfigured': 'source not configured',
            'error': 'source check failed',
            'checked_none': 'none found; source coverage is incomplete',
        }.get(status, 'international status unknown')
        parts.append(dict(signal='international', weight=W['international'], unit=None,
                          contribution=None, observable=False, note=note))

    if total_w == 0:
        return dict(value=None, observable=False, coverage=0.0, parts=parts,
                    note='nothing on the demand side is known yet')
    return dict(value=round(100 * used / total_w), observable=True,
                coverage=round(total_w / sum(W.values()), 2), parts=parts,
                per_geo=per_geo, contaminated=contaminated,
                note=f'{int(total_w / sum(W.values()) * 100)}% of demand weight observable')


def for_engine(slug, d=None):
    """The two fields the Artist Tour Engine actually needs, kept separate by currency."""
    d = d or load()
    e = (d.get('entities') or {}).get(slug) or {}
    s = e.get('search', {})
    return dict(us_monthly=(s.get('us') or {}).get('avg_monthly'),
                ca_monthly=(s.get('ca') or {}).get('avg_monthly'),
                aliases=e.get('aliases') or [],
                comedian_share=(e.get('contamination') or {}).get('qualified_share'),
                source=(s.get('us') or {}).get('source'))


def _selftest():
    global PATH, MEASUREMENTS_DIR
    import tempfile
    temp_root = tempfile.mkdtemp()
    PATH = os.path.join(temp_root, 'demand.json')
    MEASUREMENTS_DIR = os.path.join(temp_root, 'measurements')

    def months(start_y, start_m, vals):
        out, y, m = [], start_y, start_m
        for v in vals:
            out.append(dict(year=y, month=m, searches=v))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    # A rising artist: one 12-month pull to Jul 2026, climbing. It cannot produce YoY.
    record_search('rising-one', 'us', 12000,
                  months(2025, 8, [4000, 4200, 5000, 5500, 6000, 7000, 8000,
                                   9500, 11000, 13000, 15000, 18000]),
                  name='Rising One', source='keyword planner')
    record_search('rising-one', 'ca', 3000,
                  months(2025, 8, [1200, 1300, 1400, 1500, 1700, 1900, 2100,
                                   2400, 2700, 3000, 3300, 3800]),
                  name='Rising One', source='keyword planner')
    record_international('rising-one', [
        dict(country='United Kingdom', city='London', date='2026-11-02', source='serp'),
        dict(country='Australia', city='Sydney', date='2026-11-20', source='bandsintown')])

    # A contaminated name: huge volume that belongs to someone else.
    record_search('contaminated-one', 'us', 90000,
                  months(2025, 8, [88000] * 12), name='Contaminated One')
    record_contamination('contaminated-one', 0.02)

    print('  growth on rising-one (US)')
    for fn, label in ((mom, 'MoM'), (yoy, 'YoY'), (window_trend, 'window')):
        r = fn('rising-one', 'us')
        mark = ' ' if r['observable'] else '~'
        val = f'{r["value"]:+.1%}' if r['value'] is not None else '—'
        print(f'   {mark}{label:8} {val:>8}   {r["note"]}')

    print('\n  export_signal')
    for slug in ('rising-one', 'contaminated-one', 'never-fetched'):
        r = export_signal(slug)
        print(f'    {slug:18} value={str(r["value"]):>5} coverage={r["coverage"]} '
              f'{r["note"][:44]}')
        for p in r['parts']:
            mark = ' ' if p['observable'] else '~'
            print(f'      {mark}{p["signal"]:14} contrib={str(p["contribution"]):>6}  '
                  f'{p["note"][:62]}')

    manifest = dict(kind='keyword_search_measurement', schema_version=1,
                    run_at='2026-08-21T12:00:00+05:30', google_month='2026-07',
                    network='GOOGLE_SEARCH_AND_PARTNERS', language='1000',
                    geos=list(SEARCH_GEOS), artists=[dict(slug='rising-one',
                    approved_keyword='Rising One', geos=dict(us=dict(mapping_state='exact',
                    result_text='Rising One', latest=dict(month='2026-07', value=18000,
                    state='usable'))))])
    manifest_path = write_search_measurement(manifest)
    immutable_rejected = False
    try:
        write_search_measurement(manifest)
    except FileExistsError:
        immutable_rejected = True
    secret_rejected = False
    try:
        unsafe = dict(manifest, run_at='2026-08-21T12:00:01+05:30', refresh_token='nope')
        write_search_measurement(unsafe)
    except ValueError:
        secret_rejected = True
    r_rise = export_signal('rising-one')
    r_cont = export_signal('contaminated-one')
    eng = for_engine('rising-one')
    checks = [
        ('rising artist scores well', (r_rise['value'] or 0) >= 60),
        ('YoY correctly unavailable without prior-year pair',
         not yoy('rising-one', 'us')['observable']),
        ('YoY available when prior-year pair exists',
         record_search('rising-one', 'us', 12000,
                       months(2026, 8, [20000]), name='Rising One',
                       source='keyword planner') is not None
         and yoy('rising-one', 'us')['observable']),
        ('window trend available', window_trend('rising-one', 'us')['observable']),
        ('contamination discounts volume', (r_cont['value'] or 100) < (r_rise['value'] or 0)),
        ('contaminated flag set', r_cont['contaminated'] is True),
        ('unfetched entity is unobservable', not export_signal('never-fetched')['observable']),
        ('engine fields kept separate', eng['us_monthly'] == 12000 and eng['ca_monthly'] == 3000),
        # The volume component must come from ONE geo, never the total. 12,000 US and 3,000 CA
        # must score as 12,000 — if anyone ever "helpfully" adds them, this catches it.
        # `unit` is stored rounded to 3dp, so compare at that precision.
        ('volume uses the stronger geo, not the sum',
         abs((r_rise['parts'][0]['unit'] or 0) - _volume_score(12000) / 100) < 1e-3
         and abs((r_rise['parts'][0]['unit'] or 0) - _volume_score(15000) / 100) > 1e-2),
        ('both geos still reported separately',
         r_rise['per_geo']['us']['avg_monthly'] == 12000
         and r_rise['per_geo']['ca']['avg_monthly'] == 3000),
        ('compact measurement is immutable', os.path.exists(manifest_path) and immutable_rejected),
        ('measurement rejects secrets', secret_rejected),
    ]
    print()
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

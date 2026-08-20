"""Is somebody else already touring them?

THE STRONGEST SINGLE SIGNAL THIS PROJECT CAN OBSERVE.
Everything else here is inference from listings. A confirmed date in London, Sydney or Dubai is
not inference: a foreign promoter has already done their own diligence, priced the room, and put
money down. And when those dates are in diaspora markets it is very nearly the same audience a
North American tour would sell to — the closest proxy available short of touring the artist.

That is why `demand._international_score` weights a multi-country diaspora run at 90 and a
single non-diaspora date at 40. Someone playing Dubai and London is a different proposition from
someone playing one festival in Nepal.

TWO WAYS IN, AND THE COVERAGE GAP IS REAL.
  Bandsintown / Songkick   proper APIs, good for music, THIN FOR COMEDY. Most Indian stand-ups
                           are not on either. A blank result is NOT evidence of no tour.
  SERP check               works for any genre, run per shortlisted artist by Claude.

The module never reports "no international dates" as a finding. It reports "none found via
<method>", which is a different claim, and the report must keep it that way.

NETWORK IS OPTIONAL HERE. Bandsintown is a plain keyed GET, so stdlib urllib can call it
directly when an app_id is configured — no MCP round-trip for something this simple. Everything
still works without it: Claude hands in SERP findings and the module just stores them.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demand  # noqa: E402
import model   # noqa: E402

CONFIG = os.path.join(model.BASE, 'data', 'config.json')
BANDSINTOWN = 'https://rest.bandsintown.com/artists/{artist}/events'
TIMEOUT = 20

# Country name -> canonical form, so "U.K." / "United Kingdom" / "England" all score alike.
_COUNTRY_FIX = {
    'uk': 'United Kingdom', 'u.k.': 'United Kingdom', 'england': 'United Kingdom',
    'scotland': 'United Kingdom', 'wales': 'United Kingdom', 'great britain': 'United Kingdom',
    'usa': 'United States', 'u.s.a.': 'United States', 'us': 'United States',
    'uae': 'United Arab Emirates', 'u.a.e.': 'United Arab Emirates',
}


def load_config():
    if not os.path.exists(CONFIG):
        return {}
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)


def canon_country(c):
    c = re.sub(r'\s+', ' ', str(c or '')).strip()
    return _COUNTRY_FIX.get(c.lower(), c)


def is_domestic(country):
    return canon_country(country).lower() in ('india', 'in', 'bharat')


def _fetch_bandsintown_legacy(artist_name, app_id=None, timeout=TIMEOUT):
    """-> (dates, note). Never raises: a dead API must not take down the weekly run.

    Returns [] with an explanatory note when unconfigured, unreachable, or when the artist
    simply is not on the platform — and the caller has to keep those three cases distinct from
    "this artist does not tour abroad".
    """
    app_id = app_id or load_config().get('bandsintown_app_id')
    if not app_id:
        return [], 'bandsintown_app_id not set in data/config.json — skipped'
    url = BANDSINTOWN.format(artist=urllib.parse.quote(artist_name, safe='')) + \
        '?' + urllib.parse.urlencode({'app_id': app_id})
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            payload = json.loads(r.read().decode('utf-8'))
    except Exception as e:                                    # noqa: BLE001
        return [], f'bandsintown unreachable: {type(e).__name__}'
    if not isinstance(payload, list):
        return [], 'artist not found on bandsintown (thin for comedy — not evidence of no tour)'
    out = []
    for e in payload:
        v = e.get('venue') or {}
        out.append(dict(country=canon_country(v.get('country')), city=v.get('city'),
                        venue=v.get('name'), date=(e.get('datetime') or '')[:10],
                        source='bandsintown', url=e.get('url')))
    return out, f'{len(out)} dates from bandsintown'
def fetch_bandsintown(artist_name, app_id=None, timeout=TIMEOUT):
    """Return dates, explanatory note, and an explicit source status."""
    configured = app_id or load_config().get('bandsintown_app_id')
    rows, note = _fetch_bandsintown_legacy(artist_name, app_id, timeout)
    if rows:
        status = 'found'
    elif not configured:
        status = 'unconfigured'
    elif 'unreachable' in note:
        status = 'error'
    else:
        status = 'checked_none'
    return rows, note, status


def normalise_dates(rows, drop_domestic=True, future_only=True, asof=None):
    """Clean a batch of found dates from any source into the stored shape.

    Indian dates are dropped by default — the whole point is what happens OUTSIDE India, and
    leaving them in would let a busy domestic run masquerade as international validation.
    """
    asof = asof or dt.date.today().isoformat()
    out = []
    for r in rows or []:
        c = canon_country(r.get('country'))
        if drop_domestic and is_domestic(c):
            continue
        d = (r.get('date') or '')[:10]
        if future_only and d and d < asof:
            continue
        out.append(dict(country=c, city=r.get('city'), venue=r.get('venue'), date=d or None,
                        source=r.get('source') or 'unknown', url=r.get('url')))
    out.sort(key=lambda x: (x['date'] or '9999', x['country'] or ''))
    return out


def record(slug, rows, name=None, method=None, asof=None, status=None, source_note=None):
    """Store the cleaned dates against the entity and return a summary."""
    clean = normalise_dates(rows, asof=asof)
    final_status = 'found' if clean else (status or 'checked_none')
    demand.record_international(slug, clean, name=name, status=final_status,
                                note=source_note, method=method)
    countries = sorted({r['country'] for r in clean if r['country']})
    dia = [c for c in countries if c.lower() in demand.DIASPORA_MARKETS]
    return dict(slug=slug, n_dates=len(clean), countries=countries,
                diaspora_countries=dia, method=method,
                note=(f'{len(clean)} foreign date(s) across {len(countries)} country(ies)'
                      if clean else f'none found via {method or "the methods tried"}'))


def check(slug, name, serp_rows=None, app_id=None, asof=None):
    """One artist, both routes. SERP findings from Claude are merged with the API result."""
    api_rows, note, api_status = fetch_bandsintown(name, app_id)
    merged = list(api_rows) + list(serp_rows or [])
    seen, uniq = set(), []
    for r in merged:
        k = (canon_country(r.get('country')), (r.get('city') or '').lower(),
             (r.get('date') or '')[:10])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    res = record(slug, uniq, name=name,
                 method='bandsintown + serp' if serp_rows is not None else 'bandsintown',
                 asof=asof, status=api_status, source_note=note)
    res['api_note'] = note
    return res


def serp_queries(name):
    """What Claude should search for a shortlisted artist. Kept here so the query set is
    versioned with the code rather than living only in a prompt."""
    return [f'"{name}" tour tickets UK',
            f'"{name}" live London tickets',
            f'"{name}" Australia tour tickets',
            f'"{name}" Dubai OR Singapore OR Toronto live tickets',
            f'"{name}" tour dates 2026 OR 2027 -india']


def _selftest():
    global CONFIG
    import tempfile
    tmp = tempfile.mkdtemp()
    CONFIG = os.path.join(tmp, 'config.json')       # deliberately absent -> API skipped
    demand.PATH = os.path.join(tmp, 'demand.json')

    rows, note, status = fetch_bandsintown('Anybody')
    print(f'  unconfigured API: {len(rows)} rows — {note}')

    found = [
        dict(country='UK', city='London', venue='Eventim Apollo', date='2026-12-01',
             source='serp'),
        dict(country='England', city='Birmingham', venue='Symphony Hall', date='2026-12-03',
             source='serp'),
        dict(country='Australia', city='Sydney', venue='Enmore', date='2027-01-14',
             source='serp'),
        dict(country='India', city='Mumbai', venue='NCPA', date='2026-11-01', source='serp'),
        dict(country='UAE', city='Dubai', venue='Coca-Cola Arena', date='2025-01-01',
             source='serp'),                                    # past — must be dropped
    ]
    clean = normalise_dates(found, asof='2026-08-19')
    print('\n  normalised:')
    for r in clean:
        print(f'    {r["date"]}  {r["country"]:16} {r["city"]}')

    res = record('touring-one', found, name='Touring One', method='serp', asof='2026-08-19')
    print(f'\n  {res["note"]}  countries={res["countries"]} diaspora={res["diaspora_countries"]}')

    xs = demand.export_signal('touring-one')
    intl = next(p for p in xs['parts'] if p['signal'] == 'international')
    print(f'  export_signal international part: unit={intl["unit"]} — {intl["note"]}')

    empty = record('quiet-one', [], name='Quiet One', method='bandsintown')
    print(f'  no-dates wording: "{empty["note"]}"')

    checks = [
        ('missing app_id is skipped, not an error', rows == [] and status == 'unconfigured'),
        ('UK and England both canonicalise',
         all(r['country'] == 'United Kingdom' for r in clean if r['city'] in
             ('London', 'Birmingham'))),
        ('domestic Indian date dropped', not any(r['country'] == 'India' for r in clean)),
        ('past date dropped', not any((r['date'] or '') < '2026-08-19' for r in clean)),
        ('multi-country diaspora scores high', (intl['unit'] or 0) >= 0.9),
        ('absence phrased as "none found", not "no tours"',
         'none found via' in empty['note']),
        ('empty result remains unobservable',
         not demand.export_signal('quiet-one')['parts'][-1]['observable']),
    ]
    ok = True
    print()
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

"""Discovery pass: which sources can we actually read, and how?

WHY THIS RUNS BEFORE ANY STRICT CRAWL.
Every entry in sources.json carries `verify: true` and was written from general knowledge. This
probe replaces guesses with observation: for each candidate URL it records the HTTP status,
whether the page carries JSON-LD structured event data, and roughly how many events are on it.

THE THREE OUTCOMES, AND WHY THE FIRST ONE MATTERS SO MUCH.

  DIRECT + JSON-LD   the page serves to a plain request AND publishes schema.org Event objects.
                     Title, venue, city and date arrive already structured. No Apify, no cost,
                     no parser to break. This is by far the best case and worth checking for
                     every source before reaching for a crawler.

  DIRECT, no JSON-LD readable, but the listings need parsing out of HTML.

  BLOCKED            403/429/timeout. Needs Apify with browser rendering and a residential
                     proxy — which is what BookMyShow already looked like.

Results are written to data/source_probe.json and are meant to be folded back into
sources.json with verified_at set.

    python probe_sources.py              probe every source
    python probe_sources.py --key allevents
"""
import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BASE, 'scout'))
import model  # noqa: E402

OUT = os.path.join(BASE, 'data', 'source_probe.json')
TIMEOUT = 25
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

# Candidate paths per source. Deliberately several per site — the point is to find which ones
# exist, not to assume. {city} is substituted where a source is city-scoped.
CANDIDATES = {
    'bookmyshow': ['/explore/home/mumbai', '/explore/events-mumbai',
                   '/explore/comedy-shows-mumbai', '/explore/music-shows-mumbai',
                   '/explore/theatre-shows-mumbai'],
    'district':   ['/events', '/mumbai/events', '/comedy', '/mumbai'],
    'insider':    ['/all-events', '/mumbai'],
    'skillbox':   ['/events', '/all-events', '/'],
    'townscript': ['/in/all-events', '/in/comedy', '/india/events', '/'],
    'allevents':  ['/mumbai/comedy', '/mumbai/music', '/mumbai/all',
                   '/bengaluru/comedy', '/mumbai/theatre'],
    'meraevents': ['/india-events', '/'],
    'highape':    ['/mumbai/events', '/bangalore/events', '/'],
}

JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)


def _walk(node, out):
    """schema.org graphs nest arbitrarily; collect every Event-ish object."""
    if isinstance(node, dict):
        t = node.get('@type')
        types = t if isinstance(t, list) else [t]
        if any(str(x).lower().endswith('event') for x in types if x):
            out.append(node)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)


def extract_events(html):
    events = []
    for blob in JSONLD.findall(html or ''):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        _walk(data, events)
    return events


def summarise(ev):
    loc = ev.get('location') or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    addr = (loc.get('address') or {}) if isinstance(loc, dict) else {}
    if isinstance(addr, str):
        addr = {'addressLocality': addr}
    return dict(title=ev.get('name'),
                venue=(loc.get('name') if isinstance(loc, dict) else None),
                city=addr.get('addressLocality') if isinstance(addr, dict) else None,
                date=(str(ev.get('startDate') or '')[:10]) or None,
                url=ev.get('url'))


def probe(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-IN,en;q=0.9'})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            html = r.read().decode('utf-8', 'replace')
            code = r.getcode()
    except urllib.error.HTTPError as e:
        return dict(url=url, status=e.code, ok=False, verdict='BLOCKED',
                    note=f'HTTP {e.code} {e.reason}')
    except Exception as e:                                          # noqa: BLE001
        return dict(url=url, status=None, ok=False, verdict='BLOCKED',
                    note=f'{type(e).__name__}: {str(e)[:90]}')

    events = extract_events(html)
    named = [summarise(e) for e in events if e.get('name')]
    verdict = 'DIRECT + JSON-LD' if named else 'DIRECT (needs HTML parsing)'
    return dict(url=url, status=code, ok=True, verdict=verdict,
                bytes=len(html), n_jsonld_events=len(named),
                sample=named[:3],
                note=f'{len(named)} structured event(s) on the page')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--key', help='probe only this source key')
    a = ap.parse_args(argv)

    srcs = model.load_sources()['sources']
    if a.key:
        srcs = [s for s in srcs if s['key'] == a.key]

    print('Source discovery probe — plain HTTP, no Apify')
    print('=' * 74)
    results = {}
    for s in srcs:
        key, dom = s['key'], s['domain']
        print(f'\n{s["name"]}  ({dom})   tier={s["tier"]}'
              + ('   [PRIMARY]' if s.get('primary') else ''))
        rows = []
        for path in CANDIDATES.get(key, ['/']):
            r = probe(f'https://{dom}{path}')
            rows.append(r)
            mark = {'DIRECT + JSON-LD': 'BEST', 'DIRECT (needs HTML parsing)': 'ok  ',
                    'BLOCKED': 'FAIL'}[r['verdict']]
            print(f'  [{mark}] {path:34} {str(r["status"]):>4}  {r["note"][:44]}')
            for ev in (r.get('sample') or [])[:2]:
                print(f'           · {str(ev["title"])[:44]:46} '
                      f'{str(ev["venue"])[:22]:24} {ev["date"]}')
        best = next((r for r in rows if r['verdict'] == 'DIRECT + JSON-LD'),
                    next((r for r in rows if r['ok']), rows[0] if rows else None))
        results[key] = dict(domain=dom, probed_at=dt.date.today().isoformat(),
                            best_verdict=best['verdict'] if best else 'BLOCKED',
                            working_paths=[r['url'] for r in rows if r['ok']],
                            blocked_paths=[r['url'] for r in rows if not r['ok']],
                            rows=rows)

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(dict(probed_at=dt.datetime.now().isoformat(timespec='seconds'),
                       sources=results), f, indent=1, ensure_ascii=False)

    print('\n' + '=' * 74)
    print('VERDICT PER SOURCE')
    for key, r in results.items():
        print(f'  {key:14} {r["best_verdict"]:28} '
              f'{len(r["working_paths"])} of '
              f'{len(r["working_paths"]) + len(r["blocked_paths"])} paths reachable')
    print(f'\nwritten -> {OUT}')
    print('Fold the working paths into data/sources.json and set verified_at.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

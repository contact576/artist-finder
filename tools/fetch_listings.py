"""Fetch listings directly, in Python, from every source that will serve us.

THE DISCOVERY THAT MADE THIS FILE POSSIBLE (probe run 2026-08-19).
The original design assumed Claude had to do all the crawling because "Python cannot crawl".
That was too strong. Python cannot call MCP tools — but it can make plain HTTP requests, and
three of the eight sources answer a plain request AND publish schema.org Event objects as
JSON-LD:

    allevents.in    15-64 structured events per city/category page, every path reachable
    highape.com     265 structured events on a single page
    district.in     12 on /events

For those, everything the scout needs — title, venue, city, date, url — arrives already
structured. No Apify, no cost, no fragile text parsing, no LLM in the loop. That is strictly
better than crawling, so it is what the weekly job does first.

WHAT NEEDS APIFY, AND THE MEASUREMENT THAT DEMOTED BOOKMYSHOW.
    bookmyshow      403 to plain HTTP; needs a browser on an Indian IP. It IS India's biggest
                    ticketing site, but what it EXPOSES is a 10-item JSON-LD teaser of featured
                    events — the real grid renders client-side. Measured across 3 rendered
                    pages: 30 events, only 9 upcoming, and comedy-shows-bengaluru returned
                    ZERO. That is ~3 usable rows per page against 15-64 free ones from
                    AllEvents. Worse for this tool specifically, the featured carousel skews to
                    acts that are ALREADY BIG — the ESTABLISHED end — while the artists this
                    scout exists to find are on the self-serve platforms. So it is
                    SUPPLEMENTARY, kept small because it does catch arena and festival
                    bookings the long tail misses.
    skillbox        200 but no JSON-LD; the listings need parsing out of HTML.
    townscript      same. Self-serve, so likely the best remaining prize.
    meraevents      same, and /india-events is now a 404.
    insider.in      502 on everything. Dead — superseded by District. Disabled in sources.json.

TWO WAYS TO REACH A BLOCKED SITE, AND NEITHER NEEDS A NEW CREDENTIAL.
Apify datasets are readable over plain HTTP WITHOUT a token, and Claude can start actor runs
through its own MCP connection. So the default division of labour is:

    Claude    starts the run via MCP, hands over the dataset id
    Python    `--apify-dataset <id>` reads it, parses, writes data/raw/

Setting `apify_token` in data/config.json additionally lets Python start the run itself, which
is tidier unattended but adds no capability. Everything lands in data/raw/ in one shape, and
run_weekly.py cannot tell the routes apart.

    python fetch_listings.py                    all direct sources, all cities
    python fetch_listings.py --key allevents
    python fetch_listings.py --cities Mumbai,Delhi --dry-run
"""
import argparse
import datetime as dt
import html as htmllib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(BASE, 'scout'))
import model  # noqa: E402

RAW = os.path.join(BASE, 'data', 'raw')
TIMEOUT = 25
PAUSE = 0.7            # be a polite guest; these are free pages served to us in good faith

# Two different category pages sharing more than this fraction of their events means the site
# is ignoring the category segment. Measured: Mumbai 0%, Bengaluru 92% — a wide gap, so the
# threshold is not delicate.
CATEGORY_BLEED = 0.5

# Used to rescue a city out of a full postal address that a site has put in `addressLocality`.
KNOWN_CITIES = ('mumbai', 'new delhi', 'delhi', 'bengaluru', 'bangalore', 'hyderabad',
                'chennai', 'kolkata', 'pune', 'ahmedabad', 'chandigarh', 'jaipur', 'kochi',
                'indore', 'gurugram', 'gurgaon', 'noida', 'goa', 'surat', 'nagpur', 'lucknow',
                'bhopal', 'coimbatore', 'thane', 'navi mumbai')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')

JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)

# Verified 2026-08-19 by tools/probe_sources.py. City slugs are the site's own, not ours.
PLAN = {
    'allevents': dict(
        # PUNE IS DELIBERATELY ABSENT. /pune 404s and /pune-city 302-redirects to /pine-city,
        # a different place on another continent. Five wasted requests a week for nothing.
        # Restore it the moment a working slug turns up.
        cities=['mumbai', 'new-delhi', 'bengaluru', 'hyderabad', 'chennai', 'kolkata',
                'ahmedabad', 'chandigarh', 'jaipur', 'kochi', 'indore'],
        # path -> the category label handed to genres.classify_genre(). That label is the
        # PRIMARY genre signal, so getting it from the URL is worth more than any title guess.
        paths=[('/{city}/comedy', 'comedy'),
               ('/{city}/music', 'music'),
               ('/{city}/theatre', 'theatre'),
               ('/{city}/parties', 'nightlife'),
               ('/{city}/all', None)]),
    'highape': dict(
        cities=[None],
        paths=[('/', None), ('/mumbai/events', None), ('/bangalore/events', None)]),
    'district': dict(
        cities=[None],
        paths=[('/events', None)]),
    # BLOCKED to plain HTTP (403) but serves the same JSON-LD once a browser renders it.
    # Verified 2026-08-19 on /explore/comedy-shows-mumbai: HTTP 200, ItemList of Events with
    # name, startDate, location.name, location.address.addressLocality and offers.availability.
    # Note the `in.` host — bookmyshow.com without it is not the Indian listing site.
    'bookmyshow': dict(
        via='apify', host='in.bookmyshow.com',
        cities=['mumbai', 'delhi-ncr', 'bengaluru', 'hyderabad', 'chennai', 'kolkata',
                'pune', 'ahmedabad', 'chandigarh', 'jaipur', 'kochi', 'indore'],
        paths=[('/explore/comedy-shows-{city}', 'comedy'),
               ('/explore/music-shows-{city}', 'music'),
               ('/explore/theatre-shows-{city}', 'theatre'),
               ('/explore/events-{city}', None)]),
}


def _get(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-IN,en;q=0.9'})
    with urllib.request.urlopen(req, timeout=TIMEOUT,
                                context=ssl.create_default_context()) as r:
        return r.read().decode('utf-8', 'replace')


# ---------------------------------------------------------------- Apify, for blocked sites

APIFY_ACTOR = 'apify~website-content-crawler'
APIFY_API = 'https://api.apify.com/v2'
APIFY_POLL, APIFY_MAX_WAIT = 6, 420


def _apify_token():
    cfg = os.path.join(BASE, 'data', 'config.json')
    if not os.path.exists(cfg):
        return None
    with open(cfg, encoding='utf-8') as f:
        return (json.load(f) or {}).get('apify_token') or None


def apify_get_html(urls, token=None, country='IN', timeout=APIFY_MAX_WAIT):
    """Render blocked pages through Apify and hand back their HTML.

    WHY PYTHON CALLS APIFY DIRECTLY RATHER THAN ROUTING IT THROUGH CLAUDE.
    Verified 2026-08-19: BookMyShow serves the SAME schema.org JSON-LD as the free sources — it
    just refuses a plain request (403) and needs a real browser on an Indian IP. Once the HTML
    is in hand, `parse_events()` reads it identically to AllEvents. Pushing 40KB of markup
    through a language model every week to do that would be slower, dearer and less reliable
    than one HTTP call.

    So Apify contributes exactly two things here: a browser and an Indian IP. Everything
    downstream is the same deterministic parser, which is what keeps BookMyShow honest.

    Returns (list_of_html, note). Never raises — a failed render must not take down the run.
    """
    token = token or _apify_token()
    if not token:
        return [], 'apify_token not set in data/config.json — blocked sources skipped'

    payload = dict(
        startUrls=[{'url': u} for u in urls],
        crawlerType='playwright:firefox',
        maxCrawlDepth=0, maxCrawlPages=len(urls),
        saveHtml=True, saveMarkdown=False,
        proxyConfiguration={'useApifyProxy': True,
                            'apifyProxyGroups': ['RESIDENTIAL'],
                            'apifyProxyCountry': country})

    def _api(method, path, body=None):
        sep = '&' if '?' in path else '?'
        req = urllib.request.Request(
            f'{APIFY_API}{path}{sep}token={token}',
            data=(json.dumps(body).encode() if body is not None else None), method=method)
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode('utf-8'))

    try:
        run = _api('POST', f'/acts/{APIFY_ACTOR}/runs?memory=4096', payload)['data']
    except Exception as e:                                          # noqa: BLE001
        return [], f'apify start failed: {type(e).__name__}'

    run_id, waited = run['id'], 0
    while waited < timeout:
        time.sleep(APIFY_POLL)
        waited += APIFY_POLL
        try:
            run = _api('GET', f'/actor-runs/{run_id}')['data']
        except Exception:                                           # noqa: BLE001
            continue
        if run['status'] in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
            break
    if run.get('status') != 'SUCCEEDED':
        return [], f'apify run {run.get("status", "did not finish")} after {waited}s'

    try:
        items = _api('GET', f'/datasets/{run["defaultDatasetId"]}/items?clean=true&fields=html')
    except Exception as e:                                          # noqa: BLE001
        return [], f'apify dataset read failed: {type(e).__name__}'
    html = [i.get('html') or '' for i in (items or []) if i.get('html')]
    return html, f'{len(html)} page(s) rendered via apify in {waited}s'


def _walk(node, out):
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


def _clean(s):
    """Unescape HTML entities and collapse whitespace.

    Not cosmetic: the raw feed contains '&amp;', and 'Vishal &amp; Rekha Bhardwaj' would split
    on the wrong token and produce two mangled artists instead of one duo.
    """
    if s is None:
        return None
    return re.sub(r'\s+', ' ', htmllib.unescape(str(s))).strip() or None


def _offer(ev):
    """Price and availability from schema.org Offer, when the site bothers to publish it."""
    o = ev.get('offers')
    if isinstance(o, list):
        o = o[0] if o else None
    if not isinstance(o, dict):
        return None, None, None
    price = o.get('price') or o.get('lowPrice')
    high = o.get('highPrice') or price
    avail = (o.get('availability') or '')
    status = None
    if avail:
        a = str(avail).lower()
        status = ('Sold Out' if 'soldout' in a or 'outofstock' in a
                  else 'Available' if 'instock' in a else None)

    def num(x):
        try:
            return float(str(x).replace(',', ''))
        except Exception:
            return None
    return num(price), num(high), status


def parse_events(html, default_category=None, city_hint=None):
    """Extract events from raw page HTML by finding its JSON-LD script blocks."""
    raw = []
    for blob in JSONLD.findall(html or ''):
        try:
            data = json.loads(blob.strip())
        except Exception:
            continue
        _walk(data, raw)
    return events_from_jsonld(raw, default_category, city_hint)


def events_from_jsonld(raw, default_category=None, city_hint=None):
    """Same extraction, but starting from ALREADY-PARSED JSON-LD objects.

    Apify's website-content-crawler returns a CLEANED `html` field with script tags stripped,
    so `parse_events` finds nothing in it — but the crawler has already parsed the JSON-LD into
    `metadata.jsonLd`, which is strictly better. This entry point takes that directly, and is
    why the BookMyShow path needs no HTML parsing at all.
    """
    out, seen = [], set()
    for ev in raw:
        name = _clean(ev.get('name'))
        if not name:
            continue
        loc = ev.get('location')
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        loc = loc if isinstance(loc, dict) else {}
        addr = loc.get('address')
        if isinstance(addr, str):
            addr = {'addressLocality': addr}
        addr = addr if isinstance(addr, dict) else {}

        venue = _clean(loc.get('name'))
        # addressLocality is SUPPOSED to hold a city. BookMyShow puts the whole postal address
        # in it — "NC Kelkar Road, Dadar West, Mumbai, Maharashtra 400028, India" — which then
        # gets stored as the city and makes per-city grouping meaningless. If it does not look
        # like a city, pull a known one out of it; failing that, fall back to the URL slug.
        locality = _clean(addr.get('addressLocality'))
        if locality and (',' in locality or len(locality) > 28):
            hit = next((c for c in KNOWN_CITIES
                        if re.search(r'\b' + re.escape(c) + r'\b', locality, re.I)), None)
            locality = hit.title() if hit else None
        city = locality or city_hint
        # AllEvents packs "Venue: City" into location.name. Always split it off — a venue
        # string carrying the city defeats the venue map, which matches on room names.
        if venue and ':' in venue:
            head, tail = venue.rsplit(':', 1)
            tail = _clean(tail)
            if head.strip() and tail and len(tail) <= 24:
                venue = _clean(head)
                city = city or tail

        pmin, pmax, status = _offer(ev)
        url = _clean(ev.get('url'))
        key = (name.lower(), (venue or '').lower(), str(ev.get('startDate') or '')[:10])
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(title=name, venue=venue, city=city,
                        url=url, date=(str(ev.get('startDate') or '')[:10]) or None,
                        status=status, category=default_category,
                        price_min=pmin, price_max=pmax))
    return out


def read_apify_dataset(dataset_id, category_map=None, verbose=True):
    """Read an Apify dataset that somebody else already ran, and parse it into events.

    THIS IS THE ROUTE THAT NEEDS NO CREDENTIALS AT ALL.
    Apify datasets are readable over plain HTTP without a token, and Claude can start actor
    runs through its own MCP connection. So the division of labour for a blocked site is:

        Claude    starts the run via MCP, and hands over the dataset id
        Python    reads the dataset, parses the JSON-LD, writes data/raw/

    No `apify_token` required. Setting one only lets Python ALSO start the run itself
    (`apify_get_html`), which is tidier for unattended use but adds nothing to what is possible.

    `category_map` is {url_fragment: category} so the genre label still comes from the URL the
    page was crawled at — matched against each item's own canonical URL, because a dataset does
    not promise to preserve input order.
    """
    url = (f'{APIFY_API}/datasets/{dataset_id}/items?clean=true'
           f'&fields=metadata')
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            items = json.loads(r.read().decode('utf-8'))
    except Exception as e:                                          # noqa: BLE001
        return [], f'could not read dataset {dataset_id}: {type(e).__name__}'

    events = []
    for it in (items or []):
        md = it.get('metadata') or {}
        page = md.get('canonicalUrl') or ''
        cat = next((c for frag, c in (category_map or {}).items() if frag and frag in page),
                   None)
        # City comes from the URL slug (".../comedy-shows-mumbai"), never from the address
        # field — BookMyShow puts a full postal string there and it was being stored as a city.
        city = None
        slug = page.rsplit('/', 1)[-1] if page else ''
        for known in (PLAN.get('bookmyshow', {}).get('cities') or []):
            if slug.endswith('-' + known):
                city = known.replace('-', ' ').title()
                break
        raw = []
        _walk(md.get('jsonLd'), raw)
        found = events_from_jsonld(raw, cat, city_hint=city)
        if verbose:
            print(f'    {len(found):4} events  cat={str(cat):10} {page[:56]}')
        events += found

    # Venue landing pages get listed as Events with a long-past startDate. They are not shows.
    today = dt.date.today().isoformat()
    fresh = [e for e in events if not e['date'] or e['date'] >= today]
    dropped = len(events) - len(fresh)

    best = {}
    for ev in fresh:
        k = (ev['title'].lower(), (ev['venue'] or '').lower(), ev['date'])
        if k not in best or (ev['category'] and not best[k]['category']):
            best[k] = ev
    note = f'{len(best)} unique upcoming events from {len(items or [])} page(s)'
    if dropped:
        note += f'; dropped {dropped} past-dated row(s) (venue pages, not shows)'
    return list(best.values()), note


def fetch_via_apify(key, cities=None, verbose=True):
    """Blocked sources: render every page in one Apify run, then parse locally.

    ONE run for all the pages rather than one per page — Apify bills per run start as well as
    per compute unit, and a browser launched once and reused across a batch is far cheaper than
    twelve cold starts. The category label still comes from the URL, exactly as elsewhere.
    """
    plan = PLAN[key]
    host = plan.get('host') or next(
        s['domain'] for s in model.load_sources()['sources'] if s['key'] == key)
    use_cities = [c for c in plan['cities']
                  if not cities or c.lower() in
                  {x.strip().lower().replace(' ', '-') for x in cities}]

    jobs = []          # (url, category)
    for city in (use_cities or [None]):
        for path, cat in plan['paths']:
            p = path.format(city=city) if city and '{city}' in path else path
            if '{city}' in p:
                continue
            jobs.append((f'https://{host}{p}', cat))

    if verbose:
        print(f'    rendering {len(jobs)} page(s) through apify (browser + IN residential '
              f'proxy)...')
    html_pages, note = apify_get_html([u for u, _ in jobs])
    if verbose:
        print(f'    {note}')
    if not html_pages:
        return [], [host], 0, [(key, note)]

    # Apify does not guarantee dataset order matches input order, so the category cannot be
    # zipped positionally. Re-derive it from each page's own canonical URL instead.
    events, domains = [], {host}
    for html in html_pages:
        m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', html or '',
                      re.I)
        page_url = m.group(1) if m else ''
        cat = next((c for u, c in jobs if u.rstrip('/') in page_url.rstrip('/')), None)
        city = next((c for c in (use_cities or []) if c and c in page_url), None)
        found = parse_events(html, cat,
                             city_hint=(city or '').replace('-', ' ').title() or None)
        for ev in found:
            if ev.get('url'):
                try:
                    domains.add(urllib.parse.urlparse(ev['url']).netloc.lower())
                except Exception:
                    pass
        events += found

    best = {}
    for ev in events:
        k = (ev['title'].lower(), (ev['venue'] or '').lower(), ev['date'])
        if k not in best or (ev['category'] and not best[k]['category']):
            best[k] = ev
    return list(best.values()), sorted(domains), len(html_pages), []


def fetch_source(key, cities=None, verbose=True):
    plan = PLAN[key]
    if plan.get('via') == 'apify':
        return fetch_via_apify(key, cities, verbose)
    src = next(s for s in model.load_sources()['sources'] if s['key'] == key)
    dom = src['domain']
    use_cities = [c for c in plan['cities']
                  if not cities or c is None or c.lower() in
                  {x.strip().lower().replace(' ', '-') for x in cities}]
    events, domains, pages, failures = [], {dom}, 0, []

    def keyset(rows):
        return {(r['title'].lower(), (r['venue'] or '').lower(), r['date']) for r in rows}

    for city in (use_cities or [None]):
        per_city, by_cat = [], {}
        ordered = sorted(plan['paths'], key=lambda pc: pc[1] is not None)
        for path, cat in ordered:
            p = path.format(city=city) if city and '{city}' in path else path
            if '{city}' in p:
                continue
            url = f'https://{dom}{p}'
            try:
                html = _get(url)
            except Exception as e:                                   # noqa: BLE001
                failures.append((p, f'{type(e).__name__}'))
                if verbose:
                    print(f'    [fail] {p:32} {type(e).__name__}')
                continue
            pages += 1
            found = parse_events(html, cat, city_hint=(city or '').replace('-', ' ').title()
                                 or None)
            if cat:
                by_cat[cat] = keyset(found)
            for ev in found:
                if ev.get('url'):
                    try:
                        domains.add(urllib.parse.urlparse(ev['url']).netloc.lower())
                    except Exception:
                        pass
            per_city += found
            if verbose:
                print(f'    [ok  ] {p:32} {len(found):4} events')
            time.sleep(PAUSE)

        # DOES THE CATEGORY SEGMENT ACTUALLY FILTER? Compare category pages against EACH OTHER,
        # never against /all — a category page is SUPPOSED to be a subset of /all, so that
        # comparison proves nothing and an earlier version of this guard fired on the wrong
        # cities because of it. Measured 2026-08-19: Mumbai /comedy vs /music overlap 0% (real
        # filtering); Bengaluru 92% (the segment is ignored and the same 64 events are served
        # whatever you ask for). Believing Bengaluru's label would file every musician in the
        # city as a comedian — and since the platform category is the PRIMARY genre signal,
        # that error would propagate all the way to the ranking.
        cats = [c for c in by_cat if by_cat[c]]
        worst, pair = 0.0, None
        for i, x in enumerate(cats):
            for y in cats[i + 1:]:
                ov = len(by_cat[x] & by_cat[y]) / max(min(len(by_cat[x]), len(by_cat[y])), 1)
                if ov > worst:
                    worst, pair = ov, (x, y)
        if worst > CATEGORY_BLEED:
            for r in per_city:
                r['category'] = None
            if verbose:
                print(f'    [warn] {city or dom}: /{pair[0]} and /{pair[1]} share '
                      f'{worst:.0%} of their events — the category segment is not filtering, '
                      f'so all labels dropped for this city')
        events += per_city

    # One show can appear on both /comedy and /all. Dedupe, preferring the row that carries a
    # category — the categorised copy is the one that classifies correctly.
    best = {}
    for ev in events:
        k = (ev['title'].lower(), (ev['venue'] or '').lower(), ev['date'])
        if k not in best or (ev['category'] and not best[k]['category']):
            best[k] = ev
    return list(best.values()), sorted(domains), pages, failures


def write_raw(key, events, domains, date):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f'{date}__{key}.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dict(events=events, domains_seen=domains,
                       fetched_by='tools/fetch_listings.py (direct JSON-LD)'),
                  f, indent=1, ensure_ascii=False)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--key', help='one source only')
    ap.add_argument('--cities', help='comma-separated city filter')
    ap.add_argument('--date', default=dt.date.today().isoformat())
    ap.add_argument('--dry-run', action='store_true', help='fetch and report, write nothing')
    ap.add_argument('--apify-dataset',
                    help='read an Apify dataset id that Claude already ran (no token needed)')
    ap.add_argument('--source', default='bookmyshow',
                    help='which source --apify-dataset belongs to')
    a = ap.parse_args(argv)

    if a.apify_dataset:
        print(f'Reading Apify dataset {a.apify_dataset} for "{a.source}"')
        print('=' * 70)
        cmap = {frag: cat for frag, cat in
                [(p.split('{city}')[0].rstrip('-'), c)
                 for p, c in PLAN.get(a.source, {}).get('paths', []) if c]}
        events, note = read_apify_dataset(a.apify_dataset, cmap)
        print(f'  {note}')
        if events:
            withcat = sum(1 for e in events if e['category'])
            print(f'  category {withcat}/{len(events)} · '
                  f'venue {sum(1 for e in events if e["venue"])}/{len(events)} · '
                  f'date {sum(1 for e in events if e["date"])}/{len(events)}')
            for e in events[:6]:
                print(f'    {e["title"][:44]:46} {str(e["venue"])[:20]:22} '
                      f'{e["city"] or "-":12} {e["date"]}')
        if not a.dry_run and events:
            dom = next((s['domain'] for s in model.load_sources()['sources']
                        if s['key'] == a.source), a.source)
            print(f'  -> {write_raw(a.source, events, [dom], a.date)}')
        elif a.dry_run:
            print('  --dry-run: nothing written.')
        return 0

    keys = [a.key] if a.key else list(PLAN)
    cities = [c for c in (a.cities or '').split(',') if c.strip()] or None

    print(f'Direct listing fetch — {a.date}')
    print('=' * 70)
    print('Sources that serve plain HTTP with JSON-LD. BookMyShow, Skillbox, Townscript and')
    print('MeraEvents are NOT here — they need Apify via the skill. See the module docstring.')

    total = 0
    for key in keys:
        print(f'\n{key}')
        events, domains, pages, failures = fetch_source(key, cities)
        total += len(events)
        print(f'  -> {len(events)} unique events from {pages} page(s)'
              + (f', {len(failures)} failed' if failures else ''))
        withcat = sum(1 for e in events if e['category'])
        withven = sum(1 for e in events if e['venue'])
        withdate = sum(1 for e in events if e['date'])
        print(f'     category {withcat}/{len(events)} · venue {withven}/{len(events)} '
              f'· date {withdate}/{len(events)}')
        if events:
            print('     sample:')
            for e in events[:3]:
                print(f'       {e["title"][:44]:46} {str(e["venue"])[:20]:22} '
                      f'{e["city"] or "—":12} {e["date"]}')
        if not a.dry_run and events:
            print(f'     -> {write_raw(key, events, domains, a.date)}')

    print(f'\n{total} events total.')
    if a.dry_run:
        print('--dry-run: nothing written.')
    else:
        print('Next: run_weekly.py ingests data/raw/ and scores it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

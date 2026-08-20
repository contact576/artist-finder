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

WHAT NEEDS APIFY, AND THE LIMIT ON BOOKMYSHOW CLAIMS.
    bookmyshow      403 to plain HTTP; needs a browser on an Indian IP. It is a PRIMARY
                    VALIDATION source alongside District, but it is intentionally optional in
                    unattended fetches until an Apify credential or a supplied dataset is
                    available. Its old grid JSON-LD is a ten-item teaser, so this adapter never
                    treats grid JSON-LD as evidence. It renders bounded category grids, follows
                    only their event-detail links, and reads standard Event JSON-LD from those
                    detail pages. The strategy is fixture-tested, not yet live-validated with a
                    token as of 2026-08-21.
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

# District activity pages are useful only when their route labels survive into the raw rows.
# These are the routes observed on 2026-08-21: five Mumbai taxonomies plus comedy in the other
# three confirmed metros.  The /events endpoint remains a no-category fallback.  Nine requests
# is deliberately bounded; this is a coverage sample, not an absence claim outside these routes.
DISTRICT_ACTIVITY_ROUTES = (
    ('comedy-shows', 'mumbai', 'comedy'),
    ('music', 'mumbai', 'music'),
    ('nightlife', 'mumbai', 'nightlife'),
    ('sports', 'mumbai', 'sports'),
    ('performances', 'mumbai', 'theatre'),
    ('comedy-shows', 'bengaluru', 'comedy'),
    ('comedy-shows', 'hyderabad', 'comedy'),
    ('comedy-shows', 'delhi-ncr', 'comedy'),
)
DISTRICT_FALLBACK = '/events'

# BookMyShow is deliberately two-stage.  The first twelve category grids discover links; the
# second request set contains only event detail pages found on those grids.  This makes the
# request cost bounded and stops the ten-item grid teaser from contaminating validation data.
BMS_GRID_PAGE_BUDGET = 12
BMS_DETAIL_PAGE_BUDGET = 36
BMS_GRID_CITIES = ('mumbai', 'delhi-ncr', 'bengaluru', 'hyderabad')
BMS_GRID_PATHS = (
    ('/explore/comedy-shows-{city}', 'comedy'),
    ('/explore/music-shows-{city}', 'music'),
    ('/explore/theatre-shows-{city}', 'theatre'),
)
INDIA_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30))

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
        paths=[(f'/activities/{activity}-in-{city}', category)
               for activity, city, category in DISTRICT_ACTIVITY_ROUTES]
              + [(DISTRICT_FALLBACK, None)]),
    # BLOCKED to plain HTTP (403) but serves the same JSON-LD once a browser renders it.
    # Verified 2026-08-19 on /explore/comedy-shows-mumbai: HTTP 200, ItemList of Events with
    # name, startDate, location.name, location.address.addressLocality and offers.availability.
    # Note the `in.` host — bookmyshow.com without it is not the Indian listing site.
    'bookmyshow': dict(
        via='apify', host='in.bookmyshow.com',
        cities=list(BMS_GRID_CITIES),
        paths=list(BMS_GRID_PATHS)),
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


NEXT_PUSH = re.compile(r'self\.__next_f\.push\((\[.*?\])\)', re.S)


def _event_key(event):
    return (str(event.get('title') or '').casefold(),
            str(event.get('venue') or '').casefold(), event.get('date') or '')


def dedupe_events(events):
    """Keep one deterministic row per title, room and date, preferring route evidence."""
    best = {}
    for event in events:
        if not event.get('title'):
            continue
        key = _event_key(event)
        previous = best.get(key)
        if (previous is None
                or (event.get('category') and not previous.get('category'))
                or (event.get('url') and not previous.get('url'))):
            best[key] = event
    return [best[key] for key in sorted(best)]


def _date_from_epoch(value):
    """District publishes an epoch; convert it in India time rather than guessing UTC dates."""
    try:
        stamp = float(value)
        if stamp > 100_000_000_000:       # defensive support for milliseconds
            stamp /= 1000
        if stamp <= 0:
            return None
        return dt.datetime.fromtimestamp(stamp, tz=INDIA_TZ).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _next_flight_text(html):
    """Decode the string chunks emitted by Next.js' self.__next_f.push transport."""
    chunks = []
    for payload in NEXT_PUSH.findall(html or ''):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(decoded, list):
            continue
        chunks.extend(part for part in decoded[1:] if isinstance(part, str))
    return ''.join(chunks)


def _eventdata_values(flight):
    """Yield each JSON value following an EventData field in a decoded Flight stream."""
    decoder = json.JSONDecoder()
    seen = set()
    for found in re.finditer(r'"EventData"\s*:\s*', flight):
        try:
            value, _ = decoder.raw_decode(flight, found.end())
        except json.JSONDecodeError:
            continue
        marker = json.dumps(value, sort_keys=True, ensure_ascii=True)
        if marker not in seen:
            seen.add(marker)
            yield value


def _district_records(value):
    """EventData shape changes by route, so find only complete record-shaped dictionaries."""
    if isinstance(value, dict):
        if value.get('name') and value.get('event_slug') and value.get('start_time_epoch'):
            yield value
        for child in value.values():
            yield from _district_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _district_records(child)


def _district_url(slug):
    slug = _clean(slug)
    if not slug:
        return None
    if slug.startswith(('https://', 'http://')):
        return slug
    if slug.startswith('/'):
        return f'https://www.district.in{slug}'
    if slug.startswith('events/'):
        return f'https://www.district.in/{slug}'
    return f'https://www.district.in/events/{slug}'


def district_events_from_next(html, category=None, city_hint=None):
    """Parse official District activity-page EventData without making status or genre guesses.

    Confirmed 2026-08-21: activity pages expose self.__next_f.push chunks containing EventData
    records with name, event_slug, start_time_epoch, city, venue_name and is_available.  False
    availability is deliberately UNKNOWN: it could mean sales closed, cancellation or a UI state,
    and is not evidence of a sold-out show.
    """
    events = []
    for value in _eventdata_values(_next_flight_text(html)):
        for record in _district_records(value):
            title = _clean(record.get('name'))
            if not title:
                continue
            events.append(dict(
                title=title,
                venue=_clean(record.get('venue_name')),
                city=_clean(record.get('city')) or city_hint,
                url=_district_url(record.get('event_slug')),
                date=_date_from_epoch(record.get('start_time_epoch')),
                status='Available' if record.get('is_available') is True else None,
                category=category,
                price_min=None,
                price_max=None,
            ))
    return dedupe_events(events)


def parse_district_page(html, category=None, city_hint=None):
    """Use structured Flight records first, retaining /events JSON-LD as a safe fallback."""
    return dedupe_events(district_events_from_next(html, category, city_hint)
                         + parse_events(html, category, city_hint))


def _normal_url(url):
    if not url:
        return None
    parsed = urllib.parse.urlsplit(str(url))
    if not parsed.scheme or not parsed.netloc:
        return None
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                                    parsed.path.rstrip('/'), '', ''))


def _bms_detail_url(url):
    parsed = urllib.parse.urlsplit(str(url or ''))
    host = (parsed.hostname or '').lower()
    trusted_host = host == 'bookmyshow.com' or host.endswith('.bookmyshow.com')
    return (parsed.scheme in ('http', 'https')
            and trusted_host
            and '/events/' in parsed.path.lower())


def bms_detail_links(html, page_url, category=None):
    """Collect only BookMyShow event-detail links from one rendered category grid."""
    base = page_url or 'https://in.bookmyshow.com/'
    found = {}
    patterns = (
        r'\bhref\s*=\s*["\']([^"\']+)["\']',
        r'["\']href["\']\s*:\s*["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, html or '', re.I):
            url = _normal_url(urllib.parse.urljoin(base, htmllib.unescape(match.group(1))))
            if url and _bms_detail_url(url):
                existing = found.get(url)
                if existing is None or (category and not existing):
                    found[url] = category
    return found


def _jsonld_from_metadata(metadata):
    raw = metadata.get('jsonLd') if isinstance(metadata, dict) else None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    events = []
    _walk(raw, events)
    return events


def _events_from_rendered_page(row, category=None, city_hint=None):
    events = parse_events(row.get('html') or '', category, city_hint)
    events += events_from_jsonld(_jsonld_from_metadata(row.get('metadata') or {}),
                                 category, city_hint)
    return dedupe_events(events)


def apify_render_pages(urls, token=None, country='IN', timeout=APIFY_MAX_WAIT):
    """Render an explicit URL set.  Callers, not crawler depth, control which links are followed."""
    token = _apify_token() if token is None else token
    if not token:
        return [], 'UNCONFIGURED: apify_token not set in data/config.json; no BookMyShow run made'
    payload = dict(
        startUrls=[{'url': url} for url in urls],
        crawlerType='playwright:firefox',
        maxCrawlDepth=0,
        maxCrawlPages=len(urls),
        saveHtml=True,
        saveMarkdown=False,
        proxyConfiguration={'useApifyProxy': True,
                            'apifyProxyGroups': ['RESIDENTIAL'],
                            'apifyProxyCountry': country},
    )

    def call(method, path, body=None):
        sep = '&' if '?' in path else '?'
        request = urllib.request.Request(
            f'{APIFY_API}{path}{sep}token={token}',
            data=(json.dumps(body).encode() if body is not None else None), method=method)
        request.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode('utf-8'))

    try:
        run = call('POST', f'/acts/{APIFY_ACTOR}/runs?memory=4096', payload)['data']
    except Exception as exc:  # noqa: BLE001
        return [], f'apify start failed: {type(exc).__name__}'

    waited = 0
    while waited < timeout:
        time.sleep(APIFY_POLL)
        waited += APIFY_POLL
        try:
            run = call('GET', f'/actor-runs/{run["id"]}')['data']
        except Exception:  # noqa: BLE001
            continue
        if run.get('status') in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
            break
    if run.get('status') != 'SUCCEEDED':
        return [], f'apify run {run.get("status", "did not finish")} after {waited}s'

    try:
        items = call('GET', f'/datasets/{run["defaultDatasetId"]}/items?clean=true'
                     '&fields=html,metadata')
    except Exception as exc:  # noqa: BLE001
        return [], f'apify dataset read failed: {type(exc).__name__}'
    pages = []
    for item in items or []:
        metadata = item.get('metadata') or {}
        pages.append(dict(
            url=_normal_url(metadata.get('canonicalUrl') or metadata.get('loadedUrl')
                            or metadata.get('url')),
            html=item.get('html') or item.get('content') or '',
            metadata=metadata,
        ))
    return pages, f'{len(pages)} page(s) rendered via apify in {waited}s'


def _district_city_requested(city, requested):
    if not requested:
        return True
    wanted = {entry.strip().lower().replace(' ', '-') for entry in requested}
    return city in wanted or (city == 'delhi-ncr' and 'delhi' in wanted)


def _drop_bleeding_categories(rows_by_city, verbose):
    result = []
    for city, rows in rows_by_city.items():
        by_category = {}
        for row in rows:
            if row.get('category'):
                by_category.setdefault(row['category'], set()).add(_event_key(row))
        worst, pair = 0.0, None
        names = sorted(name for name, keys in by_category.items() if keys)
        for index, left in enumerate(names):
            for right in names[index + 1:]:
                overlap = len(by_category[left] & by_category[right]) / max(
                    min(len(by_category[left]), len(by_category[right])), 1)
                if overlap > worst:
                    worst, pair = overlap, (left, right)
        if worst > CATEGORY_BLEED:
            for row in rows:
                row['category'] = None
            if verbose:
                print(f'    [warn] {city}: {pair[0]}/{pair[1]} share {worst:.0%}; '
                      'route labels dropped')
        result.extend(rows)
    return result


def fetch_district(cities=None, verbose=True):
    """Fetch only the verified bounded activity routes plus the official /events fallback."""
    source = next(item for item in model.load_sources()['sources'] if item['key'] == 'district')
    domain = source['domain']
    jobs = [(f'/activities/{taxonomy}-in-{city}', category, city)
            for taxonomy, city, category in DISTRICT_ACTIVITY_ROUTES
            if _district_city_requested(city, cities)]
    jobs.append((DISTRICT_FALLBACK, None, None))
    events, failures, pages = [], [], 0
    rows_by_city = {}
    domains = {domain}
    for path, category, city in jobs:
        try:
            html = _get(f'https://{domain}{path}')
        except Exception as exc:  # noqa: BLE001
            failures.append((path, f'{type(exc).__name__}'))
            if verbose:
                print(f'    [fail] {path:42} {type(exc).__name__}')
            continue
        pages += 1
        found = parse_district_page(html, category,
                                    (city or '').replace('-', ' ').title() or None)
        if city:
            rows_by_city.setdefault(city, []).extend(found)
        else:
            events.extend(found)
        for event in found:
            parsed = urllib.parse.urlsplit(event.get('url') or '')
            if parsed.netloc:
                domains.add(parsed.netloc.lower())
        if verbose:
            print(f'    [ok  ] {path:42} {len(found):4} events')
        time.sleep(PAUSE)
    events.extend(_drop_bleeding_categories(rows_by_city, verbose))
    return dedupe_events(events), sorted(domains), pages, failures


def _category_for_url(url, category_map):
    return next((category for fragment, category in (category_map or {}).items()
                 if fragment and fragment in (url or '')), None)


def bms_events_from_items(items, category_map=None, verbose=True):
    """Read a reproducible two-depth Apify dataset: grids discover, details provide evidence."""
    links, details = {}, []
    for item in items or []:
        metadata = item.get('metadata') or {}
        page_url = _normal_url(metadata.get('canonicalUrl') or metadata.get('loadedUrl')
                                or metadata.get('url'))
        category = (_category_for_url(page_url, category_map)
                    or _category_for_url(metadata.get('requestUrl'), category_map))
        row = dict(url=page_url, html=item.get('html') or item.get('content') or '',
                   metadata=metadata)
        if _bms_detail_url(page_url):
            details.append((row, category))
            continue
        for url, linked_category in bms_detail_links(row['html'], page_url, category).items():
            links[url] = linked_category or links.get(url)

    events = []
    for row, category in details:
        if row['url'] not in links:
            continue
        category = category or links.get(row['url'])
        events.extend(_events_from_rendered_page(row, category))
    today = dt.date.today().isoformat()
    events = [event for event in dedupe_events(events)
              if not event.get('date') or event['date'] >= today]
    note = (f'{len(events)} unique upcoming events from {len(details)} rendered detail page(s); '
            f'{len(links)} grid detail link(s) discovered; grid teaser ignored')
    if not details:
        note = ('no BookMyShow event-detail pages in dataset; grid teaser ignored and no '
                'primary-validation evidence written')
    if verbose:
        print(f'    {note}')
    return events, note


def read_bookmyshow_apify_dataset(dataset_id, category_map=None, verbose=True):
    """Credential-free dataset import for a pre-run BookMyShow grid plus detail crawl."""
    url = f'{APIFY_API}/datasets/{dataset_id}/items?clean=true&fields=html,metadata'
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            items = json.loads(response.read().decode('utf-8'))
    except Exception as exc:  # noqa: BLE001
        return [], f'could not read dataset {dataset_id}: {type(exc).__name__}'
    return bms_events_from_items(items, category_map, verbose)


def _bookmyshow_grid_jobs(cities):
    wanted = {entry.strip().lower().replace(' ', '-') for entry in (cities or [])}
    jobs = []
    for city in BMS_GRID_CITIES:
        if wanted and city not in wanted and not (city == 'delhi-ncr' and 'delhi' in wanted):
            continue
        for path, category in BMS_GRID_PATHS:
            jobs.append((f'https://in.bookmyshow.com{path.format(city=city)}', category))
    return jobs[:BMS_GRID_PAGE_BUDGET]


def fetch_bookmyshow(cities=None, verbose=True):
    """Render category grids, then only their depth-one event links, never their ten-item teaser."""
    grids = _bookmyshow_grid_jobs(cities)
    pages, note = apify_render_pages([url for url, _ in grids])
    if not pages:
        kind = 'unconfigured' if note.startswith('UNCONFIGURED:') else 'bookmyshow'
        return [], ['in.bookmyshow.com'], 0, [(kind, note)]
    grid_categories = {_normal_url(url): category for url, category in grids}
    links = {}
    for page in pages:
        category = grid_categories.get(page.get('url'))
        for url, linked_category in bms_detail_links(page.get('html'), page.get('url'),
                                                      category).items():
            links[url] = linked_category or links.get(url)
    detail_jobs = sorted(links.items())[:BMS_DETAIL_PAGE_BUDGET]
    if not detail_jobs:
        return [], ['in.bookmyshow.com'], len(pages), [
            ('bookmyshow', 'no event-detail links found on rendered BookMyShow grids; '
             'grid teaser ignored')]
    detail_pages, detail_note = apify_render_pages([url for url, _ in detail_jobs])
    if not detail_pages:
        return [], ['in.bookmyshow.com'], len(pages), [('bookmyshow', detail_note)]
    categories = dict(detail_jobs)
    events, domains = [], {'in.bookmyshow.com'}
    for page in detail_pages:
        if not _bms_detail_url(page.get('url')):
            continue
        events.extend(_events_from_rendered_page(page, categories.get(page['url'])))
        parsed = urllib.parse.urlsplit(page.get('url') or '')
        if parsed.netloc:
            domains.add(parsed.netloc.lower())
    today = dt.date.today().isoformat()
    events = [event for event in dedupe_events(events)
              if not event.get('date') or event['date'] >= today]
    if verbose:
        print(f'    {note}; {detail_note}; {len(events)} parsed detail event(s)')
    if not events:
        return [], sorted(domains), len(pages) + len(detail_pages), [
            ('bookmyshow', 'rendered detail pages contained no parseable Event JSON-LD')]
    return events, sorted(domains), len(pages) + len(detail_pages), []


def _required_source_failed(key, _pages, failures):
    """Any failed route is nonzero, except an optional unconfigured BookMyShow route."""
    return bool(failures) and not (
        key == 'bookmyshow' and all(kind == 'unconfigured' for kind, _ in failures))


def _selftest():
    fixtures = os.path.join(HERE, 'fixtures')
    with open(os.path.join(fixtures, 'district_next.html'), encoding='utf-8') as handle:
        district = district_events_from_next(handle.read(), 'comedy', 'Bengaluru')
    assert len(district) == 2, district
    assert district[0]['category'] == 'comedy'
    assert district[0]['date'] == '2027-01-15'
    assert district[0]['url'] == 'https://www.district.in/events/sana-live'
    assert district[0]['status'] == 'Available'
    assert district[1]['status'] is None

    dispatched = []
    original_fetch_district = fetch_district
    try:
        def record_district(cities, verbose):
            dispatched.append((cities, verbose))
            return [], [], 0, []
        globals()['fetch_district'] = record_district
        assert fetch_source('district', ['Mumbai'], verbose=False) == ([], [], 0, [])
        assert dispatched == [(['Mumbai'], False)]
    finally:
        globals()['fetch_district'] = original_fetch_district

    with open(os.path.join(fixtures, 'bookmyshow_grid_details.json'), encoding='utf-8') as handle:
        payload = json.load(handle)
    cmap = {'/explore/comedy-shows-': 'comedy'}
    bookmyshow, note = bms_events_from_items(payload['items'], cmap, verbose=False)
    assert len(bookmyshow) == 12, (len(bookmyshow), note)
    assert len({event['url'] for event in bookmyshow}) == 12
    assert all(event['category'] == 'comedy' for event in bookmyshow)
    assert _bms_detail_url('https://in.bookmyshow.com/events/fixture/ET1')
    assert _bms_detail_url('https://bookmyshow.com/events/fixture/ET1')
    assert not _bms_detail_url('https://evilbookmyshow.com/events/fixture/ET1')
    assert not _bms_detail_url('ftp://in.bookmyshow.com/events/fixture/ET1')
    assert not _bms_detail_url('//in.bookmyshow.com/events/fixture/ET1')
    orphaned, _ = bms_events_from_items(payload['items'][1:], cmap, verbose=False)
    assert not orphaned, 'detail rows without a rendered grid must not become evidence'
    assert 'rendered detail page(s)' in note and 'grid teaser ignored' in note

    pages, note = apify_render_pages(['https://in.bookmyshow.com/explore/comedy-shows-mumbai'],
                                     token='')
    assert not pages and note.startswith('UNCONFIGURED:'), note
    assert not _required_source_failed('bookmyshow', 0, [('unconfigured', note)])
    assert _required_source_failed('district', 0, [('unconfigured', note)])
    assert _required_source_failed('district', 0, [('district', 'HTTPError')])
    assert _required_source_failed('district', 8, [('one-route', 'HTTPError')])
    assert not _required_source_failed('bookmyshow', 1, [('unconfigured', note)])
    assert _fetch_metadata('district')['source_access'] == 'direct-nextjs-eventdata'
    assert 'Apify-rendered' in _fetch_metadata('bookmyshow')['fetched_by']
    print('ALL CHECKS PASS')
def fetch_source(key, cities=None, verbose=True):
    plan = PLAN[key]
    if key == 'district':
        return fetch_district(cities, verbose)
    if plan.get('via') == 'apify':
        if key == 'bookmyshow':
            return fetch_bookmyshow(cities, verbose)
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


def _fetch_metadata(key):
    """Describe the non-secret acquisition route stored beside raw rows."""
    source = next((item for item in model.load_sources()['sources'] if item['key'] == key), {})
    access = source.get('access', 'unknown')
    route = {
        'district': 'direct HTTP Next.js EventData plus schema.org JSON-LD',
        'bookmyshow': 'Apify-rendered category grids plus depth-one Event JSON-LD details',
    }.get(key, 'direct HTTP schema.org JSON-LD')
    return dict(
        fetched_by=f'tools/fetch_listings.py ({route})',
        fetch_route=route,
        source_access=access,
    )

def write_raw(key, events, domains, date):
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f'{date}__{key}.json')
    payload = dict(events=events, domains_seen=domains)
    payload.update(_fetch_metadata(key))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
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
    ap.add_argument('--selftest', action='store_true', help='run offline parser fixtures')
    a = ap.parse_args(argv)

    if a.selftest:
        _selftest()
        return 0

    if a.apify_dataset:
        print(f'Reading Apify dataset {a.apify_dataset} for "{a.source}"')
        print('=' * 70)
        source = next((item for item in model.load_sources()['sources']
                       if item['key'] == a.source), None)
        if source is None:
            print(f'  [fail] unknown dataset source: {a.source}')
            return 2
        if not source.get('enabled', False):
            print(f'  [fail] dataset source is disabled: {a.source}')
            return 2
        if a.source not in PLAN:
            print(f'  [fail] dataset source has no enabled import route: {a.source}')
            return 2
        cmap = {frag: cat for frag, cat in
                [(p.split('{city}')[0].rstrip('-'), c)
                 for p, c in PLAN.get(a.source, {}).get('paths', []) if c]}
        if a.source == 'bookmyshow':
            events, note = read_bookmyshow_apify_dataset(a.apify_dataset, cmap)
        else:
            events, note = read_apify_dataset(a.apify_dataset, cmap)
        print(f'  {note}')
        if not events:
            print('  [fail] dataset import produced no usable event evidence; no raw file written.')
            return 1
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
    unknown = [key for key in keys if key not in PLAN]
    if unknown:
        print('Unknown or postponed fetch source(s): ' + ', '.join(unknown))
        return 2
    source_config = {source['key']: source for source in model.load_sources()['sources']}
    disabled = [key for key in keys if key in source_config
                and not source_config[key].get('enabled', False)]
    if disabled:
        print('Disabled fetch source(s): ' + ', '.join(disabled))
        return 2
    cities = [c for c in (a.cities or '').split(',') if c.strip()] or None

    print(f'Direct listing fetch — {a.date}')
    print('=' * 70)
    print('Direct routes use JSON-LD or District Next.js EventData; BookMyShow is detail-only via Apify.')
    print('Townscript is postponed; an unconfigured BookMyShow route retains prior raw data.')

    total = 0
    exit_code = 0
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
        if failures:
            for route, reason in failures:
                mark = 'skip' if key == 'bookmyshow' and route == 'unconfigured' else 'fail'
                print(f'     [{mark}] {route}: {reason}')
            if key == 'bookmyshow' and all(route == 'unconfigured'
                                           for route, _ in failures):
                print('     BookMyShow primary validation is unconfigured; prior raw data, if '
                      'any, was retained and no zero-row file was written.')
        if _required_source_failed(key, pages, failures):
            exit_code = 1

    print(f'\n{total} events total.')
    if a.dry_run:
        print('--dry-run: nothing written.')
    else:
        print('Next: run_weekly.py ingests data/raw/ and scores it.')
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())

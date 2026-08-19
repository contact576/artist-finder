"""The observation store — and the reason this tool has to run on a schedule.

WHY A SINGLE RUN IS NEARLY WORTHLESS.
No Indian platform publishes tickets sold. One crawl therefore yields a list of shows and no
idea whether any of them matter. What carries the information is CHANGE BETWEEN CRAWLS: a room
that got bigger, a second night added, a status that flipped to sold out, a name that appeared
on a gatekept platform for the first time. None of that is visible in a snapshot; all of it is
visible in a series. The schedule is not a convenience feature, it is the measurement apparatus.

TWO STORES, DIFFERENT RULES.

  data/snapshots/<date>.json   RAW and IMMUTABLE. Exactly what was seen on that date. Never
                               rewritten, never back-filled. If the parser improves later, old
                               snapshots keep their old parse — otherwise the series stops being
                               a record of what we knew when, and back-testing becomes circular.

  data/shows.json              DERIVED ledger, one row per distinct show, carrying first_seen,
                               last_seen and the full status history. Rebuildable from the
                               snapshots at any time by rebuild_ledger().

The ledger is where "added a second night" and "sold out in nine days" actually become
computable, because both are statements about a show's history rather than its current state.
"""
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

BASE = model.BASE
SNAPDIR = os.path.join(BASE, 'data', 'snapshots')
LEDGER = os.path.join(BASE, 'data', 'shows.json')

SOLD_OUT = ('sold out', 'soldout', 'sold-out')
FILLING = ('filling fast', 'few tickets', 'almost full', 'last few')


def today():
    return dt.date.today().isoformat()


def entities_of(ev):
    """The performers or production on a row.

    Reads `entities`, falling back to the pre-genre `artists` key. Snapshots are IMMUTABLE, so
    a file written by an older build keeps its old shape forever and the reader has to cope —
    that is the cost of immutability and it is the right trade.
    """
    if ev.get('entities') is not None:
        return ev['entities']
    return [dict(e, kind=e.get('kind', 'artist')) for e in (ev.get('artists') or [])]


def show_id(ev):
    """Stable identity for one show across crawls.

    URL first when present — it is the only thing a platform guarantees is stable. Otherwise a
    hash of source+artist+venue+date. Deliberately NOT the title: promoters edit titles
    mid-sale ("Live" becomes "Live - FINAL SHOW"), and a title-keyed id would read that as a
    brand new show and fake an announcement.
    """
    if ev.get('url'):
        return 'u:' + hashlib.sha1(ev['url'].encode('utf-8')).hexdigest()[:16]
    key = '|'.join(str(ev.get(k) or '') for k in ('source', 'venue', 'city', 'date'))
    key += '|' + ','.join(a['slug'] for a in entities_of(ev))
    return 'h:' + hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]


def status_class(status):
    if not status:
        return 'unknown'
    s = status.lower()
    if any(k in s for k in SOLD_OUT):
        return 'sold_out'
    if any(k in s for k in FILLING):
        return 'filling'
    return 'available'


def ingest(raw_events, source_key, run_date=None, extra=None):
    """Normalise one source's crawl into the day's snapshot. Returns a summary dict.

    Called once per source per run. Re-calling for the same (date, source) REPLACES that
    source's slice of the day, so a retry after a partial failure is safe.
    """
    run_date = run_date or today()
    os.makedirs(SNAPDIR, exist_ok=True)
    path = os.path.join(SNAPDIR, f'{run_date}.json')

    snap = (dict(taken_at=run_date, sources={}, platform_candidates=[])
            if not os.path.exists(path)
            else json.load(open(path, encoding='utf-8')))

    events, review = [], []
    for raw in raw_events or []:
        ev = model.normalise_event(raw, source_key, run_date)
        if ev is None:
            continue
        ev['show_id'] = show_id(ev)
        ev['status_class'] = status_class(ev.get('status'))
        (events if ev['trusted'] else review).append(ev)

    snap['sources'][source_key] = dict(
        crawled_at=dt.datetime.now().isoformat(timespec='seconds'),
        n_raw=len(raw_events or []), n_events=len(events), n_review=len(review),
        events=events, review=review,
        **(extra or {}))

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)

    return dict(date=run_date, source=source_key, raw=len(raw_events or []),
                kept=len(events), review=len(review),
                parse_rate=round(len(events) / max(len(raw_events or []), 1), 3))


def note_platform_candidates(domains, run_date=None):
    """Record ticketing domains seen in the wild that sources.json does not know about.

    This is the 'new channels of entertainment' half of the brief. A domain showing up here
    repeatedly is a platform worth adding as a source — or a competitor worth knowing about.
    """
    run_date = run_date or today()
    path = os.path.join(SNAPDIR, f'{run_date}.json')
    snap = (dict(taken_at=run_date, sources={}, platform_candidates=[])
            if not os.path.exists(path)
            else json.load(open(path, encoding='utf-8')))
    known = model.known_domains()
    new = sorted({d.lower().lstrip('www.') for d in domains} - known)
    snap['platform_candidates'] = sorted(set(snap.get('platform_candidates', [])) | set(new))
    os.makedirs(SNAPDIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snap, f, indent=1, ensure_ascii=False)
    return new


def snapshot_dates():
    if not os.path.isdir(SNAPDIR):
        return []
    return sorted(f[:-5] for f in os.listdir(SNAPDIR) if f.endswith('.json'))


def load_snapshot(date):
    with open(os.path.join(SNAPDIR, f'{date}.json'), encoding='utf-8') as f:
        return json.load(f)


def iter_events(snap):
    for src in snap.get('sources', {}).values():
        for ev in src.get('events', []):
            yield ev


def rebuild_ledger():
    """Fold every snapshot, oldest first, into one row per show. Idempotent.

    first_seen is the closest thing available to an ANNOUNCEMENT DATE, and most of the
    interesting metrics (sell-through speed, how far ahead a room was booked) are differences
    against it. It is a lower bound, not the truth: a show announced before this tool started
    crawling reads as first seen on our first crawl. `first_seen_censored` marks those so no
    downstream metric mistakes our start date for the market's.
    """
    dates = snapshot_dates()
    ledger, first_run = {}, (dates[0] if dates else None)

    for d in dates:
        for ev in iter_events(load_snapshot(d)):
            sid = ev['show_id']
            row = ledger.get(sid)
            if row is None:
                ledger[sid] = row = dict(
                    show_id=sid, source=ev['source'], source_tier=ev['source_tier'],
                    title=ev['title'], url=ev['url'],
                    entities=entities_of(ev), role=ev['role'],
                    lineup_size=ev['lineup_size'],
                    genre=ev.get('genre'), genre_confidence=ev.get('genre_confidence'),
                    entity_type=ev.get('entity_type', 'artist'),
                    category=ev.get('category'),
                    venue=ev['venue'], city=ev['city'],
                    venue_band=ev['venue_band'], venue_rank=ev['venue_rank'],
                    venue_capacity_approx=ev['venue_capacity_approx'],
                    show_date=ev['date'],
                    price_min=ev.get('price_min'), price_max=ev.get('price_max'),
                    first_seen=d, last_seen=d,
                    first_seen_censored=(d == first_run),
                    status_history=[], sold_out_on=None)
            row['last_seen'] = d
            # Keep the latest non-null room/date info; platforms fill fields in over time.
            for k in ('venue', 'city', 'venue_band', 'venue_rank', 'price_min', 'price_max'):
                if row.get(k) in (None, '') and ev.get(k) not in (None, ''):
                    row[k] = ev[k]
            sc = ev['status_class']
            if not row['status_history'] or row['status_history'][-1]['status'] != sc:
                row['status_history'].append(dict(date=d, status=sc))
            if sc == 'sold_out' and row['sold_out_on'] is None:
                row['sold_out_on'] = d

    out = dict(rebuilt_at=dt.datetime.now().isoformat(timespec='seconds'),
               n_snapshots=len(dates), first_snapshot=first_run,
               last_snapshot=(dates[-1] if dates else None),
               shows=list(ledger.values()))
    with open(LEDGER, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    return out


def load_ledger():
    if not os.path.exists(LEDGER):
        return rebuild_ledger()
    with open(LEDGER, encoding='utf-8') as f:
        return json.load(f)


def by_entity(ledger=None):
    """slug -> {name, kind, genre, shows:[...]}.

    A lineup show is attributed to every named performer. `genre` is the entity's MOST COMMON
    genre across its shows, not the latest — a comedian who once appeared on a music bill should
    not be reclassified by that one row.
    """
    ledger = ledger or load_ledger()
    out = {}
    for s in ledger['shows']:
        for a in (s.get('entities') or []):
            e = out.setdefault(a['slug'], dict(slug=a['slug'], name=a['name'],
                                               kind=a.get('kind', 'artist'), shows=[]))
            e['shows'].append(s)
    for e in out.values():
        counts = {}
        for s in e['shows']:
            g = s.get('genre')
            if g and g != 'unknown':
                counts[g] = counts.get(g, 0) + 1
        e['genre'] = max(counts, key=counts.get) if counts else 'unknown'
    return out


# Old name kept so nothing silently breaks; the ledger holds productions too now.
by_artist = by_entity


def _selftest():
    """Runs against a temporary store so it never touches real snapshots."""
    global SNAPDIR, LEDGER
    import tempfile
    tmp = tempfile.mkdtemp()
    SNAPDIR, LEDGER = os.path.join(tmp, 'snap'), os.path.join(tmp, 'shows.json')

    wk1 = [dict(title='Neel Sharma: Rough Draft', venue='The Habitat', city='Mumbai',
                url='https://x/1', date='2026-10-04', status='Available'),
           dict(title='Neel Sharma Live', venue='That Comedy Club', city='Bengaluru',
                url='https://x/2', date='2026-10-11', status='Available')]
    wk2 = [dict(title='Neel Sharma: Rough Draft', venue='The Habitat', city='Mumbai',
                url='https://x/1', date='2026-10-04', status='Sold Out'),
           dict(title='Neel Sharma Live', venue='That Comedy Club', city='Bengaluru',
                url='https://x/2', date='2026-10-11', status='Filling Fast'),
           dict(title='Neel Sharma: Rough Draft', venue='Sophia Bhabha Hall', city='Mumbai',
                url='https://x/3', date='2026-11-15', status='Available')]

    print(' ', ingest(wk1, 'townscript', '2026-08-01'))
    print(' ', ingest(wk2, 'townscript', '2026-08-08'))
    led = rebuild_ledger()
    print(f'\n  ledger: {len(led["shows"])} shows across {led["n_snapshots"]} snapshots')
    ok = len(led['shows']) == 3
    for s in led['shows']:
        print(f'    {s["venue"]:22} band={s["venue_band"]:11} first_seen={s["first_seen"]} '
              f'sold_out_on={s["sold_out_on"]} hist={[h["status"] for h in s["status_history"]]}')
    a = by_artist(led)
    ok = ok and 'neel-sharma' in a and len(a['neel-sharma']['shows']) == 3
    print(f'\n  by_artist: {[(k, len(v["shows"])) for k, v in a.items()]}')
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

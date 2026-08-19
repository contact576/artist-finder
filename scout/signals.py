"""What the series says about one artist.

THE CENTRAL DISTINCTION: LEVEL vs TRAJECTORY.

  LEVEL      Where an artist is right now — biggest room, how many cities, is anybody
             gatekeeping them. Computable from a SINGLE crawl. Useful on day one.

  TRAJECTORY Where they are going — room escalation, added nights, sell-through speed,
             platform graduation, announcement cadence. Needs a SERIES. Meaningless on day one
             and actively misleading if reported as though it were measured.

Every trajectory signal therefore reports `observable: False` until there is enough history,
and score.py refuses to fold an unobservable signal in as a zero. Scoring an unmeasured thing
as zero is the single most likely way this tool would quietly rank the wrong people: an artist
we have watched for six weeks would beat an identical artist we found last Tuesday, purely
because the newcomer's escalation reads as "no escalation" instead of "not yet known".

  MIN_SPAN_DAYS / MIN_SNAPSHOTS  the floor for trajectory signals to mean anything.

WHAT WE DELIBERATELY DO NOT CLAIM.
Nothing here is tickets sold. No Indian platform publishes it. These are proxies for what
somebody with money believes, which is a different and weaker thing, and the difference is
carried all the way to the report.
"""
import datetime as dt
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import genres     # noqa: E402
import model      # noqa: E402
import snapshot   # noqa: E402

MIN_SPAN_DAYS = 21      # under three weeks apart, "escalation" is mostly crawl noise
MIN_SNAPSHOTS = 3
RECENT_DAYS = 45        # the window that counts as "now"
NEW_ARTIST_DAYS = 60    # first seen this recently = a genuinely new name to us


def _d(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _days(a, b):
    da, db = _d(a), _d(b)
    return (db - da).days if da and db else None


def observability(ledger=None):
    """How much series do we actually have? Everything trajectory-shaped depends on this."""
    ledger = ledger or snapshot.load_ledger()
    dates = snapshot.snapshot_dates()
    span = _days(dates[0], dates[-1]) if len(dates) >= 2 else 0
    ok = len(dates) >= MIN_SNAPSHOTS and (span or 0) >= MIN_SPAN_DAYS
    return dict(n_snapshots=len(dates), span_days=span or 0,
                first=dates[0] if dates else None, last=dates[-1] if dates else None,
                trajectory_observable=ok,
                why=None if ok else
                    f'need >={MIN_SNAPSHOTS} snapshots spanning >={MIN_SPAN_DAYS} days; '
                    f'have {len(dates)} spanning {span or 0}')


# ------------------------------------------------------------------ level signals

def _robust_room(ranked):
    """Highest band the artist plays REPEATEDLY, plus the outright peak.

    Using the peak alone is fragile in exactly the way that matters: one support slot on an
    arena bill would make a club act read as an arena act, and one mis-parsed venue string
    would do the same. So the headline figure is the highest band with at least two shows, and
    the peak is kept beside it, flagged when it rests on a single booking.
    """
    if not ranked:
        return None, None, False
    counts = {}
    for s in ranked:
        counts[s['venue_rank']] = counts.get(s['venue_rank'], 0) + 1
    peak = max(counts)
    repeated = [r for r, n in counts.items() if n >= 2]
    # With almost no data there is nothing to corroborate against, so the peak stands.
    best = max(repeated) if repeated else (peak if len(ranked) <= 2 else
                                           sorted(counts)[max(len(counts) - 2, 0)])
    return best, peak, counts[peak] == 1


def level(shows, asof=None, genre=None):
    """Where the entity stands now. Single-crawl computable.

    `genre` is carried through rather than used here: what counts as a big room differs by
    genre, but that judgement belongs in score.py where the stature curve lives. This function
    stays a pure description of what was observed.
    """
    asof = asof or snapshot.today()
    ranked = [s for s in shows if s.get('venue_rank')]
    # Stature should reflect rooms they carry themselves. A name on a five-hander at a big
    # venue is evidence about the promoter, not the performer.
    head_ranked = [s for s in ranked if s.get('role') == 'headline'] or ranked
    best_rank, peak_rank, peak_single = _robust_room(head_ranked)
    cities = {(s.get('city') or '').strip().lower() for s in shows if s.get('city')}
    tiers = [s.get('source_tier') for s in shows if s.get('source_tier')]
    trank = {'longtail': 1, 'curated': 2, 'major': 3}
    head = [s for s in shows if s.get('role') == 'headline']

    upcoming = [s for s in shows if (_days(asof, s.get('show_date')) or -1) >= 0]
    lead = [_days(asof, s.get('show_date')) for s in upcoming]
    lead = [x for x in lead if x is not None]

    return dict(
        n_shows=len(shows),
        n_upcoming=len(upcoming),
        best_room_rank=best_rank,
        best_room_band=next((s['venue_band'] for s in head_ranked
                             if s['venue_rank'] == best_rank), None),
        peak_room_rank=peak_rank,
        peak_room_is_single_booking=peak_single,
        rooms_known=len(ranked), rooms_unknown=len(shows) - len(ranked),
        n_cities=len(cities), cities=sorted(cities),
        best_platform_tier=max(tiers, key=lambda t: trank[t]) if tiers else None,
        headline_share=round(len(head) / len(shows), 3) if shows else None,
        max_lead_days=max(lead) if lead else None,
        price_max=max((s['price_max'] for s in shows if s.get('price_max')), default=None),
        genre=genre,
        entity_kind=next((s.get('entity_type') for s in shows if s.get('entity_type')), 'artist'),
        genres_seen=sorted({s.get('genre') for s in shows if s.get('genre')}),
    )


# ------------------------------------------------------------------ trajectory signals

def _split_era(shows, cut):
    early = [s for s in shows if _d(s['first_seen']) and _d(s['first_seen']) < cut]
    late = [s for s in shows if _d(s['first_seen']) and _d(s['first_seen']) >= cut]
    return early, late


def trajectory(shows, obs, asof=None):
    """Where the artist is going. Returns a dict of signals, each with `observable`.

    Every entry is dict(value=..., observable=bool, note=str). A consumer that reads .value
    without checking .observable is a bug waiting to happen — see the module header.
    """
    asof = asof or snapshot.today()
    cut = _d(asof) - dt.timedelta(days=RECENT_DAYS)
    can = obs['trajectory_observable']
    na = lambda note='insufficient history': dict(value=None, observable=False, note=note)  # noqa: E731

    early, late = _split_era(shows, cut)

    # --- room escalation: biggest room newly announced vs biggest previously announced
    if not can or not early or not late:
        room = na() if not can else na('no shows in one of the two eras')
    else:
        e = max((s['venue_rank'] for s in early if s.get('venue_rank')), default=None)
        l = max((s['venue_rank'] for s in late if s.get('venue_rank')), default=None)
        room = (na('no classified rooms in one era') if e is None or l is None
                else dict(value=l - e, observable=True,
                          note=f'best room rank {e} -> {l}'))

    # --- added nights: >1 show at the same venue+city, announced on different dates.
    # A second night is the loudest legal signal available: it means the first one sold.
    key = {}
    for s in shows:
        k = ((s.get('venue') or '').lower(), (s.get('city') or '').lower())
        key.setdefault(k, []).append(s)
    added = 0
    for k, group in key.items():
        if not k[0]:
            continue
        seen = {g['first_seen'] for g in group}
        if len(group) > 1 and len(seen) > 1:
            added += len(group) - 1
    adds = (dict(value=added, observable=True, note=f'{added} later-announced repeat night(s)')
            if len(snapshot.snapshot_dates()) >= 2 else na('need >=2 snapshots'))

    # --- sell-through
    so = [s for s in shows if s.get('sold_out_on')]
    if len(snapshot.snapshot_dates()) < 2:
        sell = na('need >=2 snapshots')
        speed = na('need >=2 snapshots')
    else:
        sell = dict(value=round(len(so) / len(shows), 3) if shows else None, observable=True,
                    note=f'{len(so)} of {len(shows)} observed sold out')
        spans = [_days(s['first_seen'], s['sold_out_on']) for s in so
                 if not s.get('first_seen_censored')]
        spans = [x for x in spans if x is not None]
        speed = (dict(value=statistics.median(spans), observable=True,
                      note=f'median {statistics.median(spans)}d from first seen to sold out '
                           f'(n={len(spans)})')
                 if spans else na('no uncensored sellouts yet'))

    # --- platform graduation: appearing on a more gatekept platform than before
    trank = {'longtail': 1, 'curated': 2, 'major': 3}
    if not can or not early or not late:
        grad = na()
    else:
        e = max((trank[s['source_tier']] for s in early if s.get('source_tier')), default=None)
        l = max((trank[s['source_tier']] for s in late if s.get('source_tier')), default=None)
        grad = (na('platform tier unknown in one era') if e is None or l is None
                else dict(value=l - e, observable=True, note=f'platform tier {e} -> {l}'))

    # --- announcement cadence: new shows in the recent window vs the window before it
    if not can:
        cad = na()
    else:
        prev_cut = _d(asof) - dt.timedelta(days=2 * RECENT_DAYS)
        prev = [s for s in shows if _d(s['first_seen'])
                and prev_cut <= _d(s['first_seen']) < cut]
        cad = dict(value=len(late) - len(prev), observable=True,
                   note=f'{len(prev)} announced in prior {RECENT_DAYS}d -> {len(late)} in last '
                        f'{RECENT_DAYS}d')

    # --- newness (a level fact, but only meaningful against our own crawl history)
    firsts = [s['first_seen'] for s in shows]
    censored = any(s.get('first_seen_censored') for s in shows)
    fs = min(firsts) if firsts else None
    isnew = (dict(value=False, observable=False,
                  note='first seen on our first ever crawl — cannot tell new from pre-existing')
             if censored else
             dict(value=(_days(fs, asof) or 999) <= NEW_ARTIST_DAYS, observable=True,
                  note=f'first seen {fs} ({_days(fs, asof)}d ago)'))

    return dict(room_escalation=room, added_nights=adds, sellout_rate=sell,
                sellout_speed_days=speed, platform_graduation=grad,
                announce_cadence=cad, is_new=isnew)


def for_artist(slug, ledger=None, asof=None):
    ledger = ledger or snapshot.load_ledger()
    arts = snapshot.by_entity(ledger)
    if slug not in arts:
        return None
    a = arts[slug]
    obs = observability(ledger)
    return dict(slug=slug, name=a['name'], kind=a.get('kind', 'artist'),
                genre=a.get('genre', 'unknown'), n_shows=len(a['shows']),
                observability=obs,
                level=level(a['shows'], asof, a.get('genre')),
                trajectory=trajectory(a['shows'], obs, asof))


def for_all(ledger=None, asof=None):
    ledger = ledger or snapshot.load_ledger()
    obs = observability(ledger)
    out = []
    for slug, a in snapshot.by_entity(ledger).items():
        out.append(dict(slug=slug, name=a['name'], kind=a.get('kind', 'artist'),
                        genre=a.get('genre', 'unknown'), n_shows=len(a['shows']),
                        observability=obs,
                        level=level(a['shows'], asof, a.get('genre')),
                        trajectory=trajectory(a['shows'], obs, asof)))
    return out


def _selftest():
    import json
    import tempfile
    tmp = tempfile.mkdtemp()
    snapshot.SNAPDIR = os.path.join(tmp, 'snap')
    snapshot.LEDGER = os.path.join(tmp, 'shows.json')

    # Four weekly crawls. One artist climbs club -> theatre and adds a second night on a
    # gatekept platform; another stays put on open mics.
    weeks = {
        '2026-06-06': [
            ('townscript', 'Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
             'https://t/1', '2026-08-02', 'Available'),
            ('townscript', 'Ira Bose Live', 'Blue Frog Basement', 'Pune',
             'https://t/9', '2026-08-05', 'Available'),
        ],
        '2026-06-20': [
            ('townscript', 'Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
             'https://t/1', '2026-08-02', 'Sold Out'),
            ('townscript', 'Ira Bose Live', 'Blue Frog Basement', 'Pune',
             'https://t/9', '2026-08-05', 'Available'),
        ],
        '2026-07-11': [
            ('townscript', 'Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
             'https://t/1', '2026-08-02', 'Sold Out'),
            ('bookmyshow', 'Neel Sharma: Rough Draft', 'Sophia Bhabha Hall', 'Mumbai',
             'https://b/2', '2026-09-20', 'Available'),
            ('townscript', 'Ira Bose Live', 'Blue Frog Basement', 'Pune',
             'https://t/9', '2026-08-05', 'Available'),
        ],
        '2026-08-01': [
            ('bookmyshow', 'Neel Sharma: Rough Draft', 'Sophia Bhabha Hall', 'Mumbai',
             'https://b/2', '2026-09-20', 'Sold Out'),
            ('bookmyshow', 'Neel Sharma: Rough Draft', 'Sophia Bhabha Hall', 'Mumbai',
             'https://b/3', '2026-09-21', 'Available'),
            ('bookmyshow', 'Neel Sharma: Rough Draft', 'Good Shepherd Auditorium', 'Bengaluru',
             'https://b/4', '2026-10-03', 'Available'),
            ('townscript', 'Ira Bose Live', 'Blue Frog Basement', 'Pune',
             'https://t/9', '2026-08-05', 'Available'),
        ],
    }
    for d, rows in weeks.items():
        for src in {r[0] for r in rows}:
            snapshot.ingest(
                [dict(title=t, venue=v, city=c, url=u, date=dd, status=st)
                 for (s, t, v, c, u, dd, st) in rows if s == src], src, d)
    snapshot.rebuild_ledger()

    obs = observability()
    print(f'  observability: {obs}\n')
    ok = obs['trajectory_observable']

    for slug in ('neel-sharma', 'ira-bose'):
        r = for_artist(slug, asof='2026-08-01')
        print(f'  {r["name"]}  ({r["n_shows"]} shows)')
        L = r['level']
        print(f'    LEVEL      room={L["best_room_band"]} cities={L["n_cities"]} '
              f'platform={L["best_platform_tier"]} headline_share={L["headline_share"]}')
        for k, v in r['trajectory'].items():
            mark = ' ' if v['observable'] else '~'
            print(f'    {mark}{k:22} {str(v["value"]):>6}   {v["note"]}')
        print()

    n = for_artist('neel-sharma', asof='2026-08-01')['trajectory']
    checks = [
        # club(2) -> mid theatre(4) is a two-band jump, not one. Bands are the unit.
        ('room escalation +2 bands', n['room_escalation']['value'] == 2),
        ('added night detected', n['added_nights']['value'] == 1),
        ('platform graduation detected', n['platform_graduation']['value'] == 2),
        ('sellout speed measured', n['sellout_speed_days']['observable']),
    ]
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and good
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

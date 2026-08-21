"""Ranking — and the three claims this module refuses to make.

1. IT DOES NOT CLAIM TO BE CALIBRATED.
   The weights below are judgement, not measurement. Nothing has been back-tested because the
   ground truth does not exist yet: "who actually broke out" is only knowable in hindsight, and
   this tool has to run for a year to earn its own training set. Every score therefore carries
   `calibrated: False`, and backtest.py exists, empty, to be filled when the history matures.
   Compare the Artist Tour Engine, whose bands ARE measured and whose docs say so — the
   difference between the two projects is the point, and collapsing it would be dishonest.

2. IT DOES NOT SCORE AN UNMEASURED SIGNAL AS ZERO.
   Weights are renormalised over the signals actually observable for that artist. Without this,
   an artist first seen last Tuesday is punished for a short history rather than flagged as
   unknown, and the ranking becomes a ranking of how long we have been watching.

3. IT DOES NOT LET INDIAN TRACTION IMPLY DIASPORA DEMAND.
   The Artist Tour Engine measured this on 18 artists: tickets per million followers spans 15x,
   and the follower->draw fit is sublinear (exponent 0.369). Reach in India and ticket sales to
   the North American diaspora come apart badly. So breakout is scored here, export readiness is
   a separate CHECKLIST, and the actual North American number is never produced by this file —
   it comes from the calibrated engine via bridge.py, off search volume.

THE OUTPUT THAT MATTERS IS `action`, NOT `momentum`.
A score ranks a list; an action tells somebody what to do on Monday. The quadrant below turns
two numbers into one of four instructions.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demand   # noqa: E402
import genres   # noqa: E402
import signals  # noqa: E402

# --- momentum weights. JUDGEMENT, NOT MEASUREMENT. See claim 1 above.
# Ordered by how much money someone had to risk to produce the signal: booking a bigger room
# and adding a night are decisions with a downside, a listing appearing is not.
W = dict(
    room_escalation=25,
    added_nights=20,
    platform_graduation=15,
    sellout_rate=15,
    sellout_speed_days=10,
    announce_cadence=10,
)

# --- how each raw signal maps to 0..1 before weighting.
def _unit(name, v):
    if v is None:
        return None
    if name == 'room_escalation':
        return max(0.0, min(1.0, v / 2.0))          # +2 bands = full marks
    if name == 'added_nights':
        return max(0.0, min(1.0, v / 3.0))          # 3 added nights = full marks
    if name == 'platform_graduation':
        return max(0.0, min(1.0, v / 2.0))          # longtail -> major = full marks
    if name == 'sellout_rate':
        return max(0.0, min(1.0, v))
    if name == 'sellout_speed_days':
        # Faster is better. <=7d full marks, >=90d nothing.
        return max(0.0, min(1.0, (90 - v) / 83.0))
    if name == 'announce_cadence':
        return max(0.0, min(1.0, (v + 2) / 6.0))    # -2 or worse = 0, +4 = full marks
    return None


# Room band -> stature points now lives in genres.ROOM_SCALE, one curve per genre. The comedy
# curve is deliberately flat through the middle: a mid-theatre act (800-1500) is still an
# ordinary signing target, not a star, and an earlier linear scale put them at 60/100 and
# classified a textbook breakout as ESTABLISHED. Other genres climb differently — a mainstream
# music act starts in rooms a comedian would consider a career peak.
STATURE_TIER = {None: 0, 'longtail': 0, 'curated': 10, 'major': 20}


def stature(level, genre=None):
    """How big are they RIGHT NOW, measured against their own genre's ladder.

    Deliberately crude: room band does most of the work, city count and platform adjust it.
    Its job is to separate 'plays 150-seat rooms' from 'plays 1,000-seat rooms', which is all
    that is needed to tell a signing target from an artist already priced out of reach.

    The genre matters because the ladders differ. 800 seats is a milestone for a comedian and
    an off-night for a playback singer; scoring both on one curve would file half the market in
    the wrong quadrant.
    """
    scale = genres.room_scale(genre or level.get('genre'))
    base = scale.get(level.get('best_room_rank'), 0)
    base += min(level.get('n_cities') or 0, 6) * 3
    base += STATURE_TIER.get(level.get('best_platform_tier'), 0)
    if level.get('rooms_known') == 0:
        return dict(value=None, observable=False,
                    note='no room in any listing could be classified')
    return dict(value=min(round(base), 100), observable=True,
                note=f'room={level.get("best_room_band")} cities={level.get("n_cities")} '
                     f'platform={level.get("best_platform_tier")}')


def momentum(traj):
    """Rate of rise, 0..100, renormalised over observable signals only."""
    parts, used, total_w = [], 0.0, 0.0
    for name, w in W.items():
        sig = traj.get(name) or {}
        if not sig.get('observable'):
            parts.append(dict(signal=name, weight=w, unit=None, contribution=None,
                              observable=False, note=sig.get('note')))
            continue
        u = _unit(name, sig.get('value'))
        if u is None:
            parts.append(dict(signal=name, weight=w, unit=None, contribution=None,
                              observable=False, note='value missing'))
            continue
        parts.append(dict(signal=name, weight=w, unit=round(u, 3),
                          contribution=round(u * w, 2), observable=True,
                          note=sig.get('note')))
        used += u * w
        total_w += w

    if total_w == 0:
        return dict(value=None, observable=False, coverage=0.0, parts=parts,
                    note='no momentum signal is observable yet')
    coverage = total_w / sum(W.values())
    return dict(value=round(100 * used / total_w), observable=True,
                coverage=round(coverage, 2), parts=parts,
                note=f'{int(coverage * 100)}% of signal weight observable')


# --- the quadrant. Two numbers become one instruction.
# Above the ceiling an artist is treated as already established — big enough that they are
# probably repped, priced, and not a discovery. Set at 75 so that climbing INTO a mid theatre
# still reads as RISING; a lower ceiling made successful escalation self-disqualifying, which
# is the exact opposite of what this tool is for.
STATURE_CEILING = 75
MOMENTUM_FLOOR = 45      # below this nothing is moving

def quadrant(st, mo):
    if not st['observable']:
        return 'UNCLASSIFIED', 'no room data — improve the venue map or the adapter'
    if not mo['observable']:
        return ('EARLY' if st['value'] <= STATURE_CEILING else 'ESTABLISHED'), \
               'momentum not yet observable — needs more crawl history'
    hi_s, hi_m = st['value'] > STATURE_CEILING, mo['value'] >= MOMENTUM_FLOOR
    if not hi_s and hi_m:
        return 'RISING', 'small rooms, moving fast — this is the signing target'
    if hi_s and hi_m:
        return 'ESTABLISHED', 'already big and still rising — likely already repped and priced'
    if hi_s and not hi_m:
        return 'STALLED', 'big rooms but flat — check whether the plateau is real'
    return 'EARLY', 'small and quiet — keep watching, no action'


def export_readiness(sig, dossier=None, dem=None):
    """Can we even ATTEMPT a North American number for this entity?

    A checklist, never a score. The calibrated path in the Artist Tour Engine runs off US/CA
    search volume (74 tickets per 1k US, 134 per 1k Canada); absent that, any NA figure is an
    extrapolation from Indian activity, which is exactly the inference the engine's own social
    measurement showed to be weak. So this reports what is MISSING, and the missing item IS the
    research task for the week.

    It now reads real fetched volumes from demand.py as well as the dossier — whichever has the
    number. The dossier still wins, because a hand-researched figure beats a scraped one.
    """
    d = dossier or {}
    search = (d.get('search') or {})
    fetched = demand.for_engine(sig['slug'], dem)
    us = search.get('us_monthly') or fetched.get('us_monthly')
    ca = search.get('ca_monthly') or fetched.get('ca_monthly')
    have_search = bool(us or ca)
    have_actuals = bool(d.get('actuals'))
    have_social = bool((d.get('social') or {}).get('instagram_followers'))

    share = fetched.get('comedian_share')
    contaminated = share is not None and share < demand.CONTAMINATION_FLOOR

    missing = []
    if not have_search:
        missing.append('US/CA Google search volume (Keyword Planner) — the calibrated input')
    if contaminated:
        missing.append(f'a clean alias — only {share:.0%} of that volume is qualified, so the '
                       f'bare name belongs to somebody else')
    if not have_actuals:
        missing.append('any prior North American result — the single biggest evidence upgrade')
    if not have_social:
        missing.append('verified Instagram handle + followers (last-resort basis only)')

    if have_actuals:
        path = 'E1/E2 possible — run the engine'
    elif have_search and not contaminated:
        path = 'E3 possible — run the engine off search volume'
    elif have_search and contaminated:
        path = 'BLOCKED — volume is contaminated; resolve the alias first'
    else:
        path = 'NOT READY — no calibrated input exists yet'

    return dict(ready=(have_search and not contaminated) or have_actuals, path=path,
                missing=missing, has_search=have_search, has_actuals=have_actuals,
                has_social=have_social, us_monthly=us, ca_monthly=ca,
                contaminated=contaminated)


ACTIONS = {
    'RISING':       'SCREEN — research US/CA search volume, then run the Tour Engine',
    'ESTABLISHED':  'MONITOR — verify whether they are already signed elsewhere',
    'STALLED':      'MONITOR — no action unless the plateau breaks',
    'EARLY':        'WATCH — no action; the next crawls decide',
    'UNCLASSIFIED': 'FIX DATA — venue could not be classified',
}


def score_artist(sig, dossier=None, dem=None):
    """One entity, fully scored on BOTH axes.

    `momentum` is India-side rate of rise. `export_signal` is diaspora-side demand. They are
    computed by different modules, from different data, and are never combined into a single
    number — see the module header and demand.py's. What IS combined is the ACTION ordering:
    a RISING artist with a strong export signal is the one to call first.
    """
    genre = sig.get('genre', 'unknown')
    st = stature(sig['level'], genre)
    mo = momentum(sig['trajectory'])
    q, why = quadrant(st, mo)
    ex = export_readiness(sig, dossier, dem)
    xs = demand.export_signal(sig['slug'], dem)
    weight = genres.export_weight(genre)
    candidate_eligible = sig.get('kind', 'artist') in ('artist', 'dj_night')

    action = ACTIONS[q]
    if q == 'RISING':
        if not ex['ready']:
            action = 'RESEARCH — ' + ex['missing'][0]
        elif (xs['value'] or 0) >= 60:
            action = 'CALL — strong diaspora demand; run the Tour Engine and open a conversation'
        else:
            action = 'SCREEN — run the Tour Engine; diaspora demand looks thin so far'
    if not candidate_eligible:
        action = 'TRACK FORMAT - non-artist entity; excluded from the signing radar'

    return dict(
        slug=sig['slug'], name=sig['name'], n_shows=sig['n_shows'],
        kind=sig.get('kind', 'artist'), genre=genre, export_weight=weight,
        stature=st, momentum=mo, export_signal=xs,
        candidate_eligible=candidate_eligible,
        quadrant=q, quadrant_why=why,
        export=ex, action=action,
        calibrated=False,
        basis='heuristic weights, not back-tested — see score.py header',
        observability=sig['observability'],
    )


def rank(sigs, dossiers=None, dem=None):
    """RISING first, then by how worth-calling they are.

    Within a quadrant the order is momentum weighted by how well the genre EXPORTS, then the
    demand signal, then evidence coverage. Genre weight is applied to the ordering only — it
    never changes a score, so nothing is hidden: a fast-rising Marathi play still appears with
    its real momentum, it just does not outrank a comedian anyone could actually book.

    Entities whose genre is still `unknown` have no export weight and are held OUT of the main
    ranking entirely, the same treatment an unclassifiable venue gets. They are reported
    separately so they get fixed rather than silently mis-ranked.
    """
    dossiers = dossiers or {}
    dem = dem if dem is not None else demand.load()
    out = [score_artist(s, dossiers.get(s['slug']), dem) for s in sigs]
    order = {'RISING': 0, 'EARLY': 1, 'ESTABLISHED': 2, 'STALLED': 3, 'UNCLASSIFIED': 4}

    def key(r):
        w = r['export_weight']
        w = 0.0 if w is None else w
        return (order[r['quadrant']],
                -((r['momentum']['value'] or 0) * w),
                -(r['export_signal']['value'] or 0),
                -(r['momentum']['coverage'] or 0),
                -(r['stature']['value'] or 0))

    ranked = [r for r in out if r['candidate_eligible'] and r['export_weight'] is not None]
    held = [r for r in out if r['candidate_eligible'] and r['export_weight'] is None]
    formats = [r for r in out if not r['candidate_eligible']]
    ranked.sort(key=key)
    for r in held:
        r['action'] = 'CLASSIFY — genre unknown, held out of the ranking'
    formats.sort(key=lambda r: (-(r['stature']['value'] or 0), r['name']))
    return ranked + held + formats


def _selftest():
    import snapshot
    import tempfile
    tmp = tempfile.mkdtemp()
    snapshot.SNAPDIR = os.path.join(tmp, 'snap')
    snapshot.LEDGER = os.path.join(tmp, 'shows.json')
    demand.PATH = os.path.join(tmp, 'demand.json')

    def ev(t, v, c, u, d, st, cat):
        return dict(title=t, venue=v, city=c, url=u, date=d, status=st, category=cat)

    W1, W2, W3, W4 = '2026-06-06', '2026-06-20', '2026-07-11', '2026-08-01'
    weeks = {
        W1: [('townscript', ev('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                               'https://t/1', '2026-08-02', 'Available', 'Comedy')),
             ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                               'https://t/9', '2026-08-05', 'Available', 'Comedy')),
             ('bookmyshow', ev('Zara Qureshi Live', 'Shanmukhananda Hall', 'Mumbai',
                               'https://b/50', '2026-09-01', 'Available', 'Comedy')),
             ('bookmyshow', ev('Mughal-e-Azam: The Musical', 'Ranga Shankara', 'Bengaluru',
                               'https://b/70', '2026-09-10', 'Available', 'Theatre'))],
        W2: [('townscript', ev('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                               'https://t/1', '2026-08-02', 'Sold Out', 'Comedy')),
             ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                               'https://t/9', '2026-08-05', 'Available', 'Comedy')),
             ('bookmyshow', ev('Zara Qureshi Live', 'Shanmukhananda Hall', 'Mumbai',
                               'https://b/50', '2026-09-01', 'Available', 'Comedy'))],
        W3: [('townscript', ev('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                               'https://t/1', '2026-08-02', 'Sold Out', 'Comedy')),
             ('bookmyshow', ev('Neel Sharma: Rough Draft', 'Sophia Bhabha Hall', 'Mumbai',
                               'https://b/2', '2026-09-20', 'Available', 'Comedy')),
             ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                               'https://t/9', '2026-08-05', 'Available', 'Comedy')),
             ('bookmyshow', ev('Zara Qureshi Live', 'Shanmukhananda Hall', 'Mumbai',
                               'https://b/50', '2026-09-01', 'Available', 'Comedy'))],
        W4: [('bookmyshow', ev('Neel Sharma: Rough Draft', 'Sophia Bhabha Hall', 'Mumbai',
                               'https://b/2', '2026-09-20', 'Sold Out', 'Comedy')),
             ('bookmyshow', ev('Neel Sharma: Rough Draft', 'Sophia Bhabha Hall', 'Mumbai',
                               'https://b/3', '2026-09-21', 'Available', 'Comedy')),
             ('bookmyshow', ev('Neel Sharma: Rough Draft', 'Good Shepherd Auditorium',
                               'Bengaluru', 'https://b/4', '2026-10-03', 'Available', 'Comedy')),
             ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                               'https://t/9', '2026-08-05', 'Available', 'Comedy')),
             ('bookmyshow', ev('Zara Qureshi Live', 'Shanmukhananda Hall', 'Mumbai',
                               'https://b/50', '2026-09-01', 'Available', 'Comedy')),
             ('bookmyshow', ev('Mughal-e-Azam: The Musical', 'Sophia Bhabha Hall', 'Mumbai',
                               'https://b/71', '2026-10-05', 'Available', 'Theatre')),
             ('bookmyshow', ev('Mughal-e-Azam: The Musical', 'Sophia Bhabha Hall', 'Mumbai',
                               'https://b/72', '2026-10-06', 'Available', 'Theatre'))],
    }
    for d, rows in weeks.items():
        for src in sorted({r[0] for r in rows}):
            payload = []
            for sname, event in rows:
                if sname != src:
                    continue
                performer = ('Neel Sharma' if event['title'].startswith('Neel Sharma') else
                             'Zara Qureshi' if event['title'].startswith('Zara Qureshi') else None)
                payload.append(dict(event, platform_performers=[performer] if performer else []))
            snapshot.ingest(payload, src, d)
    snapshot.rebuild_ledger()

    # Only Neel has had demand fetched. Everyone else is deliberately unknown on that axis.
    def months(sy, sm, vals):
        out, y, m = [], sy, sm
        for v in vals:
            out.append(dict(year=y, month=m, searches=v))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out
    demand.record_search('neel-sharma', 'us', 14000,
                         months(2025, 8, [3000, 3200, 3800, 4200, 5000, 6000,
                                          7000, 8500, 10000, 11500, 13000, 15000]),
                         name='Neel Sharma', source='keyword planner')
    demand.record_international('neel-sharma', [
        dict(country='United Kingdom', city='London', date='2026-12-01', source='serp')])

    sigs = signals.for_all(asof=W4)
    ranked = rank(sigs)

    hdr = ('entity', 'genre', 'quad', 'stat', 'mom', 'exp')
    print('  %-22s %-16s %-13s %4s %4s %4s  action' % hdr)
    print('  %-22s %-16s %-13s %4s %4s %4s  %s'
          % ('-' * 22, '-' * 16, '-' * 13, '----', '----', '----', '-' * 34))
    for r in ranked:
        print('  %-22s %-16s %-13s %4s %4s %4s  %s'
              % (r['name'][:22], r['genre'], r['quadrant'],
                 r['stature']['value'], r['momentum']['value'],
                 r['export_signal']['value'], r['action'][:46]))

    by = {r['slug']: r for r in ranked}
    neel = by['neel-sharma']
    play = next((r for r in ranked if r['kind'] == 'production'), None)

    print()
    print('  Neel - momentum parts (India supply side)')
    for pt in neel['momentum']['parts']:
        if pt['observable']:
            print('    %-22s contrib=%s' % (pt['signal'], pt['contribution']))
    print('  Neel - export parts (diaspora demand side)')
    for pt in neel['export_signal']['parts']:
        if pt['observable']:
            print('    %-22s contrib=%-6s %s' % (pt['signal'], pt['contribution'],
                                                 pt['note'][:46]))

    mom_names = set(pt['signal'] for pt in neel['momentum']['parts'])
    exp_names = set(pt['signal'] for pt in neel['export_signal']['parts'])

    checks = [
        ('Neel ranks first', ranked[0]['slug'] == 'neel-sharma'),
        ('Neel is RISING', neel['quadrant'] == 'RISING'),
        ('strong demand upgrades the action to CALL', neel['action'].startswith('CALL')),
        ('theatre parsed as a production', play is not None),
        ('production kept its own name and genre',
         play is not None and 'Mughal' in play['name'] and play['genre'] == 'theatre'),
        ('production never became a fake artist',
         all(r['kind'] == 'production' or 'Mughal' not in r['name'] for r in ranked)),
        ('theatre ranks below comedy despite its own momentum',
         play is None or ranked.index(play) > ranked.index(neel)),
        # --- THE CARDINAL-RULE GUARD: the two axes share no signal in either direction.
        ('momentum and export share no signals', not (mom_names & exp_names)),
        ('demand signals absent from momentum weights',
         not (set(demand.W) & set(W))),
        ('momentum signals absent from demand weights',
         not (set(W) & set(demand.W))),
        ('nothing claims calibration', all(r['calibrated'] is False for r in ranked)),
        ('unfetched entity has no export signal',
         by['ira-bose']['export_signal']['value'] is None),
    ]
    ok = True
    print()
    for label, good in checks:
        print('  [%s] %s' % ('ok ' if good else 'FAIL', label))
        ok = ok and bool(good)
    print()
    print('  %s' % ('ALL CHECKS PASS' if ok else 'SELF-TEST FAILED'))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

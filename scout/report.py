"""The weekly digest.

WRITTEN TO BE READ, WHICH MEANS IT LEADS WITH CHANGE, NOT WITH A TABLE.
The same twenty names rank in roughly the same order most weeks. A report that opens with that
table gets skimmed by week two and ignored by week four. This one opens with the handful of
things that are different from last Monday and the specific action each one implies; the full
standings are further down for whoever wants them.

EVERY NUMBER CARRIES ITS OWN CAVEAT INLINE.
There is no disclaimer section at the bottom that nobody reads. If momentum rests on 47% of the
signal weight, the row says so where the number is. If the tool cannot yet measure trajectory
at all, that is the first line of the report rather than a footnote.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model      # noqa: E402
import score      # noqa: E402
import snapshot   # noqa: E402

OUT = os.path.join(model.BASE, 'out')

QUAD_NOTE = {
    'RISING':       'small rooms, moving fast — the signing target',
    'EARLY':        'small and quiet — keep watching',
    'ESTABLISHED':  'already big — likely repped and priced',
    'STALLED':      'big rooms, flat',
    'UNCLASSIFIED': 'venue unreadable — data problem, not an artist problem',
}


def _candidate_change(r):
    """Only artist candidates belong in signing-change sections."""
    return bool(r.get('candidate_eligible'))


def _pct(x):
    return '—' if x is None else f'{int(x * 100)}%'


def _n(x):
    return '—' if x is None else str(x)


def render(ranked, deltas, obs, platform_candidates=None, health=None, asof=None,
           tips_report=None, tip_matches=None):
    asof = asof or dt.date.today().isoformat()
    L = []
    A = L.append

    A(f'# Artist Scout — India · {asof}')
    A('')

    # --- the honesty line comes first, always
    if not obs['trajectory_observable']:
        A(f'> **Trajectory is not measurable yet.** {obs["why"]}.')
        A('> Everything below is a *level* reading — where artists stand today, not how fast')
        A('> they are moving. Rankings will change substantially once momentum comes online;')
        A('> treat this as building the baseline, not as a shortlist.')
    else:
        A(f'> Series: **{obs["n_snapshots"]} crawls over {obs["span_days"]} days** '
          f'({obs["first"]} → {obs["last"]}). Trajectory signals are live.')
    A('')

    # ---------------------------------------------------------------- actions
    rising = [r for r in ranked if r['candidate_eligible'] and r['quadrant'] == 'RISING']
    A('## Do this week')
    A('')
    if not rising:
        A('Nothing has crossed into RISING. No outreach recommended.')
    else:
        for r in rising:
            cov = r['momentum']['coverage']
            xs = r['export_signal']
            A(f'**{r["name"]}** ({r["genre"]}) — {r["action"]}')
            A(f'  · **India momentum** {_n(r["momentum"]["value"])}/100 on {_pct(cov)} of '
              f'signal weight, stature {_n(r["stature"]["value"])}/100'
              + ('  ⚠ thin evidence' if (cov or 0) < 0.5 else ''))
            drivers = [p for p in r['momentum']['parts'] if p['observable']]
            drivers.sort(key=lambda p: -(p['contribution'] or 0))
            for p in drivers[:3]:
                A(f'    · {p["signal"]}: {p["note"]}')
            if xs['observable']:
                A(f'  · **Diaspora demand** {_n(xs["value"])}/100 on '
                  f'{_pct(xs["coverage"])} of demand weight')
                for p in [q for q in xs['parts'] if q['observable']]:
                    A(f'    · {p["signal"]}: {p["note"]}')
            else:
                A('  · **Diaspora demand** not yet known — nothing fetched on this artist')
            if r['export']['missing']:
                A(f'  · next: {r["export"]["missing"][0]}')
            A('')
    A('')

    # ---------------------------------------------------------------- tips
    if tips_report or tip_matches:
        A('## Tips from the team')
        A('')
        if tip_matches:
            A('**This week\'s names**')
            for m in tip_matches:
                t = m['tip']
                who = f' — flagged by {t["by"]}' if t.get('by') else ''
                A(f'- **{t["name"]}**{who}: {m["note"]}'
                  + (f' → `{m["match"]}`' if m.get('match') else ''))
            A('')
        if tips_report and tips_report.get('n'):
            done = {k: v for k, v in (tips_report.get('buckets') or {}).items()
                    if k != 'pending'}
            if done:
                A('**What happened to recent tips**')
                for st, items in sorted(done.items()):
                    A(f'- {st}: {", ".join(t["name"] for t in items[:10])}')
                A('')
        A('Tipped names are always researched, whatever they score — a person noticing '
          'somebody is evidence that arrives *before* anything a crawler can see.')
        A('')

    # ---------------------------------------------------------------- change
    A('## What changed')
    A('')
    # Formats and productions remain visible in their own section below; they are not
    # signing-change candidates and must not enter the action-oriented delta list.
    candidate_new = [r for r in deltas['new'] if _candidate_change(r)]
    candidate_moved = [m for m in deltas['moved'] if _candidate_change(m.get('artist', {}))]
    candidate_jumps = [m for m in deltas['momentum_jump'] if _candidate_change(m.get('artist', {}))]
    candidate_drops = [m for m in deltas['momentum_drop'] if _candidate_change(m.get('artist', {}))]
    candidate_gone = [g for g in deltas['gone'] if _candidate_change(g)]
    any_change = False
    if candidate_new:
        any_change = True
        A('**New names**')
        for r in candidate_new[:12]:
            A(f'- {r["name"]} — {r["quadrant"]}, {r["n_shows"]} listing(s), '
              f'stature {_n(r["stature"]["value"])}')
        A('')
    if candidate_moved:
        any_change = True
        A('**Moved between quadrants**')
        for m in candidate_moved[:12]:
            arrow = '↑' if m['now'] == 'RISING' else '→'
            A(f'- {arrow} **{m["artist"]["name"]}**: {m["was"]} → {m["now"]}  '
              f'({QUAD_NOTE[m["now"]]})')
        A('')
    if candidate_jumps:
        any_change = True
        A('**Accelerating**')
        for m in candidate_jumps[:8]:
            A(f'- {m["artist"]["name"]}: momentum {m["was"]} → {m["now"]} (+{m["delta"]})')
        A('')
    if candidate_drops:
        any_change = True
        A('**Cooling**')
        for m in candidate_drops[:8]:
            A(f'- {m["artist"]["name"]}: momentum {m["was"]} → {m["now"]} ({m["delta"]})')
        A('')
    if candidate_gone:
        any_change = True
        A('**No longer listing anywhere** (3+ weeks)')
        for g in candidate_gone[:8]:
            A(f'- {g["name"]} — last seen in the {g["last_seen_run"]} crawl')
        A('')
    if not any_change:
        A('Nothing moved. Either the week was quiet or the crawl missed a source — '
          'check data health below before concluding the former.')
        A('')

    # ---------------------------------------------------------------- standings
    A('## Watchlist')
    A('')
    A('Two independent axes. **Momentum** is India-side — rooms, added nights, sell-through. '
      '**Demand** is diaspora-side — US/CA search volume, its growth, and whether a foreign '
      'promoter has already booked them. They are never combined; an artist needs both.')
    A('')
    ranked_main = [r for r in ranked if r['candidate_eligible'] and r['export_weight'] is not None]
    held = [r for r in ranked if r['candidate_eligible'] and r['export_weight'] is None]
    formats = [r for r in ranked if not r['candidate_eligible']]
    A('| Entity | Genre | Quadrant | Stature | Momentum | Evid. | Demand | Rooms | Cities |')
    A('|---|---|---|--:|--:|--:|--:|---|--:|')
    for r in ranked_main[:40]:
        lvl = r.get('_level', {})
        xs = r['export_signal']
        A(f'| {r["name"]} | {r["genre"]} | {r["quadrant"]} | {_n(r["stature"]["value"])} | '
          f'{_n(r["momentum"]["value"])} | {_pct(r["momentum"]["coverage"])} | '
          f'{_n(xs["value"])} | {lvl.get("best_room_band") or "—"} | '
          f'{_n(lvl.get("n_cities"))} |')
    A('')
    if held:
        A(f'**{len(held)} held out of the ranking — genre unclassified.** These are tracked but '
          f'not ranked, because scoring them on comedy\'s thresholds would file them wrongly. '
          f'The usual cause is a crawl that did not capture the platform\'s own category.')
        A('')
        for r in held[:15]:
            A(f'- {r["name"]} — {r["n_shows"]} listing(s)')
        A('')
    if formats:
        A(f'**{len(formats)} non-artist format(s) tracked separately.** Productions, festivals, '
          f'and fan events can reveal market activity, but they are never signing candidates.')
        A('')
        for r in formats[:15]:
            A(f'- {r["name"]} — {r["kind"]}, {r["n_shows"]} listing(s)')
        A('')


    # genre spread — the "what else is out there" view the brief asked for
    spread = {}
    for r in [x for x in ranked if x['candidate_eligible']]:
        spread.setdefault(r['genre'], []).append(r)
    A('**By genre**')
    A('')
    A('| Genre | Tracked | Rising | Exports? |')
    A('|---|--:|--:|---|')
    for gname, items in sorted(spread.items(), key=lambda kv: -len(kv[1])):
        w = items[0]['export_weight']
        rise = sum(1 for x in items if x['quadrant'] == 'RISING')
        A(f'| {gname} | {len(items)} | {rise} | '
          f'{"held out" if w is None else f"{w:.2f}"} |')
    A('')

    # ---------------------------------------------------------------- platforms
    A('## Ticketing platforms')
    A('')
    if platform_candidates:
        A('**Not in our source list — worth a look:**')
        for d in platform_candidates:
            A(f'- `{d}`')
        A('')
        A('A domain appearing here repeatedly is either a platform to start crawling or a '
          'competitor selling into our market. Add it to `data/sources.json` to track it.')
    else:
        A('No unrecognised ticketing domains surfaced this run.')
    A('')

    # ---------------------------------------------------------------- health
    A('## Data health')
    A('')
    if health:
        A('| Source | Rows | Parsed | Review | Parse rate |')
        A('|---|--:|--:|--:|--:|')
        for h in health:
            flag = ' ⚠' if h['parse_rate'] < 0.6 else ''
            A(f'| {h["source"]} | {h["raw"]} | {h["kept"]} | {h["review"]} | '
              f'{int(h["parse_rate"] * 100)}%{flag} |')
        A('')
        weak = [h for h in health if h['parse_rate'] < 0.6]
        if weak:
            A(f'⚠ {", ".join(h["source"] for h in weak)} parsed under 60%. The adapter or the '
              f'title conventions changed — rows are in the snapshot\'s `review` list, not lost.')
            A('')
    unclass = sum(1 for r in ranked if r['candidate_eligible'] and r['quadrant'] == 'UNCLASSIFIED')
    if unclass:
        A(f'⚠ {unclass} artist(s) have no classifiable room. Add their venues to '
          f'`data/venues_india.json` — until then they cannot be ranked.')
        A('')

    # ---------------------------------------------------------------- method
    A('---')
    A('')
    A('### How to read this')
    A('')
    A('**These are not ticket sales.** No Indian platform publishes them. Every signal here is '
      'a proxy for what a promoter *believes*: the room they booked, whether they added a '
      'night, whether the platform gatekeeping them changed. That is a real signal — someone '
      'is risking money on it — but it is weaker than a sales figure and is never presented '
      'as one.')
    A('')
    A('**Momentum is not calibrated.** The weights are judgement, not measurement, because the '
      'ground truth ("who actually broke out") only exists in hindsight. Every run stores its '
      'inputs so this can be back-tested once the history is long enough — see '
      '`scout/backtest.py`, deliberately empty until then.')
    A('')
    A('**Indian traction does not imply diaspora demand — which is why there are two scores.** '
      'The Artist Tour Engine measured it: tickets per million followers spans 15× across its '
      'roster and the follower→draw fit is sublinear, so reach in India and sales to the North '
      'American diaspora come apart badly. Momentum and Demand are therefore computed from '
      'different data by different modules and are never added together. An artist needs both '
      'to be worth a call.')
    A('')
    A('**Search volume is refreshed monthly, not weekly.** Google updates Keyword Planner once '
      'a month; polling it weekly would redraw the same figure four times and look like a '
      'stalled artist. A single rolling 12-month pull cannot produce true year-over-year because '
      'it lacks the same-month prior-year pair. YoY is observable as soon as stored history has '
      'that pair — a 48-month pull can provide it immediately. Until then the growth figure is '
      'a within-window trend and is labelled as one.')
    A('')
    A('**Even so, no number here is a North American ticket count.** A high Demand score means '
      '"worth forecasting", never "will sell N tickets in Toronto". That number comes from the '
      'Tour Engine, which is back-tested; this tool is not.')
    return '\n'.join(L)


def write(text, asof=None):
    asof = asof or dt.date.today().isoformat()
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, f'scout_{asof}.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p


def _selftest():
    import demand
    import signals
    import tempfile
    import tips
    import watchlist
    tmp = tempfile.mkdtemp()
    snapshot.SNAPDIR = os.path.join(tmp, 'snap')
    snapshot.LEDGER = os.path.join(tmp, 'shows.json')
    watchlist.PATH = os.path.join(tmp, 'watchlist.json')
    demand.PATH = os.path.join(tmp, 'demand.json')
    tips.PATH = os.path.join(tmp, 'tips.json')
    global OUT
    OUT = os.path.join(tmp, 'out')

    def ev(t, v, c, u, d, st, cat):
        return dict(title=t, venue=v, city=c, url=u, date=d, status=st, category=cat)

    weeks = {
        '2026-06-06': [('townscript', ev('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                                         'https://t/1', '2026-08-02', 'Available', 'Comedy')),
                       ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                                         'https://t/9', '2026-08-05', 'Available', 'Comedy')),
                       ('bookmyshow', ev('Bhajan Sandhya with Meera Joshi', 'Sophia Bhabha Hall',
                                         'Mumbai', 'https://b/80', '2026-09-02', 'Available',
                                         'Devotional'))],
        '2026-06-20': [('townscript', ev('Neel Sharma: Rough Draft', 'The Habitat', 'Mumbai',
                                         'https://t/1', '2026-08-02', 'Sold Out', 'Comedy')),
                       ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                                         'https://t/9', '2026-08-05', 'Available', 'Comedy'))],
        '2026-07-11': [('bookmyshow', ev('Neel Sharma: Rough Draft', 'Sophia Bhabha Hall',
                                         'Mumbai', 'https://b/2', '2026-09-20', 'Available',
                                         'Comedy')),
                       ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                                         'https://t/9', '2026-08-05', 'Available', 'Comedy'))],
        '2026-08-01': [('bookmyshow', ev('Neel Sharma: Rough Draft', 'Sophia Bhabha Hall',
                                         'Mumbai', 'https://b/2', '2026-09-20', 'Sold Out',
                                         'Comedy')),
                       ('bookmyshow', ev('Neel Sharma: Rough Draft', 'Sophia Bhabha Hall',
                                         'Mumbai', 'https://b/3', '2026-09-21', 'Available',
                                         'Comedy')),
                       ('bookmyshow', ev('Neel Sharma: Rough Draft', 'Good Shepherd Auditorium',
                                         'Bengaluru', 'https://b/4', '2026-10-03', 'Available',
                                         'Comedy')),
                       ('townscript', ev('Ira Bose Live', 'Blue Frog Basement', 'Pune',
                                         'https://t/9', '2026-08-05', 'Available', 'Comedy')),
                       ('bookmyshow', ev('Arjun Mehta Live', 'Some Hall', 'Surat',
                                         'https://b/99', '2026-10-09', 'Available', None))],
    }
    health = []
    for d, rows in weeks.items():
        for src in sorted(set(r[0] for r in rows)):
            payload = []
            for source, event in rows:
                if source != src:
                    continue
                performer = ('Neel Sharma' if event['title'].startswith('Neel Sharma') else
                             'Meera Joshi' if 'Meera Joshi' in event['title'] else
                             'Arjun Mehta' if event['title'].startswith('Arjun Mehta') else None)
                payload.append(dict(event, platform_performers=[performer] if performer else []))
            health = [snapshot.ingest(payload, src, d)]
    snapshot.rebuild_ledger()

    def months(sy, sm, vals):
        out, y, m = [], sy, sm
        for v in vals:
            out.append(dict(year=y, month=m, searches=v))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out
    demand.record_search('neel-sharma', 'us', 16000,
                         months(2025, 8, [4000, 4300, 4900, 5400, 6100, 7000,
                                          8200, 9600, 11000, 13000, 15000, 17000]),
                         name='Neel Sharma', source='keyword planner')
    demand.record_international('neel-sharma', [
        dict(country='United Kingdom', city='London', date='2026-12-01', source='serp')])

    tips.add(['Ira Bose', 'Totally Unseen Person'], by='Vihar', on='2026-08-01')

    sigs = signals.for_all(asof='2026-08-01')
    ranked = score.rank(sigs)
    bylevel = dict((s['slug'], s['level']) for s in sigs)
    for r in ranked:
        r['_level'] = bylevel[r['slug']]

    entities = snapshot.by_entity()
    tip_matches = tips.match_against_ledger(entities)
    deltas = watchlist.diff(ranked, asof='2026-08-01')
    text = render(ranked, deltas, signals.observability(),
                  platform_candidates=['ticketgenie.example'], health=health,
                  asof='2026-08-01', tips_report=tips.loop_report(),
                  tip_matches=tip_matches)
    p = write(text, '2026-08-01')
    print(text[:3000])
    print()
    print('  ... written to %s' % p)

    format_delta = dict(deltas)
    format_delta['gone'] = list(deltas['gone']) + [dict(
        slug='production-gone', name='Production Format', last_seen_run='2026-07-01',
        candidate_eligible=False, kind='production')]
    format_text = render(ranked, format_delta, signals.observability(),
                         platform_candidates=['ticketgenie.example'], health=health,
                         asof='2026-08-01', tips_report=tips.loop_report(),
                         tip_matches=tip_matches)
    action_section = format_text.split('## Watchlist', 1)[0]

    checks = [
        ('has an action section', 'Do this week' in text),
        ('shows both axes', 'India momentum' in text and 'Diaspora demand' in text),
        ('explains they are never combined', 'never combined' in text
         or 'never added together' in text),
        ('reports the tips loop', 'Tips from the team' in text),
        ('holds unclassified genre out', 'genre unclassified' in text),
        ('has a genre spread table', 'By genre' in text),
        ('states the monthly volume cadence', 'refreshed monthly' in text),
        ('explains when YoY is observable', '48-month pull can provide it immediately' in text
         and 'True year-over-year needs about a year' not in text),
        ('vanished format excluded from action changes', 'Production Format' not in action_section),
        ('still refuses a ticket number', 'never "will sell N tickets' in text),
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

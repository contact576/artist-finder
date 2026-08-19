"""Does momentum actually predict anything? Deliberately unable to answer yet.

WHY THIS FILE EXISTS BEFORE IT WORKS.
score.py assigns weights by judgement and says so. The honest response to that is not to
apologise in a comment, it is to build the thing that will eventually replace judgement with
measurement — and to make its absence loud. The Artist Tour Engine's bands are trustworthy
precisely because its backtest.py fails when coverage drifts; an earlier version of that engine
asserted a band covering 39% of outcomes while implying it covered most. This project is one
year of crawling away from being able to make the same check, and until then every score it
emits is labelled uncalibrated.

WHAT IT WILL MEASURE, AND WHAT IT CANNOT.
Ground truth here is our OWN later observation: given the state at week W, did the artist's
room band or city count rise by week W+HORIZON? That tests whether momentum anticipates future
escalation. It does NOT test commercial success, and it never will from this data — no Indian
platform publishes ticket counts. Anyone reading a future PASS here should read this paragraph
first: the metric is "did we see them get bigger", not "did they make money".

The one path to real ground truth is the roster: when Millennial actually books one of these
artists, the settled result goes into the Artist Tour Engine as an `actual` and becomes a hard
data point. Twenty of those beats a thousand crawls.

    python backtest.py            report readiness, run if possible
    python backtest.py --force    run on whatever exists (for development only)
"""
import argparse
import datetime as dt
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watchlist  # noqa: E402

HORIZON_WEEKS = 12       # how far ahead a score is asked to see
MIN_HISTORY_WEEKS = 26   # need the horizon twice over before any fold is meaningful
MIN_ARTISTS = 25         # below this the result is anecdote


def readiness(wl=None):
    wl = wl or watchlist.load()
    arts = wl.get('artists', {})
    spans = []
    for a in arts.values():
        h = a.get('history') or []
        if len(h) >= 2:
            d0 = dt.date.fromisoformat(h[0]['run'])
            d1 = dt.date.fromisoformat(h[-1]['run'])
            spans.append((d1 - d0).days / 7.0)
    weeks = max(spans) if spans else 0
    usable = [a for a in arts.values() if len(a.get('history') or []) >= 2]
    ready = weeks >= MIN_HISTORY_WEEKS and len(usable) >= MIN_ARTISTS
    return dict(ready=ready, weeks_of_history=round(weeks, 1),
                artists_with_history=len(usable), total_artists=len(arts),
                need_weeks=MIN_HISTORY_WEEKS, need_artists=MIN_ARTISTS,
                blocking=None if ready else
                (f'have {round(weeks,1)}w of history (need {MIN_HISTORY_WEEKS}) and '
                 f'{len(usable)} artists with 2+ observations (need {MIN_ARTISTS})'))


def _pairs(wl, horizon_weeks=HORIZON_WEEKS):
    """(momentum at time W, stature change by W+horizon) for every artist and every W."""
    out = []
    for slug, a in (wl.get('artists') or {}).items():
        h = [x for x in (a.get('history') or []) if x.get('momentum') is not None]
        for i, past in enumerate(h):
            t0 = dt.date.fromisoformat(past['run'])
            later = [x for x in h[i + 1:]
                     if (dt.date.fromisoformat(x['run']) - t0).days >= horizon_weeks * 7]
            if not later:
                continue
            then, now = past.get('stature'), later[0].get('stature')
            if then is None or now is None:
                continue
            out.append(dict(slug=slug, run=past['run'], momentum=past['momentum'],
                            stature_then=then, stature_now=now, delta=now - then))
    return out


def run(wl=None, horizon_weeks=HORIZON_WEEKS):
    wl = wl or watchlist.load()
    pairs = _pairs(wl, horizon_weeks)
    if not pairs:
        return dict(ok=False, reason='no artist has two observations far enough apart')

    hi = [p['delta'] for p in pairs if p['momentum'] >= 45]
    lo = [p['delta'] for p in pairs if p['momentum'] < 45]
    res = dict(
        ok=True, n_pairs=len(pairs), horizon_weeks=horizon_weeks,
        n_high_momentum=len(hi), n_low_momentum=len(lo),
        median_gain_high=statistics.median(hi) if hi else None,
        median_gain_low=statistics.median(lo) if lo else None,
    )
    if hi and lo:
        res['separation'] = res['median_gain_high'] - res['median_gain_low']
        res['verdict'] = ('momentum separates' if res['separation'] > 5 else
                          'NO SEPARATION — the weights are not earning their keep')
    else:
        res['verdict'] = 'one momentum group is empty; cannot compare'
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args(argv)

    r = readiness()
    print('Artist Scout — momentum back-test')
    print('=' * 62)
    print(f"  history          : {r['weeks_of_history']}w  (need {r['need_weeks']}w)")
    print(f"  artists w/ 2+ obs: {r['artists_with_history']}  (need {r['need_artists']})")
    print(f"  total watched    : {r['total_artists']}")

    if not r['ready'] and not a.force:
        print(f"\n  NOT READY — {r['blocking']}")
        print('\n  This is the expected state early on, not a failure. Until it clears,')
        print('  every momentum score is heuristic and is labelled `calibrated: False`.')
        print('  Re-run after the weekly job has been collecting for about six months.')
        return 0

    out = run()
    print()
    for k, v in out.items():
        print(f'  {k:20} {v}')
    if out.get('ok') and 'NO SEPARATION' in str(out.get('verdict', '')):
        print('\n  ACTION: the weights in score.py are not predictive. Re-derive them from')
        print('  these pairs rather than leaving a score that looks measured and is not.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

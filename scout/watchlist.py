"""Persistent state — what changed since last week, and who we have already spoken to.

TWO KINDS OF FIELD, AND THE RULE THAT KEEPS THEM APART.

  MACHINE fields (quadrant, stature, momentum, last_seen) are recomputed every run and
  overwritten freely.

  HUMAN fields (status, owner, contact notes) are written by a person and are NEVER touched by
  a run. Someone recording "passed — asking too much" in September must still see it in March.
  A scout that forgets its own outreach history makes the same call twice and looks amateur to
  the artist on the other end.

WHY DELTAS ARE THE PRODUCT.
A ranked list is the same list most weeks and stops being read by about week four. What is
worth a human's Monday is the CHANGE: who appeared, who crossed into RISING, whose momentum
broke out or collapsed. `diff()` produces exactly that, and report.py leads with it.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

PATH = os.path.join(model.BASE, 'data', 'watchlist.json')

HUMAN_FIELDS = ('status', 'owner', 'contact_notes', 'first_contacted', 'do_not_pursue')
STATUSES = ('new', 'watching', 'researching', 'contacted', 'negotiating', 'signed', 'passed')

# A momentum move smaller than this is noise from a single new listing, not a trend.
MOMENTUM_NOISE = 8


def load():
    if not os.path.exists(PATH):
        return dict(updated_at=None, artists={})
    with open(PATH, encoding='utf-8') as f:
        return json.load(f)


def save(wl):
    wl['updated_at'] = dt.datetime.now().isoformat(timespec='seconds')
    with open(PATH, 'w', encoding='utf-8') as f:
        json.dump(wl, f, indent=1, ensure_ascii=False)
    return PATH


def diff(ranked, wl=None, asof=None):
    """Compare this run against stored state. Returns deltas WITHOUT saving.

    Kept side-effect free so a dry run can show what would change — the same discipline as
    sync_edited.py --check in the 2027 project, and for the same reason: a state file that
    mutates just because you looked at it cannot be reasoned about.
    """
    wl = wl or load()
    asof = asof or dt.date.today().isoformat()
    prev = wl.get('artists', {})
    new, moved, momentum_jump, momentum_drop, gone = [], [], [], [], []

    seen = set()
    for r in ranked:
        slug = r['slug']
        seen.add(slug)
        p = prev.get(slug)
        if p is None:
            new.append(r)
            continue
        if p.get('quadrant') != r['quadrant']:
            moved.append(dict(artist=r, was=p.get('quadrant'), now=r['quadrant']))
        pm, nm = p.get('momentum'), r['momentum']['value']
        if pm is not None and nm is not None:
            if nm - pm >= MOMENTUM_NOISE:
                momentum_jump.append(dict(artist=r, was=pm, now=nm, delta=nm - pm))
            elif pm - nm >= MOMENTUM_NOISE:
                momentum_drop.append(dict(artist=r, was=pm, now=nm, delta=nm - pm))

    for slug, p in prev.items():
        if slug not in seen and not p.get('do_not_pursue'):
            last = p.get('last_seen_run')
            if last and (dt.date.fromisoformat(asof) - dt.date.fromisoformat(last)).days >= 21:
                # Persisted rows carry the entity kind and candidate gate. Older watchlists may
                # lack candidate_eligible, so infer conservatively from the stored kind.
                eligible = p.get('candidate_eligible')
                if eligible is None:
                    eligible = p.get('kind', 'artist') in ('artist', 'dj_night')
                gone.append(dict(slug=slug, name=p.get('name'), last_seen_run=last,
                                 candidate_eligible=bool(eligible),
                                 kind=p.get('kind', 'artist')))

    moved.sort(key=lambda m: -(m['artist']['momentum']['value'] or 0))
    momentum_jump.sort(key=lambda m: -m['delta'])
    return dict(new=new, moved=moved, momentum_jump=momentum_jump,
                momentum_drop=momentum_drop, gone=gone)


def apply(ranked, wl=None, asof=None):
    """Write machine fields back. Human fields are preserved verbatim."""
    wl = wl or load()
    asof = asof or dt.date.today().isoformat()
    arts = wl.setdefault('artists', {})
    for existing in arts.values():
        existing['active'] = False
    for r in ranked:
        e = arts.setdefault(r['slug'], dict(
            slug=r['slug'], name=r['name'], first_seen_run=asof,
            status='new', owner=None, contact_notes=[], first_contacted=None,
            do_not_pursue=False))
        human = {k: e.get(k) for k in HUMAN_FIELDS if k in e}
        e.update(dict(
            name=r['name'], last_seen_run=asof,
            quadrant=r['quadrant'], stature=r['stature']['value'],
            momentum=r['momentum']['value'],
            momentum_coverage=r['momentum']['coverage'],
            n_shows=r['n_shows'], action=r['action'],
            kind=r.get('kind', 'artist'), genre=r.get('genre', 'unknown'),
            active=True,
            candidate_eligible=r.get('candidate_eligible', True),
            export_ready=r['export']['ready']))
        e.update(human)                      # human always wins
        # ONE ENTRY PER RUN DATE — replaced, not appended. Re-running a date is routine: a crawl
        # gets retried, a parser gets fixed, a report gets regenerated. An append-only history
        # turned every one of those into a duplicate row. That matters because this history IS
        # the back-test set — duplicated same-day entries would weight one week several times
        # over and corrupt the very measurement it exists to make possible.
        hist = [h for h in (e.get('history') or []) if h.get('run') != asof]
        hist.append(dict(run=asof, quadrant=r['quadrant'], stature=r['stature']['value'],
                         momentum=r['momentum']['value']))
        hist.sort(key=lambda h: h['run'])
        e['history'] = hist[-104:]          # two years of weekly runs
    return wl


def set_status(slug, status, note=None, owner=None):
    """Human edit. The only writer of HUMAN_FIELDS."""
    if status not in STATUSES:
        raise ValueError(f'status must be one of {STATUSES}')
    wl = load()
    e = wl.setdefault('artists', {}).get(slug)
    if e is None:
        raise KeyError(f'{slug} is not on the watchlist')
    e['status'] = status
    if owner:
        e['owner'] = owner
    if status == 'contacted' and not e.get('first_contacted'):
        e['first_contacted'] = dt.date.today().isoformat()
    if status == 'passed':
        e['do_not_pursue'] = True
    if note:
        e.setdefault('contact_notes', []).append(
            dict(date=dt.date.today().isoformat(), note=note))
    save(wl)
    return e


def _selftest():
    global PATH
    import tempfile
    PATH = os.path.join(tempfile.mkdtemp(), 'watchlist.json')

    def fake(slug, name, quad, stat, mom, cov=1.0, kind='artist', eligible=True):
        return dict(slug=slug, name=name, quadrant=quad, n_shows=3,
                    stature=dict(value=stat), momentum=dict(value=mom, coverage=cov),
                    export=dict(ready=False), action='WATCH', kind=kind,
                    candidate_eligible=eligible)

    wk1 = [fake('a-one', 'A One', 'EARLY', 20, 30), fake('b-two', 'B Two', 'EARLY', 25, 20),
           fake('production-one', 'Production One', 'EARLY', 15, 10,
                kind='production', eligible=False)]
    d1 = diff(wk1, asof='2026-08-01')
    print(f'  run 1: new={[r["slug"] for r in d1["new"]]}')
    save(apply(wk1, asof='2026-08-01'))

    set_status('b-two', 'contacted', note='emailed manager', owner='Dhaval')

    wk2 = [fake('a-one', 'A One', 'RISING', 40, 62), fake('b-two', 'B Two', 'EARLY', 25, 22),
           fake('c-new', 'C New', 'EARLY', 10, 15)]
    d2 = diff(wk2, asof='2026-08-08')
    print(f'  run 2: new={[r["slug"] for r in d2["new"]]} '
          f'moved={[(m["was"], m["now"], m["artist"]["slug"]) for m in d2["moved"]]} '
          f'jump={[(m["slug"] if "slug" in m else m["artist"]["slug"], m["delta"]) for m in d2["momentum_jump"]]}')
    wl = apply(wk2, asof='2026-08-08')
    save(wl)

    b = wl['artists']['b-two']
    checks = [
        ('new artist detected', [r['slug'] for r in d2['new']] == ['c-new']),
        ('quadrant move detected', d2['moved'] and d2['moved'][0]['artist']['slug'] == 'a-one'),
        ('momentum jump detected', any(m['artist']['slug'] == 'a-one'
                                       for m in d2['momentum_jump'])),
        ('noise move ignored', not any(m['artist']['slug'] == 'b-two'
                                       for m in d2['momentum_jump'])),
        ('human status survived rerun', b['status'] == 'contacted'),
        ('human owner survived rerun', b['owner'] == 'Dhaval'),
        ('contact note survived', len(b['contact_notes']) == 1),
        ('history accumulating', len(wl['artists']['a-one']['history']) == 2),
    ]
    # Re-running the SAME date must not duplicate a history row — it is the back-test set.
    wl3 = apply(wk2, asof='2026-08-08')
    checks.append(('re-running a date replaces, does not duplicate',
                   len(wl3['artists']['a-one']['history']) == 2))
    checks.append(('history stays sorted by run date',
                   [h['run'] for h in wl3['artists']['a-one']['history']]
                   == sorted(h['run'] for h in wl3['artists']['a-one']['history'])))
    d3 = diff(wk2, asof='2026-08-29')
    checks.append(('vanished production is marked non-candidate',
                   any(g['slug'] == 'production-one' and not g['candidate_eligible']
                       and g['kind'] == 'production' for g in d3['gone'])))
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

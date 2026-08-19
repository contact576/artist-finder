"""Human tips — the names the team spots before any crawler does.

WHY THIS IS A PASTE AND NOT AN INTEGRATION.
The team shares artists in an Instagram group chat. No API reaches it: personal accounts have no
Graph API access at all, business accounts need `instagram_business_manage_messages` through
Meta App Review and are limited to a 24-hour window on conversations a customer started, and
group threads are not a supported surface even then. No Apify actor reads DMs. Reading them
would mean driving a logged-in browser session, which is fragile and puts the account at risk.

So the inbox is deliberately dumb: paste names in, any time, from anywhere. Two minutes a week
buys a signal the crawler structurally cannot produce.

WHY A TIP OUTRANKS THE SCORE.
Someone in the team saw a reel and thought "that person is going to be big". That judgement is
evidence, and it arrives BEFORE the supply-side signals this tool measures — before the bigger
room is booked, before the second night is added. A tipped entity is therefore ALWAYS researched
regardless of quadrant. Filtering a human tip through a heuristic score would throw away the
earliest signal available in favour of a later one.

CLOSING THE LOOP IS WHAT KEEPS TIPS COMING.
Every digest reports what happened to the previous week's tips — already tracked, newly added,
or could not be found. A tip box that swallows names in silence stops being used by about week
three, and then the whole mechanism is dead.
"""
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

PATH = os.path.join(model.BASE, 'data', 'tips.json')

STATUSES = ('pending', 'matched', 'created', 'not_found', 'duplicate', 'rejected')
DEFAULT_SOURCE = 'instagram-group'


def load():
    if not os.path.exists(PATH):
        return dict(updated_at=None, tips=[])
    with open(PATH, encoding='utf-8') as f:
        return json.load(f)


def save(d):
    d['updated_at'] = dt.datetime.now().isoformat(timespec='seconds')
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    return PATH


def _tip_id(name, on):
    return 't:' + hashlib.sha1(f'{model.slugify(name)}|{on}'.encode('utf-8')).hexdigest()[:12]


def add(names, by=None, note=None, source=DEFAULT_SOURCE, on=None, link=None):
    """Record one or more tipped names. Returns the tips actually added.

    Re-adding the same name on the same day is a no-op, so pasting the chat twice is harmless —
    which matters, because somebody will.
    """
    on = on or dt.date.today().isoformat()
    if isinstance(names, str):
        names = [names]
    d = load()
    have = {t['id'] for t in d['tips']}
    added = []
    for raw in names:
        nm = (raw or '').strip()
        if not nm:
            continue
        tid = _tip_id(nm, on)
        if tid in have:
            continue
        t = dict(id=tid, name=nm, slug=model.slugify(nm), on=on, by=by, note=note,
                 link=link, source=source, status='pending',
                 resolved_slug=None, resolved_on=None, outcome=None)
        d['tips'].append(t)
        added.append(t)
    if added:
        save(d)
    return added


def add_batch(entries, on=None):
    """entries = [{'name', 'by', 'note', 'link'}] — one paste of a week's chat."""
    out = []
    for e in entries or []:
        out += add(e.get('name'), by=e.get('by'), note=e.get('note'),
                   source=e.get('source', DEFAULT_SOURCE), on=on, link=e.get('link'))
    return out


def pending(d=None):
    return [t for t in (d or load())['tips'] if t['status'] == 'pending']


def resolve(tip_id, status, resolved_slug=None, outcome=None):
    if status not in STATUSES:
        raise ValueError(f'status must be one of {STATUSES}')
    d = load()
    for t in d['tips']:
        if t['id'] == tip_id:
            t.update(status=status, resolved_slug=resolved_slug, outcome=outcome,
                     resolved_on=dt.date.today().isoformat())
            save(d)
            return t
    raise KeyError(tip_id)


MIN_TOKEN = 4       # "raj" is not distinctive enough to match on; "bassi" is


def match_against_ledger(ledger_entities, d=None):
    """Try to attach each pending tip to something already in the ledger. Does NOT save.

    Matching is on NAME TOKENS, not substrings. People tip nicknames — "Bassi" for Anubhav
    Singh Bassi — so the tip's tokens have to be a subset of the ledger name's tokens. An
    earlier substring version with a length guard rejected exactly that case, which is the
    single most common shape a real tip takes.

    Two guards keep it safe, and they matter more than the matching itself:
      - every token must be at least MIN_TOKEN characters, so "raj" cannot claim anybody
      - the match must be UNIQUE. "Singh" hits a dozen artists, so it hits none of them.
    A wrongly merged artist is far worse than an unmatched tip: the tip just needs research,
    the merge silently corrupts two histories at once.
    """
    d = d or load()
    slugs = set(ledger_entities or {})
    out = []
    for t in pending(d):
        hit, how = None, 'no ledger entry — needs research'
        tips_tok = {x for x in (t['slug'] or '').split('-') if len(x) >= MIN_TOKEN}
        if t['slug'] in slugs:
            hit, how = t['slug'], 'exact'
        elif tips_tok:
            cands = [s for s in slugs
                     if tips_tok <= {x for x in s.split('-') if len(x) >= MIN_TOKEN}]
            if len(cands) == 1:
                hit, how = cands[0], 'partial (name tokens)'
            elif len(cands) > 1:
                how = f'ambiguous — matches {len(cands)} entries, not guessing'
        out.append(dict(tip=t, match=hit, note=how))
    return out


def tipped_slugs(d=None):
    """Every slug a human has ever flagged. These are always researched, whatever they score."""
    d = d or load()
    out = set()
    for t in d['tips']:
        if t['status'] in ('matched', 'created') and t['resolved_slug']:
            out.add(t['resolved_slug'])
        elif t['status'] == 'pending':
            out.add(t['slug'])
    return out


def loop_report(since_days=14, d=None):
    """What became of recent tips. This is what gets printed back to whoever sent them."""
    d = d or load()
    cutoff = (dt.date.today() - dt.timedelta(days=since_days)).isoformat()
    recent = [t for t in d['tips'] if t['on'] >= cutoff]
    buckets = {}
    for t in recent:
        buckets.setdefault(t['status'], []).append(t)
    return dict(n=len(recent), since=cutoff, buckets=buckets,
                pending=len(buckets.get('pending', [])))


def _selftest():
    global PATH
    import tempfile
    PATH = os.path.join(tempfile.mkdtemp(), 'tips.json')

    a = add(['Neel Sharma', 'Bassi', 'Singh', 'Someone Nobody Lists'], by='Vihar',
            note='from the Monday chat', on='2026-08-17')
    print(f'  added {len(a)} tips')
    dup = add(['Neel Sharma'], on='2026-08-17')
    print(f'  re-paste added {len(dup)} (should be 0)')

    ledger = {'neel-sharma': 1, 'anubhav-singh-bassi': 1, 'zara-qureshi': 1,
              'jaspreet-singh': 1, 'harnav-singh': 1}
    matches = match_against_ledger(ledger)
    print('\n  matching against the ledger:')
    for m in matches:
        print(f'    {m["tip"]["name"]:24} -> {str(m["match"]):24} ({m["note"]})')

    for m in matches:
        if m['match']:
            resolve(m['tip']['id'], 'matched', m['match'], 'already tracked')
        else:
            resolve(m['tip']['id'], 'not_found', None, 'no listings found anywhere yet')

    rep = loop_report(since_days=30)
    print(f'\n  loop report: {rep["n"]} tips, pending={rep["pending"]}')
    for st, items in sorted(rep['buckets'].items()):
        print(f'    {st:12} {[t["name"] for t in items]}')

    ts = tipped_slugs()
    checks = [
        ('four tips added', len(a) == 4),
        ('duplicate paste ignored', len(dup) == 0),
        ('exact name matched', any(m['match'] == 'neel-sharma' for m in matches)),
        ('partial name matched', any(m['match'] == 'anubhav-singh-bassi' for m in matches)),
        ('unknown name left unmatched',
         any(m['match'] is None and 'Nobody' in m['tip']['name'] for m in matches)),
        ('ambiguous surname refused rather than guessed',
         any(m['tip']['name'] == 'Singh' and m['match'] is None
             and 'ambiguous' in m['note'] for m in matches)),
        ('tipped slugs survive resolution', 'neel-sharma' in ts and 'anubhav-singh-bassi' in ts),
        ('nothing pending after resolution', rep['pending'] == 0),
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

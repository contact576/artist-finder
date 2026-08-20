"""Audited corrections applied while rebuilding derived data.

Snapshots are evidence and are never rewritten.  That does not mean a known bad parse should
poison the ledger forever.  This module applies a small, tracked overlay from
``data/corrections.json`` while ``snapshot.rebuild_ledger()`` folds those immutable snapshots.

Every correction names an event or entity explicitly and carries a reason and review date.
Parser improvements still do not travel backwards invisibly; a historical change only happens
when somebody records it in the overlay, where it can be reviewed and version controlled.
"""
import copy
import json
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE, 'data', 'corrections.json')

EVENT_FIELDS = {
    'genre', 'genre_confidence', 'genre_evidence', 'entity_type',
    'role', 'role_certain', 'category', 'venue', 'city',
    'venue_band', 'venue_rank', 'venue_capacity_approx',
}
ENTITY_FIELDS = {'name', 'slug', 'kind'}


def slugify(name):
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-',
                                      str(name).strip().lower())).strip('-')


def load(path=None):
    path = path or PATH
    if not os.path.exists(path):
        return dict(schema_version=1, events={}, entities={})
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if data.get('schema_version') != 1:
        raise ValueError('data/corrections.json has an unsupported schema_version')
    return data


def apply_event(ev, rules=None):
    """Return a corrected copy of ``ev``, or ``None`` when it is explicitly suppressed."""
    rules = rules or load()
    out = copy.deepcopy(ev)
    event_rule = (rules.get('events') or {}).get(out.get('show_id')) or {}
    if event_rule.get('action') == 'suppress':
        return None

    for key, value in (event_rule.get('set') or {}).items():
        if key not in EVENT_FIELDS:
            raise ValueError(f'event correction cannot set {key!r}')
        out[key] = value

    source_entities = (out.get('entities') if out.get('entities') is not None
                       else out.get('artists') or [])
    corrected = []
    for entity in source_entities:
        item = dict(entity)
        rule = (rules.get('entities') or {}).get(item.get('slug')) or {}
        if rule.get('action') == 'suppress':
            continue
        for key, value in (rule.get('set') or {}).items():
            if key not in ENTITY_FIELDS:
                raise ValueError(f'entity correction cannot set {key!r}')
            item[key] = value
        if 'name' in (rule.get('set') or {}) and 'slug' not in (rule.get('set') or {}):
            item['slug'] = slugify(item['name'])
        corrected.append(item)

    if source_entities and not corrected:
        return None
    out['entities'] = corrected
    return out


def _selftest():
    rules = {
        'schema_version': 1,
        'events': {
            'u:fan': {'action': 'suppress'},
            'u:expo': {'set': {'genre': 'unknown'}},
        },
        'entities': {
            'wrong-place': {'action': 'suppress'},
            'dj-at-club': {'set': {'name': 'DJ Correct'}},
        },
    }
    fan = dict(show_id='u:fan', entities=[dict(name='Star', slug='star', kind='artist')])
    expo = dict(show_id='u:expo', genre='music_classical', entities=[
        dict(name='Expo', slug='expo', kind='festival')])
    lineup = dict(show_id='u:lineup', entities=[
        dict(name='Wrong Place', slug='wrong-place', kind='artist'),
        dict(name='DJ At Club', slug='dj-at-club', kind='artist')])
    got_expo = apply_event(expo, rules)
    got_lineup = apply_event(lineup, rules)
    checks = [
        ('event can be suppressed', apply_event(fan, rules) is None),
        ('event field can be corrected', got_expo['genre'] == 'unknown'),
        ('bad co-entity can be removed', len(got_lineup['entities']) == 1),
        ('renamed entity receives matching slug',
         got_lineup['entities'][0]['slug'] == 'dj-correct'),
        ('input remains immutable', lineup['entities'][1]['slug'] == 'dj-at-club'),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

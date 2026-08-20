"""Audited corrections applied while rebuilding derived data.

Snapshots are evidence and are never rewritten. That does not mean a known bad parse should
poison the ledger forever. This module applies a small, tracked overlay from
``data/corrections.json`` while ``snapshot.rebuild_ledger()`` folds those immutable snapshots.

Every correction names an event or entity explicitly and carries a reason and review date.
Parser improvements still do not travel backwards invisibly; a historical change only happens
when somebody records it in the overlay, where it can be reviewed and version controlled.
"""
import copy
import datetime as dt
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
RULE_FIELDS = {'action', 'set', 'reviewed_at', 'reason'}
ROOT_FIELDS = {'schema_version', '_doc', 'events', 'entities'}
ENTITY_TYPES = {'artist', 'dj_night', 'production', 'festival', 'fan_event', 'format', 'sport', 'unknown'}


def slugify(name):
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-',
                                      str(name).strip().lower())).strip('-')


def _fail(where, message):
    raise ValueError(f'Invalid corrections overlay at {where}: {message}')


def _nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _iso_date(value):
    """Strict YYYY-MM-DD dates make reviews sortable and auditable."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_set(scope, values, where):
    allowed = EVENT_FIELDS if scope == 'events' else ENTITY_FIELDS
    if not isinstance(values, dict) or not values:
        _fail(where, 'set must be a nonempty object')
    unknown = set(values) - allowed
    if unknown:
        _fail(where, f'cannot set field(s): {", ".join(sorted(unknown))}')

    for key, value in values.items():
        if key in {'genre', 'genre_evidence', 'entity_type', 'role', 'category', 'venue',
                   'city', 'venue_band', 'name', 'slug', 'kind'} and not _nonempty_string(value):
            _fail(where, f'set.{key} must be a nonempty string')
        if key == 'genre_confidence' and (isinstance(value, bool) or
                                          not isinstance(value, (int, float)) or
                                          not 0.0 <= value <= 1.0):
            _fail(where, 'set.genre_confidence must be a number from 0 to 1')
        if key == 'role_certain' and not isinstance(value, bool):
            _fail(where, 'set.role_certain must be a boolean')
        if key == 'venue_rank' and (isinstance(value, bool) or
                                    not isinstance(value, int) or value < 1):
            _fail(where, 'set.venue_rank must be a positive integer')
        if key == 'venue_capacity_approx' and value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            _fail(where, 'set.venue_capacity_approx must be a non-negative number or null')
        if key == 'entity_type' and value not in ENTITY_TYPES:
            _fail(where, f'set.entity_type must be one of {sorted(ENTITY_TYPES)}')
        if key == 'kind' and value not in ENTITY_TYPES:
            _fail(where, f'set.kind must be one of {sorted(ENTITY_TYPES)}')
        if key == 'slug' and slugify(value) != value:
            _fail(where, 'set.slug must be a canonical lowercase slug')


def _validate_rule(scope, target, rule):
    where = f'{scope}.{target!r}'
    if not _nonempty_string(target):
        _fail(scope, 'every correction key must be a nonempty string')
    if not isinstance(rule, dict):
        _fail(where, 'rule must be an object')
    unknown = set(rule) - RULE_FIELDS
    if unknown:
        _fail(where, f'unknown field(s): {", ".join(sorted(unknown))}')
    if not _iso_date(rule.get('reviewed_at')):
        _fail(where, 'reviewed_at must be an ISO calendar date (YYYY-MM-DD)')
    if not _nonempty_string(rule.get('reason')):
        _fail(where, 'reason must be a nonempty string')

    has_action, has_set = 'action' in rule, 'set' in rule
    if has_action == has_set:
        _fail(where, 'provide exactly one of action or set')
    if has_action:
        if rule['action'] != 'suppress':
            _fail(where, "action must be 'suppress'")
    else:
        _validate_set(scope, rule['set'], where)


def validate(data):
    """Validate all corrections before the immutable evidence overlay can be applied.

    Validation is all-or-nothing: malformed rule data must fail before a ledger rebuild starts.
    This function never writes snapshots; it governs only the separately tracked overlay.
    """
    if not isinstance(data, dict):
        _fail('root', 'overlay must be an object')
    unknown = set(data) - ROOT_FIELDS
    if unknown:
        _fail('root', f'unknown field(s): {", ".join(sorted(unknown))}')
    if data.get('schema_version') != 1:
        _fail('root', 'schema_version must be 1')
    if '_doc' in data and (not isinstance(data['_doc'], list) or
                           not all(isinstance(line, str) for line in data['_doc'])):
        _fail('root', '_doc must be a list of strings')
    for scope in ('events', 'entities'):
        rules = data.get(scope)
        if not isinstance(rules, dict):
            _fail(scope, 'must be an object')
        for target, rule in rules.items():
            _validate_rule(scope, target, rule)
    return data


def load(path=None):
    path = path or PATH
    if not os.path.exists(path):
        return dict(schema_version=1, events={}, entities={})
    with open(path, encoding='utf-8') as f:
        return validate(json.load(f))


def apply_event(ev, rules=None):
    """Return a corrected copy of ``ev``, or ``None`` when explicitly suppressed."""
    rules = validate(rules) if rules is not None else load()
    out = copy.deepcopy(ev)
    event_rule = (rules.get('events') or {}).get(out.get('show_id')) or {}
    if event_rule.get('action') == 'suppress':
        return None

    for key, value in (event_rule.get('set') or {}).items():
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
            'u:fan': {'action': 'suppress', 'reviewed_at': '2026-08-21',
                      'reason': 'fan event, not a performance by the artist'},
            'u:expo': {'set': {'genre': 'unknown'}, 'reviewed_at': '2026-08-21',
                       'reason': 'venue substring made an expo look like music'},
        },
        'entities': {
            'wrong-place': {'action': 'suppress', 'reviewed_at': '2026-08-21',
                            'reason': 'place name parsed as a performer'},
            'dj-at-club': {'set': {'name': 'DJ Correct'}, 'reviewed_at': '2026-08-21',
                           'reason': 'venue tail attached to the performer'},
        },
    }
    fan = dict(show_id='u:fan', entities=[dict(name='Star', slug='star', kind='artist')])
    expo = dict(show_id='u:expo', genre='music_classical', entities=[
        dict(name='Expo', slug='expo', kind='festival')])
    lineup = dict(show_id='u:lineup', entities=[
        dict(name='Wrong Place', slug='wrong-place', kind='artist'),
        dict(name='DJ At Club', slug='dj-at-club', kind='artist')])
    got_expo = apply_event(expo, rules)
    format_event = dict(show_id='u:format', entities=[
        dict(name='A Format', slug='a-format', kind='artist')])
    got_lineup = apply_event(lineup, rules)

    format_rules = dict(schema_version=1, events={}, entities={
        'a-format': {'set': {'name': 'A Corrected Format', 'kind': 'format'},
                     'reviewed_at': '2026-08-21',
                     'reason': 'audited event format must not remain an artist'}})
    got_format = apply_event(format_event, format_rules)
    invalid = (
        ('missing review date', dict(schema_version=1, events={
            'u:bad': {'action': 'suppress', 'reason': 'missing review date'}}, entities={})),
        ('bad review date', dict(schema_version=1, events={
            'u:bad': {'action': 'suppress', 'reviewed_at': '2026-2-30', 'reason': 'bad date'}},
            entities={})),
        ('empty reason', dict(schema_version=1, events={
            'u:bad': {'action': 'suppress', 'reviewed_at': '2026-08-21', 'reason': ' '}},
            entities={})),
        ('unknown action', dict(schema_version=1, events={
            'u:bad': {'action': 'delete', 'reviewed_at': '2026-08-21', 'reason': 'wrong'}},
            entities={})),
        ('action and set together', dict(schema_version=1, events={
            'u:bad': {'action': 'suppress', 'set': {'genre': 'unknown'},
                      'reviewed_at': '2026-08-21', 'reason': 'ambiguous'}}, entities={})),
        ('unknown set field', dict(schema_version=1, events={
            'u:bad': {'set': {'title': 'rewrite'}, 'reviewed_at': '2026-08-21',
                      'reason': 'snapshot rewrite attempt'}}, entities={})),
        ('wrong set shape', dict(schema_version=1, events={
            'u:bad': {'set': [], 'reviewed_at': '2026-08-21', 'reason': 'wrong shape'}},
            entities={})),
    )
    invalid_rejected = []
    for label, bad in invalid:
        try:
            validate(bad)
        except ValueError:
            invalid_rejected.append(label)

    checks = [
        ('event can be suppressed', apply_event(fan, rules) is None),
        ('event field can be corrected', got_expo['genre'] == 'unknown'),
        ('bad co-entity can be removed', len(got_lineup['entities']) == 1),
        ('renamed entity receives matching slug',
         got_lineup['entities'][0]['slug'] == 'dj-correct'),
        ('entity kind can change through the audited overlay',
         got_format['entities'][0]['kind'] == 'format' and
         got_format['entities'][0]['slug'] == 'a-corrected-format'),
        ('input remains immutable', lineup['entities'][1]['slug'] == 'dj-at-club'),
        ('every malformed rule is rejected before application',
         len(invalid_rejected) == len(invalid)),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

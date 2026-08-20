"""Deterministic lifecycle evidence for the Artist Finder workflow.

This is a workflow state machine, not a ranking and not a forecast. It joins the derived show
ledger with stored diaspora research while keeping primary-platform evidence, US/Canada values,
and identity checks explicit. It deliberately does not read momentum or trajectory: a single
crawl can establish a booking workflow state, but cannot establish a rate of rise.
"""
import datetime as dt
import json
import os
import sys
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import demand  # noqa: E402
import model  # noqa: E402

SOURCES_PATH = os.path.join(BASE, 'data', 'sources.json')
CANDIDATE_KINDS = {'artist', 'dj_night'}
PRIMARY_ROLES = {'primary', 'primary_validation'}
STAGES = ('Discovered', 'Major-platform confirmed', 'Diaspora validated', 'Forecast ready')


def _as_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _url_for_domain(value, domain=None):
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        return False
    if not domain:
        return True
    host = (parsed.hostname or '').lower()
    domain = str(domain).lower().lstrip('.')
    return host == domain or host.endswith('.' + domain)


def load_sources(path=None):
    """Load source roles afresh so lifecycle follows configuration, not hardcoded names."""
    with open(path or SOURCES_PATH, encoding='utf-8') as f:
        return json.load(f)


def _source_rows(config=None):
    config = load_sources() if config is None else config
    if isinstance(config, dict):
        rows = config.get('sources')
    else:
        rows = config
    if not isinstance(rows, list):
        raise ValueError('sources configuration must contain a sources list')
    return [row for row in rows if isinstance(row, dict) and row.get('key')]


def is_primary_source(source):
    """Accept only an explicit, configuration-owned primary-validation designation."""
    roles = source.get('roles') or []
    if not isinstance(roles, (list, tuple, set)):
        roles = []
    return (source.get('primary') is True or source.get('role') in PRIMARY_ROLES or
            source.get('validation_role') in PRIMARY_ROLES or
            bool(set(roles) & PRIMARY_ROLES))


def primary_sources(config=None):
    return {row['key']: row for row in _source_rows(config) if is_primary_source(row)}


def _brief_show(show):
    return dict(
        show_id=show.get('show_id'), source=show.get('source'), title=show.get('title'),
        url=show.get('url'), show_date=show.get('show_date'), city=show.get('city'),
        venue=show.get('venue'), venue_band=show.get('venue_band'), role=show.get('role'),
        status_history=list(show.get('status_history') or []),
        first_seen=show.get('first_seen'), last_seen=show.get('last_seen'),
    )


def _accepted_identity(show, slug):
    matches = [e for e in (show.get('entities') or []) if e.get('slug') == slug]
    if len(matches) != 1:
        return False, None, ('entity missing from the ledger row' if not matches else
                             'multiple matching entities make identity ambiguous')
    entity = matches[0]
    name = entity.get('name')
    if not isinstance(name, str) or not name.strip():
        return False, None, 'matched entity has no canonical name'
    if model.slugify(name) != slug:
        return False, None, 'entity name and canonical slug do not agree'
    if show.get('entity_type') not in CANDIDATE_KINDS:
        return False, None, 'listing was not parsed as an artist/DJ-night entity'
    kind = entity.get('kind') or show.get('entity_type')
    if kind not in CANDIDATE_KINDS:
        return False, None, 'matched entity is not a candidate performer'
    return True, dict(slug=slug, name=name, kind=kind,
                      parser_state='trusted ledger row; one canonical entity match'), None


def _primary_evidence(entity, source_map, asof):
    evidence, rejected = [], []
    for show in entity.get('shows') or []:
        source = source_map.get(show.get('source'))
        if source is None:
            continue
        date = _as_date(show.get('show_date'))
        accepted_identity, identity, identity_reason = _accepted_identity(show, entity['slug'])
        url_ok = _url_for_domain(show.get('url'), source.get('domain'))
        date_ok = date is not None and date >= asof
        if accepted_identity and url_ok and date_ok:
            item = _brief_show(show)
            item['source_name'] = source.get('name') or source['key']
            item['source_role'] = 'primary_validation'
            item['identity'] = identity
            evidence.append(item)
        else:
            rejected.append(dict(
                **_brief_show(show), source_name=source.get('name') or source['key'],
                source_role='primary_validation', accepted_identity=accepted_identity,
                rejection_reason=(identity_reason if not accepted_identity else
                                  'listing URL is missing, malformed, or outside source domain'
                                  if not url_ok else
                                  'show date is missing, malformed, or already past'),
            ))
    return evidence, rejected


def _discovery_evidence(entity, source_map):
    out = []
    for show in entity.get('shows') or []:
        if show.get('source') in source_map:
            continue
        out.append(_brief_show(show))
    return out


def _classifiable_identity(entity):
    name, slug = entity.get('name'), entity.get('slug')
    if entity.get('kind') not in CANDIDATE_KINDS:
        return False, 'entity kind is not a performer/DJ-night candidate'
    if not isinstance(name, str) or len(name.strip()) < 3:
        return False, 'canonical artist name is missing or too short to review'
    if not isinstance(slug, str) or model.slugify(name) != slug:
        return False, 'canonical artist name and slug do not agree'
    return True, 'canonical artist identity is classifiable'


def _diaspora_evidence(slug, demand_data, asof):
    entry = (demand_data.get('entities') or {}).get(slug) or {}
    search = entry.get('search') or {}
    per_geo, usable_geos = {}, []
    for geo in demand.GEOS:
        row = search.get(geo) or {}
        volume = row.get('avg_monthly')
        fetched = _as_date(row.get('fetched_at'))
        source = row.get('source')
        usable = (isinstance(volume, (int, float)) and not isinstance(volume, bool) and
                  volume > 0 and fetched is not None and isinstance(source, str) and bool(source.strip()))
        per_geo[geo] = dict(avg_monthly=volume, fetched_at=row.get('fetched_at'), source=source,
                            monthly_observations=len(row.get('monthly') or []), usable=usable,
                            note=('usable stored search evidence' if usable else
                                  'no usable stored search evidence'))
        if usable:
            usable_geos.append(geo)

    representative = None
    if usable_geos:
        strongest = max(usable_geos, key=lambda geo: per_geo[geo]['avg_monthly'])
        representative = dict(geo=strongest, avg_monthly=per_geo[strongest]['avg_monthly'],
                              rule='stronger geography shown; US and Canada are never summed')

    contamination = entry.get('contamination') or {}
    share = contamination.get('qualified_share')
    contaminated = isinstance(share, (int, float)) and share < demand.CONTAMINATION_FLOOR
    international = entry.get('international') or {}
    foreign = []
    for item in international.get('dates') or []:
        country = str(item.get('country') or '').strip().lower()
        when = _as_date(item.get('date'))
        if (country in demand.DIASPORA_MARKETS and when is not None and when >= asof and
                isinstance(item.get('source'), str) and item['source'].strip()):
            foreign.append(dict(country=item.get('country'), city=item.get('city'), date=item.get('date'),
                                source=item.get('source'), url=item.get('url')))

    # Name contamination applies to query evidence only. A separately sourced, qualifying
    # foreign booking remains useful diaspora evidence, but it is not a substitute for clean
    # US/CA search data at the final handoff gate.
    search_usable = bool(usable_geos) and not contaminated
    foreign_date_usable = bool(foreign)
    diaspora_validated = search_usable or foreign_date_usable
    forecast_ready = search_usable
    if contaminated and foreign_date_usable:
        note = (f'search is unusable: qualified share {share:.0%} is below the '
                f'{demand.CONTAMINATION_FLOOR:.0%} contamination floor; independent foreign '
                'date evidence validates diaspora interest but cannot establish forecast readiness')
    elif contaminated:
        note = (f'search is unusable: qualified share {share:.0%} is below the '
                f'{demand.CONTAMINATION_FLOOR:.0%} contamination floor')
    elif search_usable:
        note = 'usable search evidence; the representative geography is labelled, never summed'
    elif foreign_date_usable:
        note = ('qualifying future diaspora-market date validates diaspora interest; clean '
                'US/CA search evidence is still required for forecast readiness')
    else:
        note = 'no usable US/CA search or qualifying foreign-date evidence yet'
    return dict(us=per_geo['us'], ca=per_geo['ca'], representative=representative,
                contamination=dict(qualified_share=share, checked_at=contamination.get('checked_at'),
                                   contaminated=contaminated),
                international_status=international.get('status') or 'not_checked',
                qualifying_foreign_dates=foreign, search_usable=search_usable,
                foreign_date_usable=foreign_date_usable,
                diaspora_validated=diaspora_validated,
                forecast_ready=forecast_ready,
                # Backward-compatible summary: usable to validate the diaspora workflow stage.
                usable=diaspora_validated, note=note)


def _stage(primary, diaspora, identity_ready, primary_configured):
    if not primary:
        if not primary_configured:
            return 'Discovered', ('Configure an explicit primary-validation role before a booking '
                                  'can be confirmed.')
        return 'Discovered', 'Confirm a present or future booking on a configured primary platform.'
    if not diaspora['diaspora_validated']:
        return 'Major-platform confirmed', 'Review US and Canada evidence separately and check foreign dates.'
    if not identity_ready:
        return 'Diaspora validated', 'Resolve the canonical artist identity, then prepare the dossier handoff.'
    if not diaspora['forecast_ready']:
        return ('Diaspora validated', 'Obtain usable uncontaminated US or Canada search evidence before '
                'preparing the forecast handoff.')
    return ('Forecast ready', 'Prepare and approve the Artist Tour Engine dossier handoff; '
            'this is readiness only, not a ticket forecast.')


def build(ledger, demand_data=None, asof=None, sources_config=None):
    """Derive lifecycle records for candidate performers from a ledger without mutating it."""
    asof = _as_date(asof) or dt.date.today()
    demand_data = demand.load() if demand_data is None else demand_data
    source_map = primary_sources(sources_config)
    entities = {}
    for show in (ledger.get('shows') or []):
        for item in show.get('entities') or []:
            slug = item.get('slug')
            if not slug:
                continue
            entity = entities.setdefault(slug, dict(slug=slug, name=item.get('name'),
                                                    kind=item.get('kind') or show.get('entity_type'),
                                                    shows=[]))
            entity['shows'].append(show)

    out = []
    for entity in entities.values():
        if entity.get('kind') not in CANDIDATE_KINDS:
            continue
        primary, rejected = _primary_evidence(entity, source_map, asof)
        diaspora = _diaspora_evidence(entity['slug'], demand_data, asof)
        identity_ready, identity_note = _classifiable_identity(entity)
        stage, next_action = _stage(primary, diaspora, identity_ready, bool(source_map))
        out.append(dict(
            slug=entity['slug'], name=entity.get('name'), kind=entity.get('kind'),
            candidate_eligible=True, stage=stage, next_action=next_action,
            primary_confirmed=bool(primary), classifiable_identity=identity_ready,
            identity_note=identity_note,
            evidence=dict(primary=primary, rejected_primary=rejected,
                          discovery=_discovery_evidence(entity, source_map), diaspora=diaspora),
            trajectory_required=False,
            trajectory_note=('Lifecycle uses booking and diaspora evidence only; it makes no '
                             'trajectory or momentum claim before or after three crawls.'),
            forecast_note='Forecast ready means dossier-handoff ready, never ticket-forecasted.',
        ))
    return sorted(out, key=lambda row: (STAGES.index(row['stage']), (row['name'] or '').lower()))


def _selftest():
    primary_config = dict(sources=[
        dict(key='book', name='Book fixture', domain='book.example', role='primary_validation'),
        dict(key='district', name='District fixture', domain='district.example', primary=True),
        dict(key='longtail', name='Longtail fixture', domain='long.example', tier='longtail'),
    ])

    def show(sid, source, name, slug, date, url, kind='artist'):
        return dict(show_id=sid, source=source, title=name + ' Live', url=url, show_date=date,
                    city='Mumbai', venue='Fixture Hall', venue_band='club', role='headline',
                    entity_type=kind, entities=[dict(name=name, slug=slug, kind=kind)],
                    status_history=[dict(date='2026-08-20', status='available')],
                    first_seen='2026-08-20', last_seen='2026-08-20')

    ledger = dict(shows=[
        show('1', 'longtail', 'Discovery Act', 'discovery-act', '2026-08-30', 'https://long.example/1'),
        show('2', 'book', 'Primary Only', 'primary-only', '2026-08-30', 'https://book.example/2'),
        show('3', 'district', 'X', 'x', '2026-08-30', 'https://district.example/3'),
        show('4', 'district', 'Ready Artist', 'ready-artist', '2026-08-30', 'https://district.example/4'),
        show('5', 'district', 'Contaminated Name', 'contaminated-name', '2026-08-30',
             'https://district.example/5'),
        show('6', 'book', 'Past Booking', 'past-booking', '2026-08-19', 'https://book.example/6'),
        show('7', 'district', 'Foreign Only', 'foreign-only', '2026-08-30',
             'https://district.example/7'),
    ])
    dem = dict(entities={
        'x': dict(search={
            'us': dict(avg_monthly=800, fetched_at='2026-08-21', source='keyword planner', monthly=[]),
            'ca': dict(avg_monthly=1200, fetched_at='2026-08-21', source='keyword planner', monthly=[]),
        }),
        'ready-artist': dict(search={
            'us': dict(avg_monthly=1000, fetched_at='2026-08-21', source='keyword planner', monthly=[]),
            'ca': dict(avg_monthly=2000, fetched_at='2026-08-21', source='keyword planner', monthly=[]),
        }),
        'contaminated-name': dict(search={
            'us': dict(avg_monthly=5000, fetched_at='2026-08-21', source='keyword planner', monthly=[]),
            'ca': dict(avg_monthly=None, fetched_at='2026-08-21', source='keyword planner', monthly=[]),
        }, contamination=dict(qualified_share=0.02, checked_at='2026-08-21'),
           international=dict(status='found', dates=[dict(country='United Kingdom', city='London',
                                                         date='2026-09-30', source='serp')])),
        'foreign-only': dict(search={}, international=dict(status='found', dates=[
            dict(country='Canada', city='Toronto', date='2026-09-15', source='bandsintown')
        ])),
    })
    rows = {row['slug']: row for row in build(ledger, dem, '2026-08-21', primary_config)}
    ready = rows['ready-artist']
    checks = [
        ('long-tail listing never confirms a primary platform',
         rows['discovery-act']['stage'] == 'Discovered' and not rows['discovery-act']['primary_confirmed']),
        ('primary URL + future date establish confirmation',
         rows['primary-only']['stage'] == 'Major-platform confirmed'),
        ('US and Canada are separate and stronger geo is labelled',
         ready['evidence']['diaspora']['us']['avg_monthly'] == 1000 and
         ready['evidence']['diaspora']['ca']['avg_monthly'] == 2000 and
         ready['evidence']['diaspora']['representative']['geo'] == 'ca'),
        ('unclassifiable identity stops at diaspora validation', rows['x']['stage'] == 'Diaspora validated'),
        ('primary + clean export + identity becomes handoff ready', ready['stage'] == 'Forecast ready'),
        ('contaminated search does not invalidate an independent foreign date',
         rows['contaminated-name']['stage'] == 'Diaspora validated' and
         rows['contaminated-name']['evidence']['diaspora']['foreign_date_usable'] and
         not rows['contaminated-name']['evidence']['diaspora']['search_usable']),
        ('contaminated plus foreign evidence cannot become forecast ready',
         not rows['contaminated-name']['evidence']['diaspora']['forecast_ready'] and
         rows['contaminated-name']['stage'] != 'Forecast ready'),
        ('foreign-date-only evidence stops at diaspora validation',
         rows['foreign-only']['stage'] == 'Diaspora validated' and
         rows['foreign-only']['evidence']['diaspora']['foreign_date_usable'] and
         not rows['foreign-only']['evidence']['diaspora']['forecast_ready']),
        ('past primary listing is rejected', rows['past-booking']['stage'] == 'Discovered' and
         rows['past-booking']['evidence']['rejected_primary']),
        ('one-snapshot workflow makes no trajectory claim',
         ready['trajectory_required'] is False and 'trajectory' in ready['trajectory_note'].lower()),
        ('primary role comes from configuration, not source names',
         set(primary_sources(primary_config)) == {'book', 'district'}),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

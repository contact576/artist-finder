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
import gazetteer  # noqa: E402
import model  # noqa: E402

SOURCES_PATH = os.path.join(BASE, 'data', 'sources.json')
IDENTITY_REVIEWS_PATH = os.path.join(BASE, 'data', 'identity_reviews.json')
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


def load_identity_reviews(path=None):
    """Load small human-audited identity attestations; this is not an aliases file."""
    path = path or IDENTITY_REVIEWS_PATH
    if not os.path.exists(path):
        return dict(schema_version=1, reviews={})
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get('reviews'), dict):
        raise ValueError('identity reviews must contain a reviews object')
    return data


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
        role_certain=show.get('role_certain'), parse_confidence=show.get('parse_confidence'),
        parse_reason=show.get('parse_reason'), trusted=show.get('trusted'),
        identity_provenance=list(show.get('identity_provenance') or []),
        status_history=list(show.get('status_history') or []),
        first_seen=show.get('first_seen'), last_seen=show.get('last_seen'),
    )


def _identity_attestation(slug, name, reviews):
    """Require an exact established identity or one explicit human review."""
    if gazetteer.exact_known(name):
        return True, dict(kind='exact_gazetteer', name=name), None
    record = (reviews.get('reviews') or {}).get(slug)
    if not isinstance(record, dict):
        return False, None, 'no exact gazetteer identity or audited identity review for this performer'
    if record.get('status') != 'verified':
        return False, None, 'identity review is not marked verified'
    reviewed_at = _as_date(record.get('reviewed_at'))
    if reviewed_at is None:
        return False, None, 'identity review has no valid review date'
    review_name = record.get('name')
    if not isinstance(review_name, str) or review_name.casefold() != name.casefold():
        return False, None, 'identity review name does not exactly match the canonical performer'
    source_evidence = record.get('source_evidence')
    if not isinstance(source_evidence, str) or not source_evidence.strip():
        return False, None, 'identity review is missing an actionable source-evidence reason'
    return True, dict(kind='audited_review', name=review_name,
                      reviewed_at=reviewed_at.isoformat(), source_evidence=source_evidence), None


def _accepted_identity(show, slug, reviews):
    if show.get('trusted') is not True:
        return False, None, 'listing was held for review or lacks trusted parser provenance'
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
    attested, attestation, reason = _identity_attestation(slug, name, reviews)
    if not attested:
        return False, None, reason
    return True, dict(slug=slug, name=name, kind=kind,
                      parser_state='trusted ledger row; one canonical entity match',
                      attestation=attestation), None


def _primary_evidence(entity, source_map, asof, reviews):
    evidence, rejected = [], []
    for show in entity.get('shows') or []:
        source = source_map.get(show.get('source'))
        if source is None:
            continue
        date = _as_date(show.get('show_date'))
        accepted_identity, identity, identity_reason = _accepted_identity(show, entity['slug'], reviews)
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


def _classifiable_identity(entity, reviews):
    name, slug = entity.get('name'), entity.get('slug')
    if entity.get('kind') not in CANDIDATE_KINDS:
        return False, 'entity kind is not a performer/DJ-night candidate'
    if not isinstance(name, str) or len(name.strip()) < 3:
        return False, 'canonical artist name is missing or too short to review'
    if not isinstance(slug, str) or model.slugify(name) != slug:
        return False, 'canonical artist name and slug do not agree'
    attested, attestation, reason = _identity_attestation(slug, name, reviews)
    if not attested:
        return False, reason
    return True, 'identity attested by ' + attestation['kind']


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
    checked_at = _as_date(contamination.get('checked_at'))
    share_numeric = isinstance(share, (int, float)) and not isinstance(share, bool)
    contamination_reviewed = share_numeric and checked_at is not None
    contaminated = contamination_reviewed and share < demand.CONTAMINATION_FLOOR
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
    search_usable = bool(usable_geos) and contamination_reviewed and not contaminated
    foreign_date_usable = bool(foreign)
    diaspora_validated = search_usable or foreign_date_usable
    forecast_ready = search_usable
    if not contamination_reviewed and foreign_date_usable:
        note = ('search is unvalidated: a numeric qualified share at or above the contamination '
                'floor and a review timestamp are required; independent foreign-date evidence '
                'still validates diaspora interest')
    elif not contamination_reviewed:
        note = ('search is unvalidated: a numeric qualified share at or above the contamination '
                'floor and a review timestamp are required')
    elif contaminated and foreign_date_usable:
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


def build(ledger, demand_data=None, asof=None, sources_config=None, identity_reviews=None):
    """Derive lifecycle records for candidate performers from a ledger without mutating it."""
    asof = _as_date(asof) or dt.date.today()
    demand_data = demand.load() if demand_data is None else demand_data
    source_map = primary_sources(sources_config)
    reviews = load_identity_reviews() if identity_reviews is None else identity_reviews
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
        primary, rejected = _primary_evidence(entity, source_map, asof, reviews)
        diaspora = _diaspora_evidence(entity['slug'], demand_data, asof)
        identity_ready, identity_note = _classifiable_identity(entity, reviews)
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
        dict(key='district', name='District fixture', domain='district.example', primary=True),
        dict(key='longtail', name='Longtail fixture', domain='long.example', tier='longtail'),
    ])
    previous_cache = gazetteer._CACHE
    gazetteer._CACHE = dict(known={'Known Artist': ['known', 'artist']}, explicit={})

    def show(sid, source, name, slug, trusted=True):
        return dict(show_id=sid, source=source, title=name + ' Live',
                    url='https://' + source + '.example/' + sid, show_date='2026-08-30',
                    city='Mumbai', venue='Fixture Hall', venue_band='club', role='headline',
                    role_certain=True, parse_confidence=0.9, parse_reason='fixture explicit performer',
                    trusted=trusted, entity_type='artist',
                    entities=[dict(name=name, slug=slug, kind='artist')],
                    status_history=[], first_seen='2026-08-20', last_seen='2026-08-20')

    def review(name):
        return dict(name=name, status='verified', reviewed_at='2026-08-21',
                    source_evidence='fixture primary title explicitly names this performer')

    reviews = dict(schema_version=1, reviews={
        'reviewed-artist': review('Reviewed Artist'),
        'known-unchecked': review('Known Unchecked'),
        'foreign-only': review('Foreign Only'),
        'contaminated-name': review('Contaminated Name'),
        'untrusted-reviewed': review('Untrusted Reviewed'),
    })
    ledger = dict(shows=[
        show('1', 'longtail', 'Discovery Act', 'discovery-act'),
        show('2', 'district', 'Just Go', 'just-go'),
        show('3', 'district', 'Symphonic Experience', 'symphonic-experience'),
        show('4', 'district', 'Known Artist', 'known-artist'),
        show('5', 'district', 'Reviewed Artist', 'reviewed-artist'),
        show('6', 'district', 'Corrected But Unreviewed', 'corrected-but-unreviewed'),
        show('7', 'district', 'Untrusted Reviewed', 'untrusted-reviewed', trusted=False),
        show('8', 'district', 'Known Unchecked', 'known-unchecked'),
        show('9', 'district', 'Foreign Only', 'foreign-only'),
        show('10', 'district', 'Contaminated Name', 'contaminated-name'),
    ])
    def clean(us, ca):
        return dict(search={
            'us': dict(avg_monthly=us, fetched_at='2026-08-21', source='fixture'),
            'ca': dict(avg_monthly=ca, fetched_at='2026-08-21', source='fixture'),
        }, contamination=dict(qualified_share=0.8, checked_at='2026-08-21'))
    dem = dict(entities={
        'known-artist': clean(1000, 2000),
        'reviewed-artist': clean(900, 700),
        'known-unchecked': dict(search={'us': dict(avg_monthly=600, fetched_at='2026-08-21', source='fixture')}),
        'foreign-only': dict(international=dict(status='found', dates=[dict(
            country='Canada', city='Toronto', date='2026-09-15', source='fixture')])),
        'contaminated-name': dict(search={'us': dict(avg_monthly=5000, fetched_at='2026-08-21', source='fixture')},
            contamination=dict(qualified_share=0.02, checked_at='2026-08-21'),
            international=dict(status='found', dates=[dict(country='United Kingdom', city='London',
                date='2026-09-30', source='fixture')])),
    })
    rows = {row['slug']: row for row in build(ledger, dem, '2026-08-21', primary_config, reviews)}
    known = rows['known-artist']
    checks = [
        ('unchecked generic Just Go remains discovered with actionable primary rejection',
         rows['just-go']['stage'] == 'Discovered' and rows['just-go']['evidence']['rejected_primary']),
        ('unchecked Symphonic Experience remains discovered with actionable primary rejection',
         rows['symphonic-experience']['stage'] == 'Discovered' and rows['symphonic-experience']['evidence']['rejected_primary']),
        ('exact known identity plus reviewed clean search is forecast ready', known['stage'] == 'Forecast ready'),
        ('new explicit reviewed identity can be forecast ready', rows['reviewed-artist']['stage'] == 'Forecast ready'),
        ('ordinary canonicalisation without attestation cannot promote', rows['corrected-but-unreviewed']['stage'] == 'Discovered'),
        ('trusted false evidence cannot promote', rows['untrusted-reviewed']['stage'] == 'Discovered'),
        ('missing contamination review makes positive search unvalidated',
         rows['known-unchecked']['stage'] == 'Major-platform confirmed' and
         not rows['known-unchecked']['evidence']['diaspora']['search_usable']),
        ('foreign-only evidence independently validates diaspora but not forecast readiness',
         rows['foreign-only']['stage'] == 'Diaspora validated' and
         rows['foreign-only']['evidence']['diaspora']['foreign_date_usable'] and
         not rows['foreign-only']['evidence']['diaspora']['forecast_ready']),
        ('contaminated search does not invalidate independent foreign evidence',
         rows['contaminated-name']['stage'] == 'Diaspora validated' and
         not rows['contaminated-name']['evidence']['diaspora']['search_usable']),
        ('US and Canada remain separate and the stronger geography is labelled',
         known['evidence']['diaspora']['us']['avg_monthly'] == 1000 and
         known['evidence']['diaspora']['ca']['avg_monthly'] == 2000 and
         known['evidence']['diaspora']['representative']['geo'] == 'ca'),
    ]
    gazetteer._CACHE = previous_cache
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

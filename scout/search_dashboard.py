"""Read-only payload for the monthly Artist Search Intelligence dashboard."""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
import booking_eligibility
from typing import Any

import demand
import roster

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, 'out')
AUTOMATION_STATUS = os.path.join(OUT, 'automation', 'status.json')
GEO_LABELS = {'in': 'India', 'us': 'USA', 'ca': 'Canada'}
NETWORK = 'GOOGLE_SEARCH_AND_PARTNERS'
SECRET_WORDS = ('token', 'secret', 'password', 'authorization', 'cookie', 'api_key')


def _read(path: str, default: Any) -> Any:
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()
                if not any(word in str(key).lower() for word in SECRET_WORDS)}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _normal(value: Any) -> str:
    """Normalize user-visible query text for exact, case-insensitive matching."""
    return ' '.join(str(value or '').split()).casefold()

def _series(entity: dict, geo: str, measurement_keyword: str | None = None) -> list[dict]:
    current = ((entity.get('search') or {}).get(geo) or {}).get('monthly') or []
    history = [row for row in ((entity.get('history') or {}).get(geo) or [])
               if _normal(measurement_keyword) and _normal(row.get('keyword')) == _normal(measurement_keyword)]
    rows = current or history
    by_month = {}
    for row in rows:
        try:
            year, month = int(row['year']), int(row['month'])
            searches = int(row.get('searches') or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= month <= 12:
            by_month[(year, month)] = dict(month=f'{year:04d}-{month:02d}', searches=searches)
    return [by_month[key] for key in sorted(by_month)][-48:]


def _metrics(series: list[dict], selected_month: str | None = None) -> dict:
    if not series:
        return dict(latest=None, absolute_change=None, mom=None, average_3m=None,
                    average_6m=None, average_12m=None, yoy=None,
                    month=selected_month, availability='missing_month')
    by_month = {row['month']: row['searches'] for row in series}
    month = selected_month or series[-1]['month']
    if month not in by_month:
        return dict(latest=None, absolute_change=None, mom=None, average_3m=None,
                    average_6m=None, average_12m=None, yoy=None,
                    month=month, availability='missing_month')
    year, mon = map(int, month.split('-'))
    index = year * 12 + mon - 1

    def key(month_index: int) -> str:
        return f'{month_index // 12:04d}-{month_index % 12 + 1:02d}'

    def calendar_average(size: int) -> int | None:
        values = [by_month.get(key(index - offset)) for offset in range(size)]
        return round(statistics.mean(values)) if all(value is not None for value in values) else None

    prior = key(index - 1)
    year_prior = f'{year - 1:04d}-{mon:02d}'
    latest = by_month[month]
    previous = by_month.get(prior)
    absolute = latest - previous if previous is not None else None
    mom = (absolute / previous) if previous not in (None, 0) else None
    yoy_base = by_month.get(year_prior)
    yoy = ((latest - yoy_base) / yoy_base) if yoy_base not in (None, 0) else None
    return dict(latest=latest, absolute_change=absolute,
                mom=(round(mom, 6) if mom is not None else None),
                average_3m=calendar_average(3), average_6m=calendar_average(6),
                average_12m=calendar_average(12),
                yoy=(round(yoy, 6) if yoy is not None else None), month=month,
                availability=('explicit_zero' if latest == 0 else 'available'))


def _availability(search: dict, series: list[dict]) -> str:
    """Keep an API mapping, a usable series, an explicit zero, and no data distinct."""
    mode = search.get('mapping_mode')
    if series:
        return 'explicit_zero' if series[-1]['searches'] == 0 else 'usable_series'
    if mode == 'ambiguous':
        return 'ambiguous_mapping'
    if mode == 'missing':
        return 'missing_mapping'
    if mode:
        return 'mapped_empty'
    return 'not_measured'


def _geo_payload(entity: dict, geo: str, measurement_keyword: str | None = None) -> dict:
    search = (entity.get('search') or {}).get(geo) or {}
    stored_keyword = ' '.join(str(search.get('keyword') or '').split()) or None
    requested_keyword = ' '.join(str(measurement_keyword or '').split()) or None
    keyword_matches = bool(requested_keyword and stored_keyword and
                           _normal(requested_keyword) == _normal(stored_keyword))
    if not keyword_matches:
        summary = _metrics([])
        unavailable_state = 'keyword_changed' if stored_keyword else 'not_measured'
        unavailable_reason = ('Stored search query does not match the current measurement keyword; historical values are retained but held out.' if stored_keyword else 'No measurements for this query yet.')
        return dict(series=[], summary=summary, avg_monthly=None,
                    fetched_at=search.get('fetched_at'), last_updated=search.get('fetched_at'),
                    source=search.get('source'), keyword=stored_keyword,
                    requested_keyword=requested_keyword, keyword_matches=False,
                    network=search.get('network'), mapping_mode=search.get('mapping_mode'),
                    result_text=search.get('result_text'),
                    close_variants=list(search.get('close_variants') or []),
                    mapping_quality=dict(state=unavailable_state, mapping_mode=search.get('mapping_mode'),
                                         result_text=search.get('result_text'),
                                         close_variants=list(search.get('close_variants') or [])),
                    availability=unavailable_state, unavailable_reason=unavailable_reason,
                    language=search.get('language'), geo_target=search.get('geo_target'))
    series = _series(entity, geo, requested_keyword)
    availability = _availability(search, series)
    fetched_at = search.get('fetched_at')
    return dict(series=series, summary=_metrics(series), avg_monthly=search.get('avg_monthly'),
                fetched_at=fetched_at, last_updated=fetched_at, source=search.get('source'),
                keyword=stored_keyword, requested_keyword=requested_keyword, keyword_matches=True,
                network=search.get('network'), mapping_mode=search.get('mapping_mode'),
                result_text=search.get('result_text'),
                close_variants=list(search.get('close_variants') or []),
                mapping_quality=dict(state=availability, mapping_mode=search.get('mapping_mode'),
                                     result_text=search.get('result_text'),
                                     close_variants=list(search.get('close_variants') or [])),
                availability=availability, unavailable_reason=None, language=search.get('language'),
                geo_target=search.get('geo_target'))


def _artist_rows(registry: dict, demand_data: dict, reviews=None, asof=None) -> list[dict]:
    rows = []
    entities = demand_data.get('entities') or {}
    reviews = booking_eligibility.load_reviews() if reviews is None else reviews
    for slug, artist in sorted(registry.get('artists', {}).items(),
                               key=lambda item: str(item[1].get('name')).casefold()):
        entity = entities.get(slug) or {}
        eligibility = booking_eligibility.evaluate(slug, reviews, artist.get('measurement_keyword'),
                                                    entity, asof=asof)
        geos = {geo: _geo_payload(entity, geo, artist.get('measurement_keyword')) for geo in demand.SEARCH_GEOS}
        measured = [geo for geo, value in geos.items() if value['summary']['latest'] is not None]
        mapping_review = [geo for geo, value in geos.items()
                          if value.get('mapping_mode') == 'close_variant']
        attention = []
        if artist.get('status') == 'candidate':
            attention.append('Identity and genre evidence require review.')
        if artist.get('keyword_review_state') != 'approved':
            attention.append('Measurement keyword is not approved.')
        if mapping_review:
            attention.append('Close-variant mapping needs review: ' + ', '.join(mapping_review).upper())
        rows.append(dict(
            slug=slug, name=artist.get('name') or slug,
            aliases=sorted(set((artist.get('aliases') or []) + (entity.get('aliases') or []))),
            primary_genre=artist.get('primary_genre') or 'unknown',
            niche_tags=list(artist.get('niche_tags') or []), status=artist.get('status'),
            research_state=artist.get('research_state'), curated=bool(artist.get('curated')),
            booking_eligibility=eligibility,
            summary_eligible=bool(artist.get('status') == 'verified' and eligibility['summary_eligible']),
            summary_eligible_geos=dict(eligibility['summary_eligible_geos']),
            subcategory=artist.get('subcategory'), entity_type=artist.get('entity_type'),
            languages=list(artist.get('languages') or []),
            regions=list(artist.get('regions') or []),
            measurement_keyword=artist.get('measurement_keyword'),
            keyword_review_state=artist.get('keyword_review_state'),
            contamination_status=artist.get('contamination_status'),
            contamination_note=artist.get('contamination_note'),
            contamination=dict(status=artist.get('contamination_status'),
                               note=artist.get('contamination_note'),
                               qualified_share=(entity.get('contamination') or {}).get('qualified_share'),
                               checked_at=(entity.get('contamination') or {}).get('checked_at')),
            identity_evidence=artist.get('identity_evidence') or {},
            first_seen=artist.get('first_seen'), verified_at=artist.get('verified_at'),
            last_reviewed=artist.get('last_reviewed'), measured_geos=measured,
            needs_attention=attention, geos=geos))
    return rows


def _months(artists: list[dict]) -> list[str]:
    values = {item['month'] for artist in artists for geo in artist['geos'].values()
              for item in geo['series']}
    return sorted(values, reverse=True)


def _latest_month_by_geo(artists: list[dict]) -> dict:
    return {geo: max((item['month'] for artist in artists
                      for item in artist['geos'][geo]['series']), default=None)
            for geo in demand.SEARCH_GEOS}


def _default_month(artists: list[dict]) -> str | None:
    trusted = [artist for artist in artists if artist.get('status') == 'verified'
               and artist.get('keyword_review_state') == 'approved']
    source = trusted or artists
    per_geo = [{item['month'] for artist in source
                for item in artist['geos'][geo]['series']}
               for geo in demand.SEARCH_GEOS]
    common = set.intersection(*per_geo) if all(per_geo) else set()
    if common:
        return max(common)
    months = _months(source)
    return months[0] if months else None


def _candidate_rows(inbox: dict) -> list[dict]:
    rows = list((inbox.get('candidates') or {}).values())
    rows.sort(key=lambda row: (row.get('state') != 'needs_review',
                               -(row.get('avg_monthly_searches') or 0),
                               str(row.get('text')).casefold()))
    return rows


def _data_health(demand_data: dict, artists: list[dict], registry: dict,
                 candidates: dict) -> dict:
    runs = list(demand_data.get('runs') or [])
    latest_run = runs[-1] if runs else None
    states = ('usable_series', 'explicit_zero', 'mapped_empty', 'missing_mapping',
              'ambiguous_mapping', 'keyword_changed', 'not_measured')
    availability = {state: {geo: 0 for geo in demand.SEARCH_GEOS} for state in states}
    api_mapped = {geo: 0 for geo in demand.SEARCH_GEOS}
    monthly_series = {geo: 0 for geo in demand.SEARCH_GEOS}
    for row in artists:
        for geo in demand.SEARCH_GEOS:
            item = row['geos'][geo]
            state = item['availability']
            availability[state][geo] += 1
            if item.get('mapping_mode') in ('exact', 'close_variant'):
                api_mapped[geo] += 1
            if item['series']:
                monthly_series[geo] += 1
    networks = sorted({row['geos'][geo].get('network') for row in artists
                       for geo in demand.SEARCH_GEOS if row['geos'][geo].get('network')})
    research_scope = [row for row in artists
                      if row.get('status') == 'verified'
                      or (row.get('status') == 'candidate'
                          and (row.get('research_state') == 'operator_candidate'
                               or (row.get('curated') is True
                                   and row.get('research_state') == 'directory_candidate')))]
    scoped_api_mapped = {geo: sum(
        row['geos'][geo].get('mapping_mode') in ('exact', 'close_variant')
        for row in research_scope) for geo in demand.SEARCH_GEOS}
    scoped_monthly_series = {geo: sum(bool(row['geos'][geo]['series'])
                                      for row in research_scope)
                             for geo in demand.SEARCH_GEOS}
    scoped_selected_month = _default_month(artists)
    scoped_selected_available = {geo: sum(
        any(item['month'] == scoped_selected_month for item in row['geos'][geo]['series'])
        for row in research_scope) for geo in demand.SEARCH_GEOS}
    return dict(
        expected_network=NETWORK, observed_networks=networks,
        network_state=('verified' if networks == [NETWORK] else 'refresh_required'),
        network_note=('All measured records carry the explicit Search + Partners setting.'
                      if networks == [NETWORK] else
                      'Existing retained data predates explicit Search + Partners metadata. '
                      'Run the monthly refresh before comparing new MoM values.'),
        # Retain measured_by_geo for old consumers, but make every availability denominator explicit.
        measured_by_geo=monthly_series, api_mapped_by_geo=api_mapped,
        monthly_series_by_geo=monthly_series,
        research_scope_total=len(research_scope),
        research_scope_api_mapped_by_geo=scoped_api_mapped,
        research_scope_monthly_series_by_geo=scoped_monthly_series,
        research_scope_selected_month=scoped_selected_month,
        research_scope_selected_month_available_by_geo=scoped_selected_available,
        usable_nonzero_by_geo=availability['usable_series'],
        explicit_zero_by_geo=availability['explicit_zero'],
        mapped_empty_by_geo=availability['mapped_empty'],
        missing_mapping_by_geo=availability['missing_mapping'],
        ambiguous_mapping_by_geo=availability['ambiguous_mapping'],
        keyword_changed_by_geo=availability['keyword_changed'],
        not_measured_by_geo=availability['not_measured'],
        availability_by_geo=availability,
        roster_total=len(registry.get('artists') or {}),
        latest_month_by_geo=_latest_month_by_geo(artists),
        verified=sum(1 for row in artists if row['status'] == 'verified'),
        roster_candidates=sum(1 for row in artists if row['status'] == 'candidate'),
        researched_candidates=sum(1 for row in artists
                                  if row.get('status') == 'candidate'
                                  and row.get('research_state') == 'directory_candidate'
                                  and row.get('curated')),
        legacy_unreviewed=sum(1 for row in artists
                             if row.get('research_state') == 'legacy_unreviewed'),
        idea_review_backlog=sum(1 for row in (candidates.get('candidates') or {}).values()
                                if row.get('state') == 'needs_review'),
        latest_run=_safe(latest_run), run_count=len(runs))

def _operations() -> dict:
    data = _safe(_read(AUTOMATION_STATUS, {}))
    return dict(monthly=data.get('monthly') if isinstance(data, dict) else None,
                status_path='out/automation/status.json')


def build_payload(registry=None, demand_data=None, inbox=None) -> dict:
    registry = roster.load() if registry is None else registry
    demand_data = demand.load() if demand_data is None else demand_data
    inbox = roster.load_candidates() if inbox is None else inbox
    artists = _artist_rows(registry, demand_data)
    months = _months(artists)
    return dict(
        schema_version=2, mode='live', product='Artist Search Intelligence',
        generated_at=dt.datetime.now().astimezone().isoformat(timespec='seconds'),
        geographies=[dict(id=geo, name=GEO_LABELS[geo]) for geo in demand.SEARCH_GEOS],
        months=months, default_month=_default_month(artists),
        network=NETWORK, network_label='Google Search + Search partners',
        network_note=('Keyword Planner returns one combined Google Search + Search partners '
                      'estimate. It is not a count of all YouTube views or all Google activity.'),
        artists=artists, niches=roster.load_niches(),
        candidates=_candidate_rows(inbox), candidate_runs=list(inbox.get('discovery_runs') or []),
        data_health=_data_health(demand_data, artists, registry, inbox),
        operations=_operations(),
        caveat=('Google Ads Keyword Planner Search + Partners monthly searches are approximate '
                'estimates, can consolidate close variants, and are not unique people, ticket '
                'sales, or a ticket forecast. India, USA, and Canada are never summed.'))


def fixture_payload() -> dict:
    def months(values):
        return [dict(month=f'2026-{index + 1:02d}', searches=value)
                for index, value in enumerate(values)]

    def geo(values, keyword, mapping='exact'):
        series = months(values)
        availability = 'explicit_zero' if series and series[-1]['searches'] == 0 else 'usable_series'
        return dict(series=series, summary=_metrics(series), avg_monthly=round(statistics.mean(values)),
                    fetched_at='2026-08-21', last_updated='2026-08-21', source='fixture only', keyword=keyword,
                    network=NETWORK, mapping_mode=mapping, result_text=keyword,
                    close_variants=[], mapping_quality=dict(state=availability, mapping_mode=mapping,
                    result_text=keyword, close_variants=[]), availability=availability,
                    language='1000', geo_target='fixture')

    def artist(slug, name, genre, status, india, usa, canada, keyword=None):
        keyword = keyword or name
        return dict(slug=slug, name=name, aliases=[], primary_genre=genre, niche_tags=[],
                    status=status, measurement_keyword=keyword,
                    keyword_review_state=('approved' if status == 'verified' else 'pending'),
                    contamination_status='unchecked', contamination_note=None,
                    identity_evidence=dict(kind='fixture', reviewed_at='2026-08-21',
                                           note='Fixture identity', url='https://example.com'),
                    first_seen='2026-01-01', verified_at=('2026-01-01' if status == 'verified' else None),
                    last_reviewed='2026-08-21', measured_geos=['in', 'us', 'ca'],
                    needs_attention=([] if status == 'verified' else ['Identity review required.']),
                    geos={'in': geo(india, keyword), 'us': geo(usa, keyword),
                          'ca': geo(canada, keyword)})

    artists = [
        artist('rising-comic', 'Rising Comic', 'comedy', 'verified',
               [100, 120, 150, 180, 220, 300, 420, 600],
               [30, 40, 50, 60, 80, 120, 180, 300],
               [10, 10, 20, 20, 30, 50, 70, 100]),
        artist('steady-singer', 'Steady Singer', 'music_mainstream', 'verified',
               [1000, 1000, 1100, 1000, 1000, 1100, 1000, 1000],
               [500, 500, 500, 500, 500, 500, 500, 500],
               [200, 200, 200, 200, 200, 200, 200, 200]),
        artist('bhajan-newcomer', 'Bhajan Newcomer', 'devotional', 'candidate',
               [20, 30, 40, 60, 100, 160, 250, 400], [0] * 8, [0] * 8),
    ]
    inbox = dict(candidates={'bhajan-jamming': dict(candidate_id='bhajan-jamming',
        text='Bhajan Jamming', niche_id='bhajan_jamming', state='needs_review',
        avg_monthly_searches=1200, discovered_at='2026-08-21', last_seen='2026-08-21',
        source='google_ads_keyword_ideas', review_note=None, roster_slug=None)})
    return dict(schema_version=2, mode='fixture', product='Artist Search Intelligence',
                generated_at='2026-08-21T12:00:00+05:30',
                geographies=[dict(id=geo, name=GEO_LABELS[geo]) for geo in demand.SEARCH_GEOS],
                months=[f'2026-{month:02d}' for month in range(8, 0, -1)],
                default_month='2026-08', network=NETWORK, artists=artists,
                niches=roster.load_niches(), candidates=_candidate_rows(inbox), candidate_runs=[],
                data_health=dict(expected_network=NETWORK, observed_networks=[NETWORK],
                    network_state='verified', network_note='Fixture network verified.',
                    measured_by_geo={'in': 3, 'us': 3, 'ca': 3}, roster_total=3, verified=2,
                    roster_candidates=1, idea_review_backlog=1, latest_run=None, run_count=1),
                operations=dict(monthly=None, status_path='fixture'),
                caveat='Fixture only. Search estimates are not ticket sales; geographies are separate.')


def _selftest() -> int:
    payload = fixture_payload()
    rising = payload['artists'][0]
    india = rising['geos']['in']['summary']
    wrong_query = _geo_payload(dict(search={'in': dict(keyword='Generic Phrase', avg_monthly=999,
        monthly=[dict(year=2026, month=8, searches=999)], mapping_mode='exact')},
        history={'in': [dict(year=2026, month=7, searches=888, keyword='Generic Phrase')]}),
        'in', 'Exact Performer')
    unlabeled_history = _series(dict(history={'in': [dict(year=2026, month=7, searches=888)]}),
                                 'in', 'Exact Performer')
    checks = [
        ('three geographies are separate',
         [row['id'] for row in payload['geographies']] == ['in', 'us', 'ca']),
        ('fixture MoM uses consecutive months', india['mom'] == round((600 - 420) / 420, 6)),
        ('absolute change is retained beside percentage', india['absolute_change'] == 180),
        ('six-month average uses six consecutive calendar months', india['average_6m'] == 312),
        ('missing selected month is unavailable rather than a fallback zero',
         _metrics(rising['geos']['in']['series'], '2027-01')['latest'] is None
         and _metrics(rising['geos']['in']['series'], '2027-01')['availability'] == 'missing_month'),
        ('one approved keyword is explicit', rising['measurement_keyword'] == 'Rising Comic'),
        ('candidate remains outside verified status', payload['artists'][2]['status'] == 'candidate'),
        ('network is explicit', payload['network'] == NETWORK),
        ('explicit zero is not treated as empty',
         payload['artists'][2]['geos']['us']['availability'] == 'explicit_zero'),
        ('mapping metadata exposes close variants and freshness',
         'mapping_quality' in rising['geos']['in'] and 'last_updated' in rising['geos']['in']),
        ('no combined North America field exists',
         all('north_america' not in row and 'na' not in row for row in payload['artists'])),
        ('changed keyword cannot display prior generic query values',
         wrong_query['availability'] == 'keyword_changed' and wrong_query['series'] == [] and
         wrong_query['summary']['latest'] is None and wrong_query['avg_monthly'] is None),
        ('unlabelled historical query rows are held out', unlabeled_history == []),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

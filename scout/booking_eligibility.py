"""Evidence-only booking eligibility and search-reliability gates.

This is deliberately separate from the roster's identity status and from search
measurement.  A verified identity can still need a current booking review, and
an artist with a clean booking record can still have an ambiguous search term.
Nothing in this module writes reviews or rewrites historical measurements.

``data/artist_reviews.json`` is a small, human-maintained sidecar.  The
expected shape is ``{"reviews": {"artist-slug": review}}`` (a direct slug map
is also accepted for fixture injection).  A review should contain:

* ``reviewed_at`` and ``identity_confirmed``;
* ``life_status`` (for example ``alive``, ``deceased`` or ``nonperformer``);
* dated event rows: ``date``, ``country``, ``city``, ``url`` and
  ``evidence_kind`` (``performed``, ``announced`` or ``cancelled``);
* ``search_quality`` (``clean``, ``qualified``, ``ambiguous`` or ``unchecked``)
  plus ``search_keyword`` naming the exact reviewed query.

The review's query is intentionally compared with the roster's current
``measurement_keyword``.  Replacing a generic query therefore cannot make its
old search history eligible for a new, qualified query.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEWS_PATH = os.path.join(BASE, 'data', 'artist_reviews.json')
REVIEW_MAX_AGE_DAYS = 365
BOOKING_STATUSES = ('eligible', 'review', 'excluded', 'exception')
SEARCH_QUALITIES = ('clean', 'qualified', 'ambiguous', 'unchecked')
EXCEPTION_SLUGS = frozenset(('atif-aslam', 'rahat-fateh-ali-khan'))
INDIA_NAMES = frozenset(('india', 'in', 'ind'))
EXCLUDED_LIFE_STATUSES = frozenset((
    'deceased', 'dead', 'nonperformer', 'non-performer', 'not_a_performer',
    'not-a-performer', 'unsuitable', 'retired',
))


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _today(value: dt.date | str | None = None) -> dt.date:
    return _as_date(value) or dt.date.today()


def _three_year_cutoff(asof: dt.date) -> dt.date:
    """The inclusive rolling calendar-three-year boundary, leap-day safe."""
    try:
        return asof.replace(year=asof.year - 3)
    except ValueError:  # 29 February -> 28 February in the earlier non-leap year
        return asof.replace(year=asof.year - 3, day=28)


def _normal(value: Any) -> str:
    return ' '.join(str(value or '').split()).casefold()


def _reviews_map(data: Any) -> dict:
    if not isinstance(data, dict):
        return {}
    reviews = data.get('reviews')
    if isinstance(reviews, dict):
        return reviews
    # Fixture injection can pass the map directly.  A direct map is never
    # written by this module, so production remains one explicit schema.
    return data


def load_reviews(path: str | None = None) -> dict:
    try:
        with open(path or REVIEWS_PATH, encoding='utf-8') as handle:
            return _reviews_map(json.load(handle))
    except (OSError, ValueError):
        return {}


def _review_for(slug: str, reviews: Any) -> dict:
    value = _reviews_map(reviews).get(slug)
    return value if isinstance(value, dict) else {}


def _event_rows(review: dict) -> list[dict]:
    rows = review.get('events') or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _is_india(event: dict) -> bool:
    return _normal(event.get('country')) in INDIA_NAMES


def _performed_in_window(event: dict, asof: dt.date, country=None) -> bool:
    # Month-only/year-only event records are useful research leads, never evidence for a
    # precise rolling-window gate.  We do not invent a day from an imprecise source.
    if _normal(event.get('date_precision')) in ('month', 'year', 'unknown', 'approximate'):
        return False
    when = _as_date(event.get('date'))
    url = str(event.get('url') or '').strip()
    if (_normal(event.get('evidence_kind')) != 'performed' or when is None or
            not url.startswith(('https://', 'http://'))):
        return False
    country_name = _normal(event.get('country'))
    if country is True and not _is_india(event):
        return False
    if country is False and (not country_name or _is_india(event)):
        return False
    # A row labelled performed cannot qualify before it could have happened.
    return _three_year_cutoff(asof) <= when <= asof


def _event_key(event: dict) -> tuple:
    name = _normal(event.get('name') or event.get('event_name'))
    identity = (_normal(event.get('date')), _normal(event.get('country')),
                _normal(event.get('city')), name)
    if any(identity):
        return ('event',) + identity
    return ('url', _normal(event.get('url')))


def _distinct_events(events: list[dict]) -> list[dict]:
    seen, out = set(), []
    for event in events:
        key = _event_key(event)
        if key not in seen:
            seen.add(key)
            out.append(event)
    return out


def _review_state(review: dict, asof: dt.date) -> tuple[bool, str | None]:
    if review.get('identity_confirmed') is not True:
        return False, 'Identity is not confirmed in the booking review.'
    reviewed_at = _as_date(review.get('reviewed_at'))
    if reviewed_at is None:
        return False, 'Booking review has no valid review date.'
    if reviewed_at > asof:
        return False, 'Booking review date is in the future.'
    if (asof - reviewed_at).days > REVIEW_MAX_AGE_DAYS:
        return False, f'Booking review expired after {REVIEW_MAX_AGE_DAYS} days; re-review required.'
    return True, None


def _life_exclusion(review: dict) -> str | None:
    life_status = _normal(review.get('life_status'))
    reason = str(review.get('excluded_reason') or review.get('life_evidence') or '').strip()
    sources = review.get('sources') or []
    sourced = isinstance(sources, list) and any(isinstance(item, dict) and item.get('url') for item in sources)
    # A sourced life-status review is enough for a terminal exclusion; it does not need
    # the separate identity-confirmed flag used for positive booking eligibility.
    if life_status in EXCLUDED_LIFE_STATUSES and (reason or sourced):
        return reason or f'Sourced review confirms unsuitable for booking: {life_status}.'
    return None


def _brief_event(event: dict) -> dict:
    """Return only review evidence fields that can safely reach the dashboard."""
    return {key: event.get(key) for key in ('name', 'event_name', 'date', 'date_precision', 'country', 'city', 'url', 'evidence_kind')
            if event.get(key) not in (None, '')}


def _reviewed_keyword(review: dict) -> str | None:
    for key in ('search_keyword', 'measurement_keyword', 'keyword'):
        value = review.get(key)
        if isinstance(value, str) and value.strip():
            return ' '.join(value.split())
    return None


def search_reliability(review: dict | None, measurement_keyword: str | None,
                       entity: dict | None = None, asof: dt.date | str | None = None) -> dict:
    """Return a query-specific search-quality gate, keeping USA and Canada separate."""
    review = review if isinstance(review, dict) else {}
    asof_date = _today(asof)
    requested = ' '.join(str(measurement_keyword or '').split()) or None
    raw_quality = _normal(review.get('search_quality')) or 'unchecked'
    quality = raw_quality if raw_quality in SEARCH_QUALITIES else 'unchecked'
    review_ok, review_reason = _review_state(review, asof_date)
    reviewed_keyword = _reviewed_keyword(review)
    keyword_matches = bool(requested and reviewed_keyword and
                           _normal(requested) == _normal(reviewed_keyword))
    note = str(review.get('search_note') or '').strip()

    if quality not in ('clean', 'qualified'):
        valid_quality = False
        reason = note or ('Search query is ambiguous and metrics are held out.'
                          if quality == 'ambiguous' else
                          'Search query has not been quality-reviewed.')
    elif not review_ok:
        valid_quality = False
        reason = review_reason or 'Search quality review requires a current identity review.'
    elif not keyword_matches:
        valid_quality = False
        reason = ('Search-quality review does not name the current measurement keyword; '
                  'historical query data remains archived but is held out.')
    else:
        valid_quality = True
        reason = note or 'Search query is quality-reviewed for the current measurement keyword.'

    per_geo = {}
    search = (entity or {}).get('search') or {}
    for geo in ('in', 'us', 'ca'):
        row = search.get(geo) or {}
        actual_keyword = ' '.join(str(row.get('keyword') or '').split()) or None
        mode = row.get('mapping_mode')
        exact = mode == 'exact' and actual_keyword and requested and _normal(actual_keyword) == _normal(requested)
        qualified_close = (quality == 'qualified' and mode == 'close_variant' and actual_keyword and
                           requested and _normal(actual_keyword) == _normal(requested))
        per_geo[geo] = bool(valid_quality and (exact or qualified_close))
    return dict(search_quality=quality, reviewed_keyword=reviewed_keyword,
                current_keyword=requested, keyword_matches=keyword_matches,
                valid_quality=valid_quality, valid_mapping_by_geo=per_geo,
                note=reason)


def evaluate(slug: str, reviews: Any = None, measurement_keyword: str | None = None,
             entity: dict | None = None, asof: dt.date | str | None = None) -> dict:
    """Evaluate one roster row without changing the review, roster, or search history."""
    asof_date = _today(asof)
    review = _review_for(slug, load_reviews() if reviews is None else reviews)
    events = _event_rows(review)
    evidence = [_brief_event(event) for event in events]
    india = _distinct_events([event for event in events if _performed_in_window(event, asof_date, True)])
    foreign = _distinct_events([event for event in events if _performed_in_window(event, asof_date, False)])
    india.sort(key=lambda event: _as_date(event.get('date')) or dt.date.min, reverse=True)
    search = search_reliability(review, measurement_keyword, entity, asof_date)
    excluded_reason = _life_exclusion(review)
    review_ok, review_reason = _review_state(review, asof_date)
    exception_reason = str(review.get('exception_reason') or '').strip()

    if excluded_reason:
        status, reason = 'excluded', excluded_reason
    elif not review:
        status, reason = 'review', 'No booking review exists; absence of evidence is not evidence of no shows.'
    elif not review_ok:
        status, reason = 'review', review_reason
    elif slug in EXCEPTION_SLUGS and exception_reason and foreign:
        status, reason = 'exception', exception_reason
    elif india:
        status, reason = 'eligible', 'Verified performed India event within the rolling three-year window.'
    elif slug in EXCEPTION_SLUGS and exception_reason:
        status, reason = 'review', 'Named exception needs a dated foreign event marked performed.'
    else:
        status, reason = 'review', ('No qualifying dated India performed event is recorded; '
                                    'this is an evidence gap, not a claim that no show occurred.')

    return dict(status=status, reason=reason,
                last_india_show=(india[0].get('date') if india else None),
                recent_event_count=len(india), search_quality=search['search_quality'],
                note=search['note'], evidence=evidence,
                search=search,
                review_date=review.get('reviewed_at'),
                summary_eligible=(status in ('eligible', 'exception') and search['valid_quality'] and
                                  any(search['valid_mapping_by_geo'].values())),
                summary_eligible_geos=search['valid_mapping_by_geo'])


def _selftest() -> int:
    asof = '2026-09-08'
    reviews = {'reviews': {
        'eligible': dict(identity_confirmed=True, life_status='alive', reviewed_at='2026-09-01',
                         search_quality='clean', search_keyword='Eligible Artist',
                         events=[dict(date='2024-01-10', country='India', city='Delhi',
                                      url='https://example.com/india', evidence_kind='performed')]),
        'announced': dict(identity_confirmed=True, life_status='alive', reviewed_at='2026-09-01',
                          search_quality='clean', search_keyword='Announced Artist',
                          events=[dict(date='2026-10-01', country='India', evidence_kind='announced')]),
        'deceased': dict(identity_confirmed=True, life_status='deceased', reviewed_at='2021-01-01',
                         excluded_reason='Confirmed deceased.', search_quality='unchecked', events=[]),
        'atif-aslam': dict(identity_confirmed=True, life_status='alive', reviewed_at='2026-09-01',
                          exception_reason='Named exception: current foreign performed evidence.',
                          search_quality='qualified', search_keyword='Atif Aslam',
                          events=[dict(date='2025-02-01', country='United Kingdom', city='London',
                                       url='https://example.com/london', evidence_kind='performed')]),
        'old-review': dict(identity_confirmed=True, life_status='alive', reviewed_at='2025-01-01',
                           search_quality='clean', search_keyword='Old Review',
                           events=[dict(date='2025-08-01', country='India', evidence_kind='performed')]),
    }}
    entity = dict(search={geo: dict(keyword='Eligible Artist', mapping_mode='exact')
                          for geo in ('in', 'us', 'ca')})
    eligible = evaluate('eligible', reviews, 'Eligible Artist', entity, asof)
    announced = evaluate('announced', reviews, 'Announced Artist', {}, asof)
    deceased = evaluate('deceased', reviews, 'Deceased', {}, asof)
    exception = evaluate('atif-aslam', reviews, 'Atif Aslam',
                         dict(search={'us': dict(keyword='Atif Aslam', mapping_mode='close_variant')}), asof)
    old = evaluate('old-review', reviews, 'Old Review', {}, asof)
    replacement = evaluate('eligible', reviews, 'Eligible Artist Tickets', entity, asof)
    missing = evaluate('missing', reviews, 'Missing', {}, asof)
    checks = [
        ('dated India performed event qualifies', eligible['status'] == 'eligible' and eligible['recent_event_count'] == 1),
        ('future announced event never qualifies', announced['status'] == 'review'),
        ('confirmed deceased is excluded even if review is old', deceased['status'] == 'excluded'),
        ('named exception needs foreign performed evidence', exception['status'] == 'exception'),
        ('expired review automatically returns to review', old['status'] == 'review'),
        ('replacement query cannot reuse old query series', not replacement['summary_eligible']),
        ('missing evidence is an honest review state', missing['status'] == 'review' and 'absence of evidence' in missing['reason']),
        ('qualified close variant is explicit and geo-specific', exception['summary_eligible_geos']['us'] and not exception['summary_eligible_geos']['ca']),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

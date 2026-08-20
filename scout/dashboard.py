"""Private, static dashboard payload for Artist Finder.

This module is deliberately a read-only projection of the scout's evidence.  It
does not crawl, score, write snapshots, write dossiers, or read credentials.
``tools/build_dashboard.py`` serialises its payload into a local static site.

The dashboard keeps India-side stature/momentum and diaspora-side export_signal
as independent fields.  It never emits a combined score or a ticket forecast.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
DATA = os.path.join(BASE, 'data')
OUT = os.path.join(BASE, 'out')

if HERE not in sys.path:
    sys.path.insert(0, HERE)


STAGES = ('Discovered', 'Major-platform confirmed', 'Diaspora validated', 'Forecast ready')
STAGE_ORDER = {stage: index for index, stage in enumerate(STAGES)}
SECRET_WORDS = ('token', 'secret', 'password', 'authorization', 'cookie', 'refresh', 'api_key')


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _date(value: Any) -> dt.date | None:
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


def _days_old(value: Any, today: dt.date) -> int | None:
    parsed = _date(value)
    return max(0, (today - parsed).days) if parsed else None


def _relative(path: str) -> str:
    try:
        return os.path.relpath(path, BASE).replace('\\', '/')
    except ValueError:
        return os.path.basename(path)


def _safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if re.match(r'^https?://', value, re.IGNORECASE) else None


def _safe(value: Any) -> Any:
    """Drop secret-shaped fields before an object reaches a generated artifact."""
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()
                if not any(word in str(key).lower() for word in SECRET_WORDS)}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _metric(value: Any = None, observable: bool = False, note: str | None = None,
            coverage: Any = None, parts: list[dict] | None = None) -> dict:
    return dict(value=value, observable=bool(observable), note=note or 'Unknown',
                coverage=coverage, parts=list(parts or []))


def _status_of(show: dict) -> Any:
    history = show.get('status_history') or []
    latest = history[-1] if history and isinstance(history[-1], dict) else {}
    return latest.get('status') or show.get('status')


def _snapshot_reviews(snapshot_module: Any, dates: list[str]) -> list[dict]:
    """Extract review rows permissively; snapshots remain untouched."""
    rows: list[dict] = []
    for date in dates:
        try:
            if snapshot_module is None:
                continue
            snap = snapshot_module.load_snapshot(date)
        except (OSError, ValueError):
            continue
        if not isinstance(snap, dict):
            continue
        buckets = [(None, snap.get(key) or []) for key in ('review', 'reviews', 'unparseable', 'unparsed')]
        sources = snap.get('sources') if isinstance(snap.get('sources'), dict) else {}
        buckets.extend((str(source), value.get('review') or []) for source, value in sources.items()
                       if isinstance(value, dict))
        for source_key, items in buckets:
            for item in items:
                if not isinstance(item, dict):
                    continue
                entities = item.get('entities') or item.get('artists') or []
                slugs = [entry.get('slug') for entry in entities if isinstance(entry, dict)
                         and entry.get('slug')]
                rows.append(dict(snapshot_date=date, show_id=item.get('show_id'),
                                 source=item.get('source') or source_key, title=item.get('title'),
                                 url=_safe_url(item.get('url')), show_date=item.get('show_date') or item.get('date'),
                                 city=item.get('city'), confidence=(item.get('parse_confidence')
                                 if item.get('parse_confidence') is not None else item.get('confidence')),
                                 reason=item.get('parse_reason') or item.get('reason') or item.get('review_reason') or
                                 'Parsing review required', slugs=slugs))
    return rows


def _correction_notes(corrections: dict, show_id: str | None, slug: str) -> list[str]:
    notes = []
    event = ((corrections.get('events') or {}).get(show_id) if show_id else None) or {}
    entity = ((corrections.get('entities') or {}).get(slug) or {})
    for item in (event, entity):
        if item.get('reason'):
            notes.append(str(item['reason']))
    return notes


def _unresolved_identity_rows(shows: list[dict]) -> list[dict]:
    """Expose accepted evidence that cannot safely become an artist record yet.

    A blank/missing slug must stay out of lifecycle and score joins. Showing a
    compact, traceable review row is intentionally different from inventing a
    slug or treating the signal as zero.
    """
    rows = []
    for show in shows:
        for entity in show.get('entities') or []:
            if not isinstance(entity, dict) or entity.get('slug'):
                continue
            rows.append(dict(show_id=show.get('show_id'), name=entity.get('name') or 'Unnamed entity',
                             kind=entity.get('kind') or show.get('entity_type') or 'unknown',
                             source=show.get('source'), title=show.get('title'), url=_safe_url(show.get('url')),
                             show_date=show.get('show_date'), city=show.get('city'),
                             reason=('Missing canonical slug: held out of lifecycle and scoring until '
                                     'identity review resolves it.')))
    return rows


def _source_rows(config: Any) -> list[dict]:
    rows = config.get('sources') if isinstance(config, dict) else config
    return [row for row in (rows or []) if isinstance(row, dict) and row.get('key')]


def _source_health(source_rows: list[dict], shows: list[dict], reviews: list[dict],
                   lifecycle_module: Any, today: dt.date, probe: dict,
                   snapshot_module: Any = None, snapshot_dates: list[str] | None = None) -> list[dict]:
    by_source: dict[str, list[dict]] = defaultdict(list)
    for show in shows:
        by_source[str(show.get('source') or '')].append(show)
    reviewed = Counter(str(row.get('source') or '') for row in reviews)
    primary = lifecycle_module.primary_sources(dict(sources=source_rows))
    probe_rows = probe.get('sources') if isinstance(probe, dict) else []
    if isinstance(probe_rows, dict):
        probe_rows = [dict(key=key, **(value if isinstance(value, dict) else {}))
                      for key, value in probe_rows.items()]
    probe_by_key = {row.get('key') or row.get('source'): row for row in probe_rows or []
                    if isinstance(row, dict)}
    latest_meta = {}
    for date in reversed(snapshot_dates or []):
        try:
            if snapshot_module is None:
                continue
            snap = snapshot_module.load_snapshot(date)
        except (OSError, ValueError):
            continue
        if not isinstance(snap, dict):
            continue
        for key, meta in (snap.get('sources') or {}).items():
            if key not in latest_meta and isinstance(meta, dict):
                latest_meta[key] = dict(meta, snapshot_date=date)
    out = []
    for source in source_rows:
        key = source['key']
        accepted = by_source.get(key, [])
        success_dates = [show.get('last_seen') for show in accepted if show.get('last_seen')]
        latest = max(success_dates) if success_dates else None
        observed = probe_by_key.get(key) or {}
        meta = latest_meta.get(key) or {}
        access = source.get('access') or 'unknown'
        failure = observed.get('failure_reason') or observed.get('error')
        probe_state = observed.get('status') or observed.get('state')
        explicitly_disabled = source.get('enabled') is False
        postponed = source.get('postponed') is True
        if postponed:
            state = 'postponed'
        elif explicitly_disabled:
            state = 'disabled'
        elif failure or str(probe_state).lower() in {'error', 'failed', 'blocked'}:
            state = 'error'
        elif access == 'dead':
            state = 'disabled'
        elif meta.get('n_raw') is not None or latest:
            state = 'retained_data'
        elif access in {'apify-browser-proxy', 'apify-html'}:
            state = 'requires_route'
        else:
            state = 'not_recorded'
        if state == 'postponed':
            next_action = 'Postponed by the source plan; keep historical evidence and do not treat it as missing.'
        elif state == 'error':
            next_action = 'Inspect the latest safe source failure, then retry the configured route.'
        elif state == 'disabled':
            next_action = 'Keep historical evidence only; re-enable after a fresh source probe.'
        elif state == 'requires_route':
            next_action = 'Run the configured browser/Apify route; no result is treated as zero evidence.'
        elif state == 'not_recorded':
            next_action = 'Run the configured fetch and record a source-health result.'
        else:
            next_action = 'Revalidate coverage on the next scheduled fetch.'
        out.append(dict(
            key=key, name=source.get('name') or key,
            purpose='Primary validation' if key in primary else 'Discovery',
            source_role='primary_validation' if key in primary else 'discovery',
            tier=source.get('tier') or 'unknown', access=access,
            state=state, last_attempted=(meta.get('crawled_at') or observed.get('attempted_at') or
                                        observed.get('checked_at')),
            last_successful=observed.get('successful_at') or latest,
            accepted_rows=meta.get('n_events') if meta else None,
            reviewed_rows=(meta.get('n_review', len(meta.get('review') or []))
                           if meta else None),
            fetched_rows=meta.get('n_raw'), parser_rate=(
                meta.get('n_events') / meta.get('n_raw') if isinstance(meta.get('n_events'), (int, float))
                and isinstance(meta.get('n_raw'), (int, float)) and meta.get('n_raw') else None),
            coverage=observed.get('coverage') or meta.get('coverage') or meta.get('route'), failure_reason=failure,
            retained_data_date=meta.get('snapshot_date') or latest, crawled_at=meta.get('crawled_at'),
            next_action=next_action,
            note=source.get('notes') or 'No source note recorded.',
            freshness_days=_days_old(latest, today),
        ))
    return out


def _latest_job_log(mode: str) -> dict:
    folder = os.path.join(OUT, 'automation')
    names = glob.glob(os.path.join(folder, f'{mode}_*.log'))
    if not names:
        return dict(name=mode, state='not_recorded', last_run=None, result='Unknown',
                    log_path=None, artifact_date=None, next_run=None,
                    credentials_available='Not recorded', note='No local automation log found yet.')
    path = max(names, key=os.path.getmtime)
    result = 'Unknown'
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            handle.seek(max(0, os.path.getsize(path) - 4096))
            tail = handle.read()
        match = re.search(r'completed with exit code\s+(\d+)', tail)
        if match:
            result = 'Success' if match.group(1) == '0' else f'Failed (exit {match.group(1)})'
    except OSError:
        pass
    return dict(name=mode, state='recorded',
                last_run=dt.datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec='seconds'),
                result=result, log_path=_relative(path), artifact_date=None, next_run=None,
                credentials_available='Not recorded',
                note='Status is inferred from the latest non-secret local automation log.')


def _operations() -> dict:
    """Use an optional safe status sidecar, or a narrow log summary when absent."""
    sidecar = {}
    for candidate in (os.path.join(OUT, 'automation', 'status.json'),
                      os.path.join(OUT, 'automation_status.json'),
                      os.path.join(DATA, 'automation_status.json')):
        value = _read_json(candidate, None)
        if isinstance(value, dict):
            sidecar = _safe(value)
            break
    jobs = []
    for mode in ('weekly', 'monthly'):
        recorded = sidecar.get(mode) if isinstance(sidecar.get(mode), dict) else None
        if recorded:
            jobs.append(dict(name=mode, state=recorded.get('state') or recorded.get('status') or 'recorded',
                             last_run=recorded.get('last_run') or recorded.get('finished_at'),
                             result=recorded.get('result') or 'Unknown',
                             log_path=recorded.get('log_path'),
                             artifact_date=recorded.get('artifact_date'),
                             next_run=recorded.get('next_run'),
                             credentials_available=recorded.get('credentials_available', 'Not recorded'),
                             note=recorded.get('note') or 'Read from a local safe status sidecar.'))
        else:
            jobs.append(_latest_job_log(mode))
    return dict(jobs=jobs, note=('Scheduler task metadata is optional. Credential values are never '
                                 'read or rendered by this dashboard.'))


def _safe_watchlist(row: dict | None) -> dict:
    row = row or {}
    return dict(status=row.get('status'), owner=row.get('owner'),
                first_contacted=row.get('first_contacted'), do_not_pursue=bool(row.get('do_not_pursue')),
                contact_note_count=len(row.get('contact_notes') or []),
                note=('Contact-note contents are intentionally omitted from generated dashboard data; '
                      'they remain in the local watchlist.'))


def _artist_row(lifecycle_row: dict, score_row: dict | None, shows: list[dict], source_primary: dict,
                corrections: dict, review_by_slug: dict[str, list[dict]], watchlist: dict,
                today: dt.date) -> dict:
    slug = lifecycle_row['slug']
    primary_ids = {item.get('show_id') for item in
                   ((lifecycle_row.get('evidence') or {}).get('primary') or [])}
    evidence = []
    for show in shows:
        matches = [entity for entity in (show.get('entities') or [])
                   if isinstance(entity, dict) and entity.get('slug') == slug]
        if not matches:
            continue
        entity = matches[0]
        source = str(show.get('source') or '')
        notes = _correction_notes(corrections, show.get('show_id'), slug)
        evidence.append(dict(
            show_id=show.get('show_id'), source=source, source_role=('primary_validation'
                     if source in source_primary else 'discovery'),
            primary_confirmation=show.get('show_id') in primary_ids,
            title=show.get('title'), url=_safe_url(show.get('url')), show_date=show.get('show_date'),
            city=show.get('city'), venue=show.get('venue'), venue_band=show.get('venue_band'),
            role=show.get('role'), status=_status_of(show), first_seen=show.get('first_seen'),
            last_seen=show.get('last_seen'), parse_confidence=(entity.get('confidence') or
            entity.get('parse_confidence') or show.get('parse_confidence') or show.get('confidence') or
            show.get('genre_confidence')),
            correction_notes=notes,
        ))
    evidence.sort(key=lambda item: (item.get('show_date') or '', item.get('first_seen') or '',
                                     item.get('show_id') or ''), reverse=True)
    last_seen_values = [item.get('last_seen') for item in evidence if item.get('last_seen')]
    last_seen = max(last_seen_values) if last_seen_values else None
    cities = sorted({item['city'] for item in evidence if item.get('city')})
    sources = sorted({item['source'] for item in evidence if item.get('source')})
    venue_bands = sorted({item['venue_band'] for item in evidence if item.get('venue_band')})
    reviews = review_by_slug.get(slug, [])
    rejected_primary = ((lifecycle_row.get('evidence') or {}).get('rejected_primary') or [])
    identity_rejections = [str(item.get('rejection_reason') or '') for item in rejected_primary
                           if any(term in str(item.get('rejection_reason') or '').lower()
                                  for term in ('identity review', 'gazetteer identity', 'attest'))]
    unknowns = []
    if (score_row or {}).get('genre') in (None, 'unknown'):
        unknowns.append('Genre is unknown and is held out of the main signing ranking.')
    if any(not item.get('venue_band') for item in evidence):
        unknowns.append('At least one venue has no classified room band.')
    correction_notes = sorted({note for item in evidence for note in item['correction_notes']})
    if identity_rejections:
        reviews = list(reviews) + [dict(reason=identity_rejections[0], source='lifecycle')]
    review_state = ('review_required' if reviews else 'unknown' if unknowns else 'clear')
    score_row = score_row or {}
    momentum = score_row.get('momentum') or _metric(note='No India-side momentum calculation is available.')
    stature = score_row.get('stature') or _metric(note='No India-side stature calculation is available.')
    export = score_row.get('export_signal') or _metric(note='No diaspora evidence is available.')
    lifecycle_evidence = lifecycle_row.get('evidence') or {}
    return dict(
        slug=slug, name=lifecycle_row.get('name') or score_row.get('name') or slug,
        stage=lifecycle_row.get('stage') or 'Discovered', next_action=lifecycle_row.get('next_action') or
        'Review the retained evidence before acting.', primary_confirmed=bool(lifecycle_row.get('primary_confirmed')),
        kind=lifecycle_row.get('kind') or score_row.get('kind') or 'artist',
        genre=score_row.get('genre') or 'unknown', quadrant=score_row.get('quadrant') or 'UNCLASSIFIED',
        quadrant_why=score_row.get('quadrant_why') or 'No scored quadrant is available.',
        candidate_eligible=bool(lifecycle_row.get('candidate_eligible', True)),
        scores=dict(stature=stature, momentum=momentum, export_signal=export,
                    calibrated=score_row.get('calibrated'), basis=score_row.get('basis')),
        observability=score_row.get('observability') or dict(n_snapshots=0, span_days=0,
        trajectory_observable=False, why='No retained observability record.'),
        diaspora=(lifecycle_evidence.get('diaspora') or {}),
        primary_evidence=lifecycle_evidence.get('primary') or [],
        rejected_primary=lifecycle_evidence.get('rejected_primary') or [],
        evidence=evidence, sources=sources, cities=cities, venue_bands=venue_bands,
        last_seen=last_seen, freshness_days=_days_old(last_seen, today),
        freshness_state=('unknown' if not last_seen else 'stale' if _days_old(last_seen, today) is not None and
                         _days_old(last_seen, today) > 7 else 'recent'),
        review_state=review_state, review_notes=[item.get('reason') for item in reviews if item.get('reason')],
        unknowns=unknowns, corrections=correction_notes,
        watchlist=_safe_watchlist(watchlist.get(slug)),
        export_readiness=score_row.get('export') or {},
        forecast_note=lifecycle_row.get('forecast_note') or
        'This dashboard does not produce a ticket forecast; the handoff is a dossier only.',
    )


def build_payload(asof: str | dt.date | None = None) -> dict:
    """Build a safe live dashboard projection without changing project state."""
    # Imports live here so dashboard.py's self-test remains isolated and data-free.
    import demand
    import lifecycle
    import score
    import signals
    import snapshot

    today = _date(asof) or dt.date.today()
    ledger = snapshot.load_ledger()
    snapshots = snapshot.snapshot_dates()
    shows = list(ledger.get('shows') or [])
    demand_data = demand.load()
    source_config = lifecycle.load_sources()
    source_rows = _source_rows(source_config)
    source_primary = lifecycle.primary_sources(source_config)
    reviews = _snapshot_reviews(snapshot, snapshots)
    unresolved_identity = _unresolved_identity_rows(shows)
    review_by_slug: dict[str, list[dict]] = defaultdict(list)
    for row in reviews:
        for slug in row.get('slugs') or []:
            review_by_slug[slug].append(row)
    corrections = _read_json(os.path.join(DATA, 'corrections.json'), dict(events={}, entities={}))
    watchlist_root = _read_json(os.path.join(DATA, 'watchlist.json'), dict(artists={}))
    watchlist = watchlist_root.get('artists') or {} if isinstance(watchlist_root, dict) else {}
    observability = signals.observability(ledger)
    score_rows = {signal['slug']: score.score_artist(signal, dem=demand_data)
                  for signal in signals.for_all(ledger) if signal.get('candidate_eligible', True)}
    lifecycle_rows = lifecycle.build(ledger, demand_data, asof=today.isoformat(),
                                     sources_config=source_config)
    artists = [_artist_row(row, score_rows.get(row['slug']), shows, source_primary, corrections,
                           review_by_slug, watchlist, today) for row in lifecycle_rows]
    artists.sort(key=lambda row: (STAGE_ORDER.get(row['stage'], 99), row['name'].casefold()))
    stage_counts = {stage: sum(1 for row in artists if row['stage'] == stage) for stage in STAGES}
    actions = [dict(slug=row['slug'], name=row['name'], stage=row['stage'],
                    next_action=row['next_action'], review_state=row['review_state'],
                    primary_confirmed=row['primary_confirmed']) for row in artists
               if row['stage'] != 'Forecast ready' or row['review_state'] == 'review_required']
    actions.sort(key=lambda row: (row['review_state'] != 'review_required',
                                  STAGE_ORDER.get(row['stage'], 99), row['name'].casefold()))
    unknown_genre = sum(1 for row in artists if row['genre'] == 'unknown')
    unknown_venue = sum(1 for row in artists if any(not item.get('venue_band') for item in row['evidence']))
    freshness_date = ledger.get('last_snapshot') or (snapshots[-1] if snapshots else None)
    source_health = _source_health(source_rows, shows, reviews, lifecycle, today,
                                   _read_json(os.path.join(DATA, 'source_probe.json'), {}), snapshot, snapshots)
    return _safe(dict(
        schema_version=1, mode='live', generated_at=dt.datetime.now().isoformat(timespec='seconds'),
        as_of=today.isoformat(), title='Artist Finder — Private Scout',
        privacy='Local/private dashboard. Do not deploy or share scouting data publicly without approval.',
        caveat=('Booking, room-band, platform, added-date, and status signals are promoter-side proxies, '
                'not ticket sales. India-side traction and diaspora research are separate; this tool emits no '
                'North American ticket forecast.'),
        observability=observability,
        freshness=dict(last_snapshot=freshness_date, age_days=_days_old(freshness_date, today),
                       state=('no_data' if not freshness_date else 'stale' if _days_old(freshness_date, today) and
                              _days_old(freshness_date, today) > 7 else 'recent'),
                       snapshot_count=len(snapshots), first_snapshot=(snapshots[0] if snapshots else None)),
        lifecycle=dict(stages=list(STAGES), counts=stage_counts),
        action_queue=actions[:40], artists=artists,
        source_health=source_health, operations=_operations(),
        quality=dict(review_rows=len(reviews), unresolved_identity_rows=len(unresolved_identity),
                     unresolved_identity_evidence=unresolved_identity, unknown_genre=unknown_genre,
                     unknown_venue_band=unknown_venue,
                     corrections_event=len((corrections.get('events') or {})),
                     corrections_entity=len((corrections.get('entities') or {}))),
        empty_states=dict(no_rising_live=(not observability.get('trajectory_observable') and
                       not any(row.get('quadrant') == 'RISING' for row in artists)),
                       note=('No live RISING candidates are expected until at least three crawls span 21 days.')),
    ))


def fixture_payload() -> dict:
    """Explicit, non-live demo covering lifecycle and absent-data states."""
    observable = dict(n_snapshots=3, span_days=28, first='2026-07-01', last='2026-07-29',
                      trajectory_observable=True, why=None)
    unobservable = dict(n_snapshots=1, span_days=0, first='2026-08-19', last='2026-08-19',
                        trajectory_observable=False,
                        why='need >=3 snapshots spanning >=21 days; have 1 spanning 0')

    def evidence(slug, source, role, when, band='club', confidence=0.91):
        return [dict(show_id=f'fixture:{slug}', source=source, source_role=role,
                     primary_confirmation=role == 'primary_validation', title=f'{slug.title()} live',
                     url=f'https://example.test/{slug}', show_date=when, city='Mumbai',
                     venue='Fixture Hall', venue_band=band, role='headline', status='available',
                     first_seen='2026-07-01', last_seen='2026-07-29', parse_confidence=confidence,
                     correction_notes=[])]

    no_diaspora = dict(us=dict(avg_monthly=None, fetched_at=None, source=None, usable=False,
                                note='no usable stored search evidence'),
                       ca=dict(avg_monthly=None, fetched_at=None, source=None, usable=False,
                               note='no usable stored search evidence'), representative=None,
                       qualifying_foreign_dates=[], usable=False,
                       note='No diaspora evidence; export readiness is unvalidated, not zero.')
    validated_diaspora = dict(us=dict(avg_monthly=1200, fetched_at='2026-07-29', source='fixture', usable=True),
                              ca=dict(avg_monthly=2700, fetched_at='2026-07-29', source='fixture', usable=True),
                              representative=dict(geo='ca', avg_monthly=2700,
                              rule='stronger geography shown; US and Canada are never summed'),
                              qualifying_foreign_dates=[dict(country='United Kingdom', city='London',
                              date='2026-10-05', source='fixture', url='https://example.test/london')],
                              usable=True, note='Fixture diaspora evidence only.')
    rows = [
        dict(slug='fixture-discovered', name='Fixture Discovered', stage='Discovered',
             next_action='Confirm a present or future booking on a configured primary platform.',
             primary_confirmed=False, kind='artist', genre='unknown', quadrant='UNCLASSIFIED',
             quadrant_why='Fixture has only one crawl; no trajectory is observable.',
             candidate_eligible=True, scores=dict(stature=_metric(note='Unknown room band.'),
             momentum=_metric(note='No trajectory: one crawl only.'), export_signal=_metric(
             note='No diaspora evidence fetched.'), calibrated=False, basis='fixture'),
             observability=unobservable, diaspora=no_diaspora, primary_evidence=[], rejected_primary=[],
             evidence=evidence('fixture-discovered', 'allevents', 'discovery', '2026-08-30', None, 0.42),
             sources=['allevents'], cities=['Mumbai'], venue_bands=[], last_seen='2026-08-19',
             freshness_days=10, freshness_state='stale', review_state='review_required',
             review_notes=['Low parsing confidence; confirm the identity before action.'],
             unknowns=['Genre and venue band are unknown.'], corrections=[],
             watchlist=_safe_watchlist({}), export_readiness={},
             forecast_note='Fixture only — not a forecast.'),
        dict(slug='fixture-confirmed', name='Fixture Confirmed', stage='Major-platform confirmed',
             next_action='Review US and Canada evidence separately and check foreign dates.',
             primary_confirmed=True, kind='artist', genre='comedy', quadrant='EARLY',
             quadrant_why='No trajectory is observable in this fixture row.', candidate_eligible=True,
             scores=dict(stature=_metric(31, True, 'Room bands are booking proxies.'),
             momentum=_metric(note='Insufficient history.'), export_signal=_metric(note='No diaspora evidence.'),
             calibrated=False, basis='fixture'), observability=unobservable, diaspora=no_diaspora,
             primary_evidence=evidence('fixture-confirmed', 'district', 'primary_validation', '2026-09-04'),
             rejected_primary=[], evidence=evidence('fixture-confirmed', 'district', 'primary_validation', '2026-09-04'),
             sources=['district'], cities=['Mumbai'], venue_bands=['club'], last_seen='2026-08-19',
             freshness_days=10, freshness_state='stale', review_state='clear', review_notes=[], unknowns=[],
             corrections=[], watchlist=_safe_watchlist({}), export_readiness={}, forecast_note='Fixture only.'),
        dict(slug='fixture-validated', name='Fixture Validated', stage='Diaspora validated',
             next_action='Resolve the canonical artist identity, then prepare the dossier handoff.',
             primary_confirmed=True, kind='artist', genre='music_indie', quadrant='RISING',
             quadrant_why='Fixture-only trajectory demonstration after 3 crawls spanning 28 days.',
             candidate_eligible=True, scores=dict(stature=_metric(42, True, 'Room band level.'),
             momentum=_metric(67, True, 'Fixture trajectory evidence.', 1.0,
             [dict(signal='room_escalation', observable=True, note='club to mid theatre')]),
             export_signal=_metric(58, True, 'Representative geography is Canada, never US+CA.', 0.75),
             calibrated=False, basis='fixture judgement weights'), observability=observable,
             diaspora=validated_diaspora,
             primary_evidence=evidence('fixture-validated', 'bookmyshow', 'primary_validation', '2026-09-12', 'mid theatre'),
             rejected_primary=[], evidence=evidence('fixture-validated', 'bookmyshow', 'primary_validation', '2026-09-12', 'mid theatre'),
             sources=['bookmyshow'], cities=['Mumbai'], venue_bands=['mid theatre'], last_seen='2026-07-29',
             freshness_days=0, freshness_state='recent', review_state='unknown',
             review_notes=[], unknowns=['Canonical identity needs resolution before a dossier.'], corrections=[],
             watchlist=_safe_watchlist({}), export_readiness={'ready': False}, forecast_note='Fixture only.'),
        dict(slug='fixture-ready', name='Fixture Ready', stage='Forecast ready',
             next_action='Prepare and approve the Artist Tour Engine dossier handoff; this is readiness only, not a ticket forecast.',
             primary_confirmed=True, kind='artist', genre='comedy', quadrant='ESTABLISHED',
             quadrant_why='Fixture level and trajectory labels.', candidate_eligible=True,
             scores=dict(stature=_metric(76, True, 'Room bands are booking proxies.'),
             momentum=_metric(54, True, 'Fixture trajectory evidence.', 0.8),
             export_signal=_metric(63, True, 'Representative geography is Canada, never US+CA.', 0.75),
             calibrated=False, basis='fixture judgement weights'), observability=observable, diaspora=validated_diaspora,
             primary_evidence=evidence('fixture-ready', 'district', 'primary_validation', '2026-09-18', 'mid theatre'),
             rejected_primary=[], evidence=evidence('fixture-ready', 'district', 'primary_validation', '2026-09-18', 'mid theatre'),
             sources=['district'], cities=['Mumbai'], venue_bands=['mid theatre'], last_seen='2026-07-29',
             freshness_days=0, freshness_state='recent', review_state='clear', review_notes=[], unknowns=[],
             corrections=['Fixture correction overlay is traceable.'], watchlist=_safe_watchlist({'owner': 'Scout'}),
             export_readiness={'ready': True}, forecast_note='Dossier-handoff ready is not a ticket forecast.'),
    ]
    stages = {stage: sum(1 for row in rows if row['stage'] == stage) for stage in STAGES}
    return dict(schema_version=1, mode='fixture', generated_at='2026-07-29T12:00:00', as_of='2026-07-29',
                title='Artist Finder — Fixture demonstration',
                privacy='Fixture only. Local/private operating model still applies.',
                caveat=('FIXTURE DATA ONLY. Booking and room signals are proxies, not ticket sales. '
                        'No ticket forecast is produced.'), observability=observable,
                freshness=dict(last_snapshot='2026-07-29', age_days=0, state='recent', snapshot_count=3,
                               first_snapshot='2026-07-01'), lifecycle=dict(stages=list(STAGES), counts=stages),
                action_queue=[dict(slug=row['slug'], name=row['name'], stage=row['stage'],
                                   next_action=row['next_action'], review_state=row['review_state'],
                                   primary_confirmed=row['primary_confirmed']) for row in rows[:-1]],
                artists=rows,
                source_health=[dict(key='bookmyshow', name='BookMyShow', purpose='Primary validation',
                                    source_role='primary_validation', tier='major', access='apify-browser-proxy',
                                    state='error', last_attempted='2026-07-29T09:00:00', last_successful='2026-07-01',
                                    accepted_rows=0, reviewed_rows=0, fetched_rows=None, parser_rate=None,
                                    coverage='Low-yield teaser only', failure_reason='Fixture blocked route',
                                    retained_data_date='2026-07-01', freshness_days=28,
                                    next_action='Run the configured browser/proxy route; retained data is not zero evidence.',
                                    note='Fixture source error state.'),
                               dict(key='district', name='District', purpose='Primary validation',
                                    source_role='primary_validation', tier='major', access='direct-jsonld',
                                    state='retained_data', last_attempted='2026-07-29T09:00:00', last_successful='2026-07-29',
                                    accepted_rows=2, reviewed_rows=0, fetched_rows=4, parser_rate=0.5, coverage='Fixture',
                                    failure_reason=None, retained_data_date='2026-07-29', freshness_days=0,
                                    next_action='Revalidate coverage on the next fetch.', note='Fixture healthy state.'),
                               dict(key='townscript', name='Townscript', purpose='Discovery',
                                    source_role='discovery', tier='longtail', access='apify-html',
                                    state='postponed', last_attempted=None, last_successful=None,
                                    accepted_rows=0, reviewed_rows=0, fetched_rows=None, parser_rate=None,
                                    coverage=None, failure_reason=None, retained_data_date=None, freshness_days=None,
                                    next_action='Postponed by the source plan; keep historical evidence and do not treat it as missing.',
                                    note='Fixture postponed-source state.')],
                operations=dict(jobs=[dict(name='weekly', state='recorded', last_run='2026-07-29T08:00:00',
                                            result='Success', log_path='out/automation/weekly_fixture.log',
                                            artifact_date='2026-07-29', next_run='Scheduled',
                                            credentials_available='Not recorded', note='Fixture job state.'),
                                      dict(name='monthly', state='not_recorded', last_run=None, result='Unknown',
                                           log_path=None, artifact_date=None, next_run='Scheduled',
                                           credentials_available='Not recorded', note='Fixture missing job state.')],
                                note='Fixture only; no credentials are present.'),
                quality=dict(review_rows=1, unresolved_identity_rows=1,
                             unresolved_identity_evidence=[dict(show_id='fixture:missing-slug',
                             name='Unresolved Fixture Entity', kind='artist', source='allevents',
                             title='Fixture identity review', url='https://example.test/unresolved',
                             show_date='2026-08-03', city='Mumbai',
                             reason='Missing canonical slug: held out of lifecycle and scoring until identity review resolves it.')],
                             unknown_genre=1, unknown_venue_band=1,
                             corrections_event=1, corrections_entity=0),
                empty_states=dict(no_rising_live=False,
                                  note='Fixture demonstrates trajectory. Live data must establish its own history.'))


def _selftest() -> int:
    payload = fixture_payload()
    rows = {row['slug']: row for row in payload['artists']}
    flattened = json.dumps(payload).lower()
    class _LifecycleFixture:
        @staticmethod
        def primary_sources(config):
            return {}
    source_states = _source_health([
        dict(key='postponed-source', name='Postponed fixture', access='apify-html', postponed=True),
        dict(key='disabled-source', name='Disabled fixture', access='apify-html', enabled=False),
        dict(key='unattempted-source', name='Unattempted fixture', access='apify-browser-proxy'),
    ], [], [], _LifecycleFixture, dt.date(2026, 7, 29), {})
    checks = [
        ('all four lifecycle stages render', set(payload['lifecycle']['counts']) == set(STAGES) and
         all(payload['lifecycle']['counts'][stage] == 1 for stage in STAGES)),
        ('US and Canada remain separate', rows['fixture-ready']['diaspora']['us']['avg_monthly'] == 1200 and
         rows['fixture-ready']['diaspora']['ca']['avg_monthly'] == 2700),
        ('representative geography is labelled, not summed',
         rows['fixture-ready']['diaspora']['representative']['geo'] == 'ca' and
         rows['fixture-ready']['diaspora']['representative']['avg_monthly'] == 2700),
        ('one-crawl fixture makes no trajectory claim',
         not rows['fixture-discovered']['observability']['trajectory_observable'] and
         rows['fixture-discovered']['scores']['momentum']['value'] is None),
        ('fixture trajectory is explicitly labelled', rows['fixture-validated']['quadrant'] == 'RISING' and
         rows['fixture-validated']['observability']['trajectory_observable']),
        ('no merged score emitted', 'combined_score' not in flattened and 'ticket forecast' in flattened),
        ('review, unknown, stale, and source-error states render',
         rows['fixture-discovered']['review_state'] == 'review_required' and
         rows['fixture-discovered']['freshness_state'] == 'stale' and
         payload['source_health'][0]['state'] == 'error'),
        ('missing-slug evidence is visibly held out for review',
         payload['quality']['unresolved_identity_rows'] == 1 and
         payload['quality']['unresolved_identity_evidence'][0]['name'] == 'Unresolved Fixture Entity'),
        ('postponed/disabled source flags beat route requirements',
         [row['state'] for row in source_states] == ['postponed', 'disabled', 'requires_route'] and
         payload['source_health'][2]['state'] == 'postponed'),
        ('unattempted source metrics stay unknown instead of zero',
         source_states[2]['accepted_rows'] is None and
         source_states[2]['reviewed_rows'] is None and source_states[2]['fetched_rows'] is None),
    ]
    passed = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        passed = passed and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if passed else "SELF-TEST FAILED"}')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

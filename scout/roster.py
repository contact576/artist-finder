"""Durable artist/niche registry for the monthly search-intelligence product.

Keyword ideas are discovery evidence, not identity evidence.  This module keeps
the candidate inbox separate from the verified artist registry and preserves
every merge/rejection so a generic phrase is not rediscovered each month.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from typing import Any

import gazetteer
import model

DATA = os.path.join(model.BASE, 'data')
PATH = os.path.join(DATA, 'artist_roster.json')
CANDIDATES_PATH = os.path.join(DATA, 'artist_candidates.json')
NICHES_PATH = os.path.join(DATA, 'niches.json')
WATCHLIST_PATH = os.path.join(DATA, 'watchlist.json')
DEMAND_PATH = os.path.join(DATA, 'demand.json')
IDENTITY_REVIEWS_PATH = os.path.join(DATA, 'identity_reviews.json')

STATUSES = ('candidate', 'verified', 'inactive', 'merged', 'rejected')
ACTIVE_KINDS = ('artist', 'dj_night')
RESEARCH_STATES = ('public_identity_review', 'directory_candidate', 'legacy_unreviewed')


def _read(path: str, default: Any) -> Any:
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _atomic_write(path: str, data: Any) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + os.path.basename(path), suffix='.tmp',
                                     dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def empty_registry() -> dict:
    return dict(schema_version=1, updated_at=None, artists={})


def empty_candidates() -> dict:
    return dict(schema_version=1, updated_at=None, discovery_runs=[], candidates={})


def load(path: str | None = None) -> dict:
    data = _read(path or PATH, empty_registry())
    if not isinstance(data, dict) or not isinstance(data.get('artists'), dict):
        raise ValueError('artist roster must contain an artists object')
    return data


def save(data: dict, path: str | None = None) -> str:
    validate(data)
    data['updated_at'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    return _atomic_write(path or PATH, data)


def load_candidates(path: str | None = None) -> dict:
    data = _read(path or CANDIDATES_PATH, empty_candidates())
    if not isinstance(data, dict) or not isinstance(data.get('candidates'), dict):
        raise ValueError('artist candidates must contain a candidates object')
    return data


def save_candidates(data: dict, path: str | None = None) -> str:
    data['updated_at'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    return _atomic_write(path or CANDIDATES_PATH, data)


def load_niches(path: str | None = None) -> list[dict]:
    data = _read(path or NICHES_PATH, dict(niches=[]))
    rows = data.get('niches') if isinstance(data, dict) else []
    return [row for row in (rows or []) if isinstance(row, dict) and row.get('id')]


def validate(data: dict) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get('artists'), dict):
        raise ValueError('artist roster must contain an artists object')
    seen_names = {}
    niche_ids = {row['id'] for row in load_niches() if not row.get('ui_only')}
    for slug, artist in data['artists'].items():
        if not isinstance(artist, dict):
            raise ValueError(f'artist {slug} must be an object')
        if artist.get('slug') != slug or not artist.get('name'):
            raise ValueError(f'artist {slug} must have matching slug and non-empty name')
        if artist.get('status') not in STATUSES:
            raise ValueError(f'artist {slug} has invalid status')
        key = str(artist['name']).strip().casefold()
        if key in seen_names and artist.get('status') not in ('merged', 'rejected'):
            raise ValueError(f'duplicate active canonical name: {artist["name"]}')
        seen_names[key] = slug
        if artist.get('status') == 'verified' and not artist.get('measurement_keyword'):
            raise ValueError(f'verified artist {slug} needs one measurement keyword')
        research_state = artist.get('research_state')
        if research_state and research_state not in RESEARCH_STATES:
            raise ValueError(f'artist {slug} has invalid research_state')
        if artist.get('curated'):
            evidence = artist.get('identity_evidence') or {}
            if (research_state not in ('public_identity_review', 'directory_candidate')
                    or not str(evidence.get('url') or '').startswith(('https://', 'http://'))
                    or not evidence.get('reviewed_at')):
                raise ValueError(f'curated artist {slug} needs dated public evidence')
            if artist.get('primary_genre') not in niche_ids:
                raise ValueError(f'curated artist {slug} has unknown category')
        if artist.get('status') == 'verified' and artist.get('keyword_review_state') != 'approved':
            raise ValueError(f'verified artist {slug} needs an approved measurement keyword')
    return True


def _review_map() -> dict:
    data = _read(IDENTITY_REVIEWS_PATH, dict(reviews={}))
    return data.get('reviews') if isinstance(data, dict) else {}


def _verified_identity(slug: str, name: str, reviews: dict) -> tuple[bool, dict]:
    review = reviews.get(slug) if isinstance(reviews, dict) else None
    if (isinstance(review, dict) and review.get('status') == 'verified'
            and str(review.get('name') or '').casefold() == str(name).casefold()):
        return True, dict(kind='audited_review', reviewed_at=review.get('reviewed_at'),
                          note=review.get('source_evidence'), url=review.get('evidence_url'))
    if gazetteer.exact_known(name):
        return True, dict(kind='exact_gazetteer', reviewed_at=None,
                          note='Exact canonical identity in the maintained artist gazetteer.',
                          url=None)
    return False, dict(kind='legacy_candidate', reviewed_at=None,
                       note='Migrated from the prior scout; identity review is still required.',
                       url=None)


def seed_from_watchlist(data: dict | None = None, save_now: bool = True,
                        today: str | None = None) -> tuple[dict, dict]:
    """Merge eligible legacy records without upgrading unverified identities."""
    registry = data if data is not None else load()
    watchlist = _read(WATCHLIST_PATH, dict(artists={}))
    demand = _read(DEMAND_PATH, dict(entities={}))
    reviews = _review_map()
    today = today or dt.date.today().isoformat()
    added = verified = retained = 0
    for slug, old in sorted((watchlist.get('artists') or {}).items()):
        if not isinstance(old, dict):
            continue
        if old.get('do_not_pursue') or old.get('active') is False or not old.get('candidate_eligible'):
            continue
        if old.get('kind', 'artist') not in ACTIVE_KINDS:
            continue
        name = str(old.get('name') or '').strip()
        if not name:
            continue
        current = registry['artists'].get(slug)
        if current:
            retained += 1
            continue
        is_verified, evidence = _verified_identity(slug, name, reviews)
        demand_entry = (demand.get('entities') or {}).get(slug) or {}
        aliases = sorted({str(item).strip() for item in demand_entry.get('aliases') or []
                          if str(item).strip() and str(item).casefold() != name.casefold()})
        registry['artists'][slug] = dict(
            slug=slug, name=name, aliases=aliases,
            primary_genre=old.get('genre') or 'unknown', niche_tags=[],
            status='verified' if is_verified else 'candidate',
            measurement_keyword=name, keyword_review_state=('approved' if is_verified else 'pending'),
            contamination_status='unchecked', contamination_note=None,
            identity_evidence=evidence, first_seen=today,
            verified_at=(evidence.get('reviewed_at') or today if is_verified else None),
            last_reviewed=evidence.get('reviewed_at'), merged_into=None,
            source='legacy_watchlist_migration')
        added += 1
        verified += int(is_verified)
    if save_now:
        save(registry)
    return registry, dict(added=added, verified=verified, retained=retained,
                          total=len(registry['artists']))


def measurement_artists(data: dict | None = None, include_candidates: bool = False) -> list[dict]:
    registry = data if data is not None else load()
    def eligible(row):
        if row.get('status') == 'verified':
            return bool(row.get('measurement_keyword'))
        # A broad pull may measure evidence-sourced research candidates, but never the
        # unreviewed legacy crawl phrases that were retained only for audit/history.
        return bool(include_candidates and row.get('status') == 'candidate'
                    and row.get('curated') is True
                    and row.get('research_state') == 'directory_candidate'
                    and row.get('measurement_keyword'))
    return sorted((row for row in registry['artists'].values() if eligible(row)),
                  key=lambda row: (str(row.get('name')).casefold(), row.get('slug')))


def add_discovery_candidates(niche_id: str, rows: list[dict], fetched_at: str | None = None,
                             data: dict | None = None, save_now: bool = True) -> tuple[dict, dict]:
    inbox = data if data is not None else load_candidates()
    fetched_at = fetched_at or dt.datetime.now().astimezone().isoformat(timespec='seconds')
    added = updated = 0
    for item in rows or []:
        text = ' '.join(str(item.get('text') or '').split())
        if not text:
            continue
        key = model.slugify(f'{niche_id}-{text}')
        current = inbox['candidates'].get(key)
        record = dict(candidate_id=key, text=text, niche_id=niche_id,
                      state=(current or {}).get('state', 'needs_review'),
                      discovered_at=(current or {}).get('discovered_at', fetched_at),
                      last_seen=fetched_at, source='google_ads_keyword_ideas',
                      avg_monthly_searches=item.get('avg_monthly_searches'),
                      competition=item.get('competition'),
                      close_variants=list(item.get('close_variants') or []),
                      review_note=(current or {}).get('review_note'),
                      roster_slug=(current or {}).get('roster_slug'))
        inbox['candidates'][key] = record
        if current:
            updated += 1
        else:
            added += 1
    inbox.setdefault('discovery_runs', []).append(dict(niche_id=niche_id, fetched_at=fetched_at,
                                                       returned=len(rows or []), added=added,
                                                       updated=updated))
    inbox['discovery_runs'] = inbox['discovery_runs'][-120:]
    if save_now:
        save_candidates(inbox)
    return inbox, dict(added=added, updated=updated, total=len(inbox['candidates']))


def approve_candidate(candidate_id: str, name: str, genre: str, evidence_url: str,
                      reviewed_at: str | None = None, measurement_keyword: str | None = None,
                      registry: dict | None = None, inbox: dict | None = None,
                      save_now: bool = True) -> dict:
    registry = registry if registry is not None else load()
    inbox = inbox if inbox is not None else load_candidates()
    candidate = inbox['candidates'].get(candidate_id)
    if not candidate:
        raise ValueError(f'unknown candidate: {candidate_id}')
    if not str(evidence_url or '').startswith(('https://', 'http://')):
        raise ValueError('approval needs a dated public evidence URL')
    reviewed_at = reviewed_at or dt.date.today().isoformat()
    slug = model.slugify(name)
    existing = registry['artists'].get(slug) or {}
    registry['artists'][slug] = dict(existing,
        slug=slug, name=name, aliases=sorted(set(existing.get('aliases') or [])),
        primary_genre=genre, niche_tags=sorted(set((existing.get('niche_tags') or []) +
                                                   [candidate.get('niche_id')])),
        status='verified', measurement_keyword=measurement_keyword or name,
        keyword_review_state='approved', contamination_status='unchecked',
        contamination_note=existing.get('contamination_note'),
        identity_evidence=dict(kind='candidate_review', reviewed_at=reviewed_at,
                               note='Verified from the monthly keyword-idea candidate inbox.',
                               url=evidence_url),
        first_seen=existing.get('first_seen') or candidate.get('discovered_at') or reviewed_at,
        verified_at=reviewed_at, last_reviewed=reviewed_at, merged_into=None,
        source='keyword_idea_review')
    candidate.update(state='approved', roster_slug=slug, review_note='Promoted after identity review.')
    if save_now:
        save(registry)
        save_candidates(inbox)
    return registry['artists'][slug]


def _selftest() -> int:
    registry = empty_registry()
    inbox = empty_candidates()
    inbox, result = add_discovery_candidates('bhajan_jamming', [
        dict(text='Bhajan Jamming', avg_monthly_searches=1000),
        dict(text='Example Performer', avg_monthly_searches=800),
    ], fetched_at='2026-08-21T10:00:00+05:30', data=inbox, save_now=False)
    candidate_id = model.slugify('bhajan_jamming-Example Performer')
    approved = approve_candidate(candidate_id, 'Example Performer', 'devotional',
                                 'https://example.com/example-performer',
                                 registry=registry, inbox=inbox, save_now=False)
    registry['artists']['researched-candidate'] = dict(
        slug='researched-candidate', name='Researched Candidate', aliases=[],
        primary_genre='comedy', niche_tags=['comedy'], status='candidate',
        measurement_keyword='Researched Candidate', keyword_review_state='pending',
        contamination_status='unchecked', contamination_note=None,
        identity_evidence=dict(kind='directory_or_festival_candidate',
                               reviewed_at='2026-08-21', note='Fixture directory candidate.',
                               url='https://example.com/directory'),
        first_seen='2026-08-21', verified_at=None, last_reviewed='2026-08-21',
        merged_into=None, source='fixture', research_state='directory_candidate',
        curated=True)
    registry['artists']['legacy-phrase'] = dict(
        slug='legacy-phrase', name='Legacy Phrase', aliases=[], primary_genre='unknown',
        niche_tags=[], status='candidate', measurement_keyword='Legacy Phrase',
        keyword_review_state='pending', contamination_status='unchecked',
        contamination_note=None, identity_evidence=dict(kind='legacy_candidate',
        reviewed_at=None, note='Fixture legacy row.', url=None), first_seen='2026-08-21',
        verified_at=None, last_reviewed=None, merged_into=None, source='fixture',
        research_state='legacy_unreviewed', curated=False)
    checks = [
        ('generic keyword idea stays in review inbox',
         inbox['candidates'][model.slugify('bhajan_jamming-Bhajan Jamming')]['state'] == 'needs_review'),
        ('approval creates one verified canonical artist',
         approved['status'] == 'verified' and approved['measurement_keyword'] == 'Example Performer'),
        ('approved artist carries traceable evidence',
         approved['identity_evidence']['url'].startswith('https://')),
        ('measurement list excludes unverified candidates',
         len(measurement_artists(registry)) == 1),
        ('broad measurement includes researched candidates but excludes legacy phrases',
         [row['slug'] for row in measurement_artists(registry, include_candidates=True)] ==
         ['example-performer', 'researched-candidate']),
        ('candidate import counted deterministically', result['added'] == 2),
        ('registry validates', validate(registry)),
    ]
    ok = True
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())

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
RESEARCH_STATES = ('public_identity_review', 'directory_candidate', 'operator_candidate', 'identity_review_ambiguous', 'legacy_unreviewed')


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
                    and row.get('measurement_keyword')
                    and ((row.get('curated') is True
                          and row.get('research_state') == 'directory_candidate')
                         or row.get('research_state') == 'operator_candidate'))
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


OPERATOR_TEXT_LIMIT = 160
OPERATOR_URL_LIMIT = 1000

def _operator_text(value, field, limit=OPERATOR_TEXT_LIMIT):
    text = ' '.join(str(value or '').split())
    if not text: raise ValueError(f'{field} is required')
    if len(text) > limit: raise ValueError(f'{field} must be at most {limit} characters')
    return text

def _operator_url(value):
    if value in (None, ''): return None
    url = str(value).strip()
    if len(url) > OPERATOR_URL_LIMIT or not url.startswith(('https://', 'http://')): raise ValueError('evidence_url must be a public http(s) URL')
    return url

def _operator_niche(niche_id):
    value = _operator_text(niche_id, 'category', 80)
    if value not in {row['id'] for row in load_niches() if not row.get('ui_only')}: raise ValueError('category is not a configured niche')
    return value

def operator_add_artist(name, category, measurement_keyword=None, evidence_url=None, data=None, save_now=True, path=None):
    """Add an operator identity as a candidate only; never implicit verification."""
    registry = data if data is not None else load(path); name = _operator_text(name, 'name'); category = _operator_niche(category); keyword = _operator_text(measurement_keyword or name, 'keyword'); url = _operator_url(evidence_url); slug = model.slugify(name)
    if not slug or len(slug) > 120: raise ValueError('name does not produce a valid slug')
    if slug in registry['artists']: raise ValueError('an artist with that slug already exists')
    timestamp = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    artist = dict(slug=slug, name=name, aliases=[], primary_genre=category, niche_tags=[category], status='candidate', measurement_keyword=keyword, keyword_variants=[], keyword_review_state='pending', contamination_status='unchecked', contamination_note=None, identity_evidence=dict(kind='operator_candidate', reviewed_at=None, note='Added through the local operator API; review required.', url=url), first_seen=timestamp[:10], verified_at=None, last_reviewed=None, merged_into=None, source='local_operator', research_state='operator_candidate', curated=False)
    registry['artists'][slug] = artist
    if save_now: save(registry, path)
    return artist

def operator_set_category(slug, category, data=None, save_now=True, path=None):
    registry = data if data is not None else load(path)
    if slug not in registry['artists']: raise ValueError('unknown artist slug')
    category = _operator_niche(category); artist = registry['artists'][slug]; old_category = artist.get('primary_genre'); artist['primary_genre'] = category; artist['niche_tags'] = [category]; artist.setdefault('category_history', []).append(dict(category=category, previous_category=old_category, changed_at=dt.datetime.now().astimezone().isoformat(timespec='seconds'))); artist['last_reviewed'] = dt.date.today().isoformat()
    if save_now: save(registry, path)
    return artist

def operator_set_status(slug, active, data=None, save_now=True, path=None):
    registry = data if data is not None else load(path); artist = registry['artists'].get(slug)
    if not artist: raise ValueError('unknown artist slug')
    if not isinstance(active, bool): raise ValueError('active must be boolean')
    if not active and artist.get('status') not in ('inactive', 'merged', 'rejected'):
        artist['operator_prior_status'] = artist.get('status'); artist['status'] = 'inactive'
    elif active and artist.get('status') == 'inactive':
        prior = artist.pop('operator_prior_status', 'candidate'); artist['status'] = prior if prior in ('candidate', 'verified') else 'candidate'
    artist['last_reviewed'] = dt.date.today().isoformat()
    if save_now: save(registry, path)
    return artist

def operator_update_keyword(slug, keyword, action, previous_keyword=None, data=None, save_now=True, path=None):
    registry = data if data is not None else load(path); artist = registry['artists'].get(slug)
    if not artist: raise ValueError('unknown artist slug')
    keyword = _operator_text(keyword, 'keyword')
    if action not in ('add', 'replace'): raise ValueError('keyword action must be add or replace')
    if action == 'replace':
        if previous_keyword is not None and previous_keyword != artist.get('measurement_keyword'): raise ValueError('previous_keyword does not match the current measurement keyword')
        artist['measurement_keyword'] = keyword
        # A local operator explicitly replaces a verified identity's one measurement term.
        # That is human approval of the term, unlike a candidate which remains review-pending.
        artist['keyword_review_state'] = 'approved' if artist.get('status') == 'verified' else 'pending'
    else:
        variants = list(artist.get('keyword_variants') or [])
        if keyword != artist.get('measurement_keyword') and keyword not in variants: variants.append(keyword)
        # Variants are lookup aliases only; they are never fetched or summed into demand.
        artist['keyword_variants'] = sorted(variants, key=str.casefold)
    artist['last_reviewed'] = dt.date.today().isoformat()
    if save_now: save(registry, path)
    return artist
def operator_edit_artist(slug, name=None, aliases=None, evidence_url=None, evidence_note=None, data=None, save_now=True, path=None):
    """Edit identity fields without changing durable slug or verification state."""
    registry = data if data is not None else load(path); artist = registry['artists'].get(slug)
    if not artist: raise ValueError('unknown artist slug')
    if name is not None:
        name = _operator_text(name, 'name'); old_name = artist.get('name')
        if name != old_name:
            aliases_now = list(artist.get('aliases') or [])
            if old_name and old_name not in aliases_now: aliases_now.append(old_name)
            artist['aliases'] = sorted(set(aliases_now), key=str.casefold); artist['name'] = name
    if aliases is not None:
        if not isinstance(aliases, list) or len(aliases) > 30: raise ValueError('aliases must be a list of at most 30 names')
        supplied = {_operator_text(item, 'alias') for item in aliases}
        artist['aliases'] = sorted(set(artist.get('aliases') or []) | supplied, key=str.casefold)
    if evidence_url is not None or evidence_note is not None:
        evidence = dict(artist.get('identity_evidence') or {})
        if evidence_url is not None: evidence['url'] = _operator_url(evidence_url)
        if evidence_note is not None: evidence['note'] = _operator_text(evidence_note, 'evidence_note', 500)
        artist['identity_evidence'] = evidence
    artist['last_reviewed'] = dt.date.today().isoformat()
    if save_now: save(registry, path)
    return artist
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
        ('operator add stays a candidate', operator_add_artist('Operator Person', 'comedy', data=registry, save_now=False)['status'] == 'candidate'),
        ('operator add joins only broad next measurement pull', 'operator-person' not in [row['slug'] for row in measurement_artists(registry)] and 'operator-person' in [row['slug'] for row in measurement_artists(registry, include_candidates=True)]),
        ('operator category and keyword are validated', operator_set_category('operator-person', 'devotional', data=registry, save_now=False)['primary_genre'] == 'devotional' and operator_update_keyword('operator-person', 'Operator Person Live', 'replace', data=registry, save_now=False)['measurement_keyword'] == 'Operator Person Live' and registry['artists']['operator-person']['keyword_review_state'] == 'pending' and registry['artists']['operator-person']['category_history']),
        ('verified keyword replacement remains approved and measurable', operator_update_keyword('example-performer', 'Example Performer Live', 'replace', data=registry, save_now=False)['keyword_review_state'] == 'approved' and [row['slug'] for row in measurement_artists(registry)] == ['example-performer']),
        ('keyword variant is an alias, never a replacement', operator_update_keyword('example-performer', 'Example Performer Tickets', 'add', data=registry, save_now=False)['measurement_keyword'] == 'Example Performer Live' and registry['artists']['example-performer']['keyword_variants'] == ['Example Performer Tickets']),
        ('operator rename preserves slug and old alias', operator_edit_artist('operator-person', name='Renamed Operator', data=registry, save_now=False)['slug'] == 'operator-person' and 'Operator Person' in registry['artists']['operator-person']['aliases']),
        ('operator deactivation is reversible', operator_set_status('operator-person', False, data=registry, save_now=False)['status'] == 'inactive' and operator_set_status('operator-person', True, data=registry, save_now=False)['status'] == 'candidate'),
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

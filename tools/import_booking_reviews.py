"""Apply an explicitly reviewed change manifest. Never infer approval from search results.

Dry-run by default; --write merges roster fields and the dated review sidecar.
Research evidence is retained separately. No demand history or favorites are edited.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scout'))
import roster


def merge(registry, retained, manifest):
    updated = copy.deepcopy(registry)
    reviews = copy.deepcopy(retained)
    date = manifest['reviewed_at']
    changes = []
    for slug, decision in manifest['decisions'].items():
        review = decision['review']
        if review.get('reviewed_at') != date:
            raise ValueError('review dates must match the approved manifest')
        sources = review.get('sources') or []
        if not sources or not all(str(s.get('url', '')).startswith(('http://', 'https://')) for s in sources):
            raise ValueError(f'{slug}: every decision needs public source evidence')
        existing = updated['artists'].get(slug)
        if existing and any(item.get('manifest_id') == manifest['id']
                            for item in existing.get('research_history', [])):
            # An old reviewed plan must not overwrite newer operator decisions.
            continue
        if existing:
            expected = decision.get('expected_current')
            fields = ('name', 'primary_genre', 'measurement_keyword', 'status')
            if not isinstance(expected, dict) or any(expected.get(key) != existing.get(key) for key in fields):
                raise ValueError(f'{slug}: roster changed or expected_current missing; review the current row first')
        row = copy.deepcopy(existing or {})
        if decision.get('archive'):
            if not existing:
                raise ValueError(f'{slug}: cannot archive a nonexistent artist')
            if row.get('status') not in ('inactive', 'merged', 'rejected'):
                row['operator_prior_status'] = row.get('status', 'candidate')
                row['status'] = 'inactive'
        else:
            supplied = decision['artist']
            allowed = {'name', 'primary_genre', 'niche_tags', 'measurement_keyword', 'status', 'entity_type'}
            if set(supplied) - allowed:
                raise ValueError(f'{slug}: unsupported artist fields; operator metadata must be preserved')
            if row.get('identity_evidence'):
                history = row.setdefault('identity_evidence_history', [])
                if row['identity_evidence'] not in history:
                    history.append(copy.deepcopy(row['identity_evidence']))
            row.update(supplied)
            row.update(slug=slug, entity_type=supplied.get('entity_type', 'artist'),
                       curated=True, research_state='public_identity_review',
                       last_reviewed=date,
                       identity_evidence=dict(kind='booking_research', url=sources[0]['url'],
                                              reviewed_at=date, note=review.get('identity_note', 'Dated public artist review.')))
            row.setdefault('first_seen', date)
            row.setdefault('aliases', [])
            row.setdefault('source', 'monthly_booking_research')
            row.setdefault('merged_into', None)
            row['keyword_review_state'] = 'approved' if row.get('status') == 'verified' else 'pending'
            if row.get('status') == 'verified':
                row.setdefault('verified_at', date)
            row['search_quality_state'] = review.get('search_quality', 'unchecked')
            row['search_quality_keyword'] = review.get('search_keyword')
            row['contamination_status'] = 'ambiguous' if row['search_quality_state'] == 'ambiguous' else 'reviewed'
            row['contamination_note'] = review.get('search_note')
            # Research must not silently reactivate a deliberate operator archive.
            if existing and existing.get('status') in ('inactive', 'merged', 'rejected'):
                row['status'] = existing['status']
        row.setdefault('research_history', [])
        audit = dict(reviewed_at=date, manifest_id=manifest['id'],
                     previous_status=(existing or {}).get('status'),
                     previous_keyword=(existing or {}).get('measurement_keyword'))
        if not any(item.get('manifest_id') == manifest['id'] for item in row['research_history']):
            row['research_history'].append(audit)
        updated['artists'][slug] = row
        prior = reviews.setdefault('reviews', {}).get(slug)
        if prior != review:
            if prior:
                reviews.setdefault('history', {}).setdefault(slug, []).append(prior)
            reviews['reviews'][slug] = copy.deepcopy(review)
        changes.append(slug)
    roster.validate(updated)
    reviews.update(schema_version=1, updated_at=date)
    return updated, reviews, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    with open(args.manifest, encoding='utf-8') as handle:
        manifest = json.load(handle)
    path = BASE / 'data' / 'artist_reviews.json'
    retained = roster._read(str(path), dict(schema_version=1, reviews={}))
    updated, reviews, changed = merge(roster.load(), retained, manifest)
    if args.write and changed:
        # Publish as one Git commit only after both files pass validation. Write evidence
        # first so a failed roster save cannot publish new identities without their basis.
        roster._atomic_write(str(path), reviews)
        try:
            roster.save(updated)
        except Exception:
            roster._atomic_write(str(path), retained)
            raise
    print(json.dumps(dict(mode='written' if args.write else 'dry-run',
                          decisions=len(changed), total_artists=len(updated['artists']),
                          retained_reviews=len(reviews['reviews'])), indent=2))


if __name__ == '__main__':
    main()

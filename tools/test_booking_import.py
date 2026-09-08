"""Importer must preserve subsequent operator edits when an old plan is replayed."""
import unittest
from unittest.mock import patch
import import_booking_reviews as subject


class ImportTests(unittest.TestCase):
    def test_replay_preserves_newer_user_edit(self):
        registry = {'artists': {'one': {'name': 'Operator renamed', 'status': 'inactive',
                    'measurement_keyword': 'Operator query', 'research_history': [{'manifest_id': 'old'}]}}}
        retained = {'reviews': {'one': {'note': 'newer evidence'}}}
        manifest = {'id': 'old', 'reviewed_at': '2026-09-08', 'decisions': {'one': {
            'artist': {'name': 'Old name'}, 'review': {'reviewed_at': '2026-09-08',
            'sources': [{'url': 'https://example.com/review'}]}}}}
        with patch.object(subject.roster, 'validate'):
            result, reviews, changes = subject.merge(registry, retained, manifest)
        self.assertEqual(result, registry)
        self.assertEqual(reviews['reviews'], retained['reviews'])
        self.assertEqual(changes, [])

    def test_revision_requires_current_row_precondition(self):
        registry = {'artists': {'one': {'name': 'Operator name'}}}
        manifest = {'id': 'new', 'reviewed_at': '2026-09-08', 'decisions': {'one': {
            'artist': {'name': 'Override'}, 'review': {'reviewed_at': '2026-09-08',
            'sources': [{'url': 'https://example.com/review'}]}}}}
        with self.assertRaisesRegex(ValueError, 'expected_current'):
            subject.merge(registry, {'reviews': {}}, manifest)


if __name__ == '__main__':
    unittest.main()

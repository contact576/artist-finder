"""Offline regression for the observed 2026-09-08 Google rate-limit failure."""
import io
import unittest
from unittest.mock import patch
import urllib.error
import fetch_search_volume as planner


class RetryTests(unittest.TestCase):
    def error(self, code):
        return urllib.error.HTTPError('https://example.invalid', code, 'test', {},
                                      io.BytesIO(b'{"message":"Retry in 30 seconds."}'))

    def test_quota_retry_recovers(self):
        with patch.object(planner.urllib.request, 'urlopen',
                          side_effect=[self.error(429), io.BytesIO(b'{"ok":true}')]) as call, \
                patch.object(planner.time, 'sleep') as sleep:
            self.assertEqual(planner._post('https://example.invalid', {}), {'ok': True})
            self.assertEqual(call.call_count, 2)
            sleep.assert_called_once_with(30)

    def test_retries_are_bounded(self):
        with patch.object(planner.urllib.request, 'urlopen',
                          side_effect=[self.error(429) for _ in range(4)]) as call, \
                patch.object(planner.time, 'sleep') as sleep:
            with self.assertRaises(SystemExit):
                planner._post('https://example.invalid', {})
            self.assertEqual(call.call_count, 4)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [30, 60, 60])

    def test_auth_errors_not_retried(self):
        with patch.object(planner.urllib.request, 'urlopen', side_effect=self.error(401)), \
                patch.object(planner.time, 'sleep') as sleep:
            with self.assertRaises(SystemExit):
                planner._post('https://example.invalid', {})
            sleep.assert_not_called()

    def test_cooldown_hint(self):
        self.assertEqual(planner._retry_delay(0, {'Retry-After': '45'}, ''), 45)
        self.assertEqual(planner._retry_delay(0, {}, 'Retry in 45 seconds.'), 45)


if __name__ == '__main__':
    unittest.main()

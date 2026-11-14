"""Clock boundaries and repeated transient failures, entirely offline."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
import test_cross_codex_health as fixtures

h = fixtures.h


class HealthRoundTwo(unittest.TestCase):
    setUp = fixtures.HealthEvidence.setUp
    write = fixtures.HealthEvidence.write
    check = fixtures.HealthEvidence.check
    receipt = fixtures.HealthEvidence.receipt

    def test_receipt_five_minute_boundary(self):
        for seconds, accepted in [(299, True), (300, True), (301, False)]:
            with self.subTest(seconds=seconds):
                self.receipt((self.now + timedelta(seconds=seconds)).isoformat())
                self.assertEqual(h.last_brief_sent(self.work, self.now) is not None, accepted)

    def test_bad_latest_run_shapes_are_unavailable(self):
        for value in [[], None, 'x', 1, {'run_dir': []}, {'run_dir': str(self.run), 'status': []}]:
            with self.subTest(value=value):
                self.write(self.work / 'latest_run.json', value)
                self.assertEqual(self.check()['checks']['last_run']['status'], 'unavailable')

    def test_pending_only_on_due_day_between_nine_and_noon(self):
        for hour, minute, pending in [(0, 0, False), (8, 59, False), (9, 0, True),
                                       (11, 59, True), (12, 0, False), (23, 0, False)]:
            with self.subTest(hour=hour, minute=minute):
                now = datetime(2026, 11, 14, hour, minute, tzinfo=h.SYDNEY)
                result = h.daily_brief(self.work, now, True, True)
                self.assertEqual(result.get('phase') == 'pending', pending)

    def test_publication_working_limit(self):
        for hours, status in [(26, 'ok'), (30, 'ok'), (32, 'ok'), (32.01, 'stale')]:
            with self.subTest(hours=hours):
                self.write(self.served / 'current/manifest.json', {
                    'snapshot_finished_utc': (self.now - timedelta(hours=hours)).isoformat()})
                self.assertEqual(self.check(brief_active=True)['checks']['publication']['status'], status)


class MailGrace(unittest.TestCase):
    def test_transient_chain_never_renews_verification(self):
        now = datetime(2026, 11, 14, tzinfo=timezone.utc)
        previous = {'checks': {'mail_authorization': {'verified_at_utc': now.isoformat()}}}
        def fail(_):
            raise TimeoutError('synthetic')
        for hours in [1, 3, 5, 6, 7, 8]:
            with self.subTest(hours=hours):
                result = h.mail_authorization(None, 'sender@example.org', now + timedelta(hours=hours), previous, fail)
                self.assertEqual(result['status'], 'ok' if hours <= 6 else 'failed')
                if hours <= 6:
                    self.assertEqual(result['verified_at_utc'], now.isoformat())
                previous = {'checks': {'mail_authorization': result}}

    def test_untrusted_previous_state_cannot_grant_grace(self):
        now = datetime(2026, 11, 14, tzinfo=timezone.utc)
        stamps = [(now + timedelta(seconds=1)).isoformat(), '2026-11-14T00:00:00', '{']
        values = [None, [], 'damaged', {'checks': []}]
        values += [{'checks': {'mail_authorization': {'verified_at_utc': s}}} for s in stamps]
        def fail(_):
            raise TimeoutError('synthetic')
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(h.mail_authorization(None, 'sender@example.org', now, value, fail)['status'], 'failed')

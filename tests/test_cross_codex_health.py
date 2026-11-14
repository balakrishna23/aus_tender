"""Health evidence must fail closed without false morning alarms."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
spec = importlib.util.spec_from_file_location('cross_health', ROOT / 'deploy/illoura_cloud_health.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class HealthEvidence(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.run = self.work / 'reports/daily_sweep_fixture'
        self.run.mkdir(parents=True)
        self.served = self.work / 'served'
        (self.served / 'current').mkdir(parents=True)
        self.now = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
        self.write(self.work / 'latest_run.json', {'run_dir': str(self.run), 'status': 'sent'})
        self.write(self.run / 'newsletter_evidence.json', {'generated_at_utc': self.now.isoformat()})
        self.write(self.served / 'current/manifest.json', {'snapshot_finished_utc': self.now.isoformat()})

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding='utf-8')

    def receipt(self, stamp, sent=True, run=None):
        self.write((run or self.run) / 'send_receipt.json', {'sent': sent, 'recorded_at_utc': stamp})

    def check(self, **kwargs):
        with patch.dict(os.environ, {'ILLOURA_GMAIL_SENDER': 'sender@example.com'}), patch.object(h.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100 * 1024**3, 20 * 1024**3, 80 * 1024**3)):
            options = dict(work=self.work, served=self.served, token=self.work / 'fake-token',
                           now=self.now, brief_enabled=False, brief_active=False,
                           verify_mail=lambda _: 'sender@example.com', cert=None)
            options.update(kwargs)
            return h.check(**options)

    def test_deadlines_across_dst_and_timezones(self):
        """Sydney deadlines must survive both DST changes and India midnight."""
        for day in ((2026, 4, 5), (2026, 10, 4), (2026, 11, 14)):
            for hour, minute in ((8, 14), (8, 15), (8, 59), (9, 0), (11, 59), (12, 0)):
                local = datetime(*day, hour, minute, tzinfo=h.SYDNEY)
                stamp = (local.replace(hour=8, minute=15) if (hour, minute) >= (8, 15)
                         else local - timedelta(days=1))
                self.receipt(stamp.astimezone(timezone.utc).isoformat())
                with self.subTest(local=local):
                    self.assertEqual(h.daily_brief(self.work, local.astimezone(timezone(timedelta(hours=5, minutes=30))), True)['status'], 'ok')

    def test_pending_ends_at_noon(self):
        """A working service buys grace until noon, never indefinitely."""
        for hour, minute, expected in ((9, 0, 'ok'), (11, 59, 'ok'), (12, 0, 'missed')):
            now = datetime(2026, 11, 14, hour, minute, tzinfo=h.SYDNEY)
            self.assertEqual(h.daily_brief(self.work, now, True, working=True)['status'], expected)
        self.assertEqual(h.daily_brief(self.work, self.now, True, working=False)['status'], 'missed')
        self.assertEqual(h.daily_brief(self.work, self.now, False)['status'], 'ok')

    def test_tomorrow_receipt_does_not_satisfy_today(self):
        """Clock skew or a forged future receipt must not mask missed delivery."""
        self.receipt((self.now + timedelta(days=1)).isoformat())
        self.assertEqual(h.daily_brief(self.work, self.now, True)['status'], 'missed')

    def test_receipt_list_does_not_crash_scan(self):
        """One corrupt receipt must not take down the entire health poll."""
        self.write(self.run / 'send_receipt.json', [])
        self.assertEqual(h.daily_brief(self.work, self.now, True)['status'], 'missed')

    def test_naive_receipt_cannot_count_as_delivery(self):
        """Receipt interpretation must not depend on the server's local timezone."""
        self.receipt('2026-11-14T00:00:00')
        self.assertEqual(h.daily_brief(self.work, self.now, True)['status'], 'missed')

    def test_multiple_receipts_and_false_sent(self):
        """Newest unsuccessful attempts must not eclipse a genuine sent receipt."""
        other = self.work / 'reports/daily_sweep_second'
        other.mkdir()
        self.receipt(self.now.isoformat())
        self.receipt((self.now + timedelta(minutes=5)).isoformat(), False, other)
        self.assertEqual(h.last_brief_sent(self.work), self.now)

    def test_each_critical_check_causes_attention(self):
        """Grace must not conceal a real disk, cert, auth, or coordinator failure."""
        self.assertEqual(self.check()['status'], 'ok')
        for name in ('publication', 'collection', 'last_run', 'coordinator', 'retention'):
            paths = {'publication': self.served / 'current/manifest.json',
                     'collection': self.run / 'newsletter_evidence.json',
                     'last_run': self.work / 'latest_run.json',
                     'coordinator': self.work / 'coordinator_status.json',
                     'retention': self.served / 'retention.json'}
            path = paths[name]
            old = path.read_bytes() if path.exists() else None
            self.write(path, {'status': 'failed', 'started_at_utc': self.now.isoformat()})
            with self.subTest(check=name):
                self.assertEqual(self.check()['status'], 'attention')
            if old is None:
                path.unlink()
            else:
                path.write_bytes(old)

    def test_wrong_type_latest_run_reports_attention(self):
        """A truncated coordinator replacement must yield a pollable failure."""
        self.write(self.work / 'latest_run.json', [])
        self.assertEqual(self.check()['status'], 'attention')

    def test_morning_grace_never_hides_auth_certificate_or_disk(self):
        """A busy morning service must not make independent real failures green."""
        def revoked(_):
            raise ValueError('revoked')
        self.assertEqual(self.check(brief_active=True, verify_mail=revoked)['status'], 'attention')
        self.assertEqual(self.check(brief_active=True, cert=self.work / 'fake-cert', read_cert_end=lambda _: self.now - timedelta(seconds=1))['status'], 'attention')
        with patch.object(h.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100, 99, 1)):
            # Call directly so the healthy-disk fixture in self.check cannot override this fault.
            with patch.dict(os.environ, {'ILLOURA_GMAIL_SENDER': 'sender@example.com'}):
                result = h.check(work=self.work, served=self.served, now=self.now,
                                 brief_enabled=False, brief_active=True, cert=None,
                                 verify_mail=lambda _: 'sender@example.com')
        self.assertEqual(result['checks']['disk']['status'], 'low')
        self.assertEqual(result['status'], 'attention')

    def test_late_publication_and_running_collection_are_normal(self):
        """A recent running retry may temporarily keep yesterday's publication green."""
        self.write(self.work / 'latest_run.json', {'run_dir': str(self.run), 'status': 'running',
                   'started_at_utc': (self.now - timedelta(hours=1)).isoformat()})
        self.write(self.served / 'current/manifest.json', {'snapshot_finished_utc': (self.now - timedelta(hours=28)).isoformat()})
        result = self.check(brief_active=True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['checks']['publication']['phase'], 'in_progress')
        self.write(self.served / 'current/manifest.json', {'snapshot_finished_utc': (self.now - timedelta(hours=33)).isoformat()})
        self.assertEqual(self.check(brief_active=True)['status'], 'attention')

    def test_wrong_type_coordinator_reports_attention(self):
        """A JSON scalar coordinator file must not crash the health service."""
        self.write(self.work / 'coordinator_status.json', [])
        self.assertEqual(self.check()['status'], 'attention')

    def test_naive_running_timestamp_reports_attention(self):
        """A timestamp without timezone must fail closed rather than crash."""
        self.write(self.work / 'latest_run.json', {'run_dir': str(self.run), 'status': 'running', 'started_at_utc': '2026-11-14T00:00:00'})
        self.assertEqual(self.check()['status'], 'attention')

    def test_certificate_boundaries(self):
        """Missing, expired and near-expiry HTTPS certificates must be visible."""
        for seconds, expected in ((86401, 'ok'), (86400, 'expiring'), (1, 'expiring'), (0, 'expired'), (-1, 'expired')):
            self.assertEqual(h.certificate('fake', self.now, lambda _: self.now + timedelta(seconds=seconds))['status'], expected)
        def missing(_):
            raise FileNotFoundError()
        self.assertEqual(h.certificate('fake', self.now, missing)['status'], 'missing')

    def test_transient_grace_cannot_hide_revocation_or_clock_skew(self):
        """Only recent verified authorization can tolerate a transient failure."""
        Timeout = type('Timeout', (Exception,), {})
        for age, error, expected in ((0, Timeout(), 'ok'), (21600, Timeout(), 'ok'),
                                      (21601, Timeout(), 'failed'), (-1, Timeout(), 'failed'),
                                      (0, ValueError('revoked'), 'failed')):
            previous = {'checks': {'mail_authorization': {'verified_at_utc': (self.now - timedelta(seconds=age)).isoformat()}}}
            def verify(_, error=error):
                raise error
            self.assertEqual(h.mail_authorization('fake', 'sender@example.com', self.now, previous, verify)['status'], expected)

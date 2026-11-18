"""Exercise production send gates with fake Google modules and temporary artifacts."""
import base64
from contextlib import redirect_stdout
from datetime import datetime
from email import policy
from email.parser import BytesParser
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def load_sender():
    names = ['google', 'google.oauth2', 'google.oauth2.credentials', 'google.auth',
             'google.auth.transport', 'google.auth.transport.requests', 'fcntl']
    modules = {name: ModuleType(name) for name in names}
    modules['google.oauth2.credentials'].Credentials = Mock()
    transport = modules['google.auth.transport.requests']
    transport.AuthorizedSession = Mock()
    transport.Request = Mock()
    modules['fcntl'].flock = Mock()
    modules['fcntl'].LOCK_EX = 1
    modules['fcntl'].LOCK_NB = 2
    spec = importlib.util.spec_from_file_location('routine_sender', ROOT/'routine/scripts/send_gmail.py')
    sender = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules), patch.object(sys, 'path', [str(ROOT/'routine/scripts'), *sys.path]):
        spec.loader.exec_module(sender)
    return sender


class SenderTests(unittest.TestCase):
    def setUp(self):
        self.sender = load_sender()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name)
        env = patch.dict(os.environ, ILLOURA_MAIL_SENDER='sender@example.com',
                         ILLOURA_MAIL_RECIPIENT='reader@example.org',
                         ILLOURA_MAIL_ENV=str(self.out/'absent.env'))
        env.start()
        self.addCleanup(env.stop)
        self.write('source_refresh.json', {'success': True, 'feed': 'rebuilt', 'results': [
            {'spider': name, 'status': 'OK'} for name in ('contract_api', 'aus_contract',
            'aus_tender', 'planned_procurement', 'standing_offer', 'buy_ict', 'act_tender', 'nsw_tender')]})
        self.write('email_layout_checks.json', [{'viewport': v, 'passed': True} for v in ('desktop', 'mobile')])
        self.write('editorial_notes.json', {'edition_date': str(datetime.now(ZoneInfo('Asia/Kolkata')).date())})
        self.write('newsletter_editorial.json', {'from': 'sender@example.com', 'to': 'reader@example.org', 'subject': 'Example brief'})
        (self.out/'newsletter_preview.txt').write_text('Example brief')
        (self.out/'newsletter_preview.html').write_text('<p>Example brief</p>')
        self.approve()
        self.session = Mock()
        self.sender.Credentials.from_authorized_user_file.return_value.valid = True
        self.sender.AuthorizedSession.return_value = self.session
        self.session.get.side_effect = lambda url, **kw: self.response(
            {'emailAddress': 'SENDER@EXAMPLE.COM'} if url.endswith('/profile') else
            {} if url.endswith('/messages') else {'id': 'example-message', 'labelIds': ['SENT']})
        self.session.post.side_effect = self.post

    def write(self, name, value):
        (self.out/name).write_text(json.dumps(value))

    def approve(self):
        self.write('cloud_send_review.json', {'authorization': 'User requested the Illoura routine and email on AWS',
            'reviewed': True, 'sha256': {name: self.sender.digest(self.out/name) for name in
            ('newsletter_preview.html', 'newsletter_preview.txt', 'newsletter_editorial.json')}})

    def response(self, data):
        response = Mock()
        response.json.return_value = data
        return response

    def post(self, url, json, **kwargs):
        attempt = __import__('json').loads((self.out/'send_attempt.json').read_text())
        message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(json['raw']))
        self.assertEqual(message['From'], 'sender@example.com')
        self.assertEqual(message['To'], 'reader@example.org')
        self.assertEqual(message['Message-ID'], attempt['message_id_header'])
        self.assertEqual(attempt['sha256']['newsletter_preview.txt'], self.sender.digest(self.out/'newsletter_preview.txt'))
        return self.response({'id': 'example-message', 'threadId': 'example-thread'})

    def send(self):
        with redirect_stdout(io.StringIO()):
            self.sender._send_locked(self.out, self.out/'fake-token')

    def test_configured_send_and_attempt_before_post(self):
        self.send()
        self.session.post.assert_called_once()
        self.assertTrue(json.loads((self.out/'send_receipt.json').read_text())['sent'])
        with self.assertRaisesRegex(ValueError, 'receipt'):
            self.send()
        self.session.post.assert_called_once()

    def test_missing_review_refuses_before_google(self):
        (self.out/'cloud_send_review.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.send()
        self.session.post.assert_not_called()
        self.sender.AuthorizedSession.assert_not_called()

    NAMES = ('contract_api', 'aus_contract', 'aus_tender', 'planned_procurement', 'standing_offer', 'buy_ict', 'act_tender', 'nsw_tender')

    def source_report(self, success, **problems):
        self.write('source_refresh.json', {'success': success, 'feed': 'rebuilt', 'results': [
            dict({'spider': name, 'status': 'OK'}, **problems.get(name, {})) for name in self.NAMES]})

    GAP = {'planned_procurement': {'status': 'PARTIAL', 'crawl_failed': True, 'errors': 1}}

    def test_problem_in_a_source_the_brief_does_not_use_still_sends(self):
        self.source_report(False, **self.GAP)
        self.write('cloud_run.json', {'source_exit_code': 1, 'source_gaps': ['planned_procurement']})
        self.send()
        self.session.post.assert_called_once()

    def test_gap_needs_the_run_scripts_approval_and_nothing_else_failed(self):
        def report(**changes):
            data = json.loads((self.out/'source_refresh.json').read_text())
            data.update(changes)
            self.write('source_refresh.json', data)
        def no_success():
            data = json.loads((self.out/'source_refresh.json').read_text())
            del data['success']
            self.write('source_refresh.json', data)
        cases = {
            'no run state': lambda: (self.out/'cloud_run.json').unlink(),
            'run cut short': lambda: self.write('cloud_run.json', {'source_exit_code': 124, 'source_gaps': ['planned_procurement']}),
            'run approved other gaps': lambda: self.write('cloud_run.json', {'source_exit_code': 1, 'source_gaps': ['nsw_tender']}),
            'feed failed as well': lambda: report(feed='releases feed: FAILED (disk error); intelligence views rebuilt'),
            'success flag missing': no_success,
            'runner says success': lambda: report(success=True),
        }
        for label, spoil in cases.items():
            with self.subTest(label):
                self.source_report(False, **self.GAP)
                self.write('cloud_run.json', {'source_exit_code': 1, 'source_gaps': ['planned_procurement']})
                spoil()
                with self.assertRaises(ValueError):
                    self.send()
        self.session.post.assert_not_called()

    def test_buyict_problem_or_unclear_report_refuses(self):
        for label, arrange in {
            'buy_ict failed': lambda: self.source_report(False, buy_ict={'status': 'FAIL(1)'}),
            'buy_ict crawl failed': lambda: self.source_report(True, buy_ict={'crawl_failed': True}),
            'failure with no named source': lambda: self.source_report(False),
            'rows are not records': lambda: self.write('source_refresh.json', {'success': False, 'feed': 'rebuilt', 'results': [1] * 8}),
        }.items():
            with self.subTest(label):
                arrange()
                with self.assertRaises(ValueError):
                    self.send()
        self.session.post.assert_not_called()

    def test_safety_gates(self):
        cases = [('source_refresh.json', {'success': False}),
                 ('email_layout_checks.json', []), ('cloud_send_review.json', {'reviewed': False})]
        for name, invalid in cases:
            with self.subTest(name=name):
                original = (self.out/name).read_text()
                self.write(name, invalid)
                with self.assertRaises(ValueError):
                    self.send()
                (self.out/name).write_text(original)
        (self.out/'newsletter_preview.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Content changed'):
            self.send()
        self.session.post.assert_not_called()

    def test_sent_subject_reconciles_without_post(self):
        def get(url, **kwargs):
            if url.endswith('/profile'):
                return self.response({'emailAddress': 'sender@example.com'})
            if url.endswith('/messages'):
                self.assertIn('from:sender@example.com to:reader@example.org', kwargs['params']['q'])
                return self.response({'messages': [{'id': 'existing'}]})
            return self.response({'id': 'existing', 'labelIds': ['SENT'], 'payload': {'headers': [
                {'name': 'Subject', 'value': 'Example brief'}, {'name': 'From', 'value': 'sender@example.com'},
                {'name': 'To', 'value': 'reader@example.org'}]}})
        self.session.get.side_effect = get
        self.send()
        self.session.post.assert_not_called()
        self.assertTrue(json.loads((self.out/'send_receipt.json').read_text())['reconciled_existing_sent'])

    def test_unresolved_attempt_refuses_post(self):
        self.write('send_attempt.json', {})
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            self.send()
        self.session.post.assert_not_called()

    def test_wrong_recipient_refuses_post(self):
        self.write('newsletter_editorial.json', {'from': 'sender@example.com', 'to': 'other@example.org', 'subject': 'Example brief'})
        self.approve()
        with self.assertRaisesRegex(ValueError, 'sender/recipient'):
            self.send()
        self.session.post.assert_not_called()

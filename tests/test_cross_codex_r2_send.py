"""Synthetic send-state matrix: no Gmail, locks or actual mail."""
import itertools
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch, Mock
import test_cloud_daily_brief as fixtures
import test_cloud_collect as collect_fixtures

b = fixtures.brief
c = collect_fixtures.collect


class SendStateMatrix(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve()
        self.reports = self.work / 'reports'
        self.other = self.reports / 'daily_sweep_old'
        self.out = self.reports / 'daily_sweep_new'
        for folder in (self.other, self.out):
            folder.mkdir(parents=True)
            (folder / 'editorial_notes.json').write_text('{"edition_date":"2026-11-14"}')
        self.addCleanup(patch.stopall)
        patch.object(b, 'WORK', self.work).start()

    def state(self, name, value):
        p = self.other / name
        p.unlink(missing_ok=True)
        if value is not None:
            p.write_text(value)

    def test_settlement_matrix_agrees_with_edition_guard(self):
        """All 75 states: guard blocks exactly when delivery is proven or uncertain."""
        receipts = [None, '{', '[]', '{"sent":false}', '{"sent":true}']
        decisions = [None, '{', '[]', '{"delivered":true}', '{"delivered":false}']
        for attempt, receipt, decision in itertools.product([None, '{', '{}'], receipts, decisions):
            with self.subTest(attempt=attempt, receipt=receipt, decision=decision):
                self.state('send_attempt.json', attempt)
                self.state('send_receipt.json', receipt)
                self.state('send_reconciliation.json', decision)
                confirmed = receipt == '{"sent":true}'
                self.assertEqual(b.receipt_confirms_send(self.other), confirmed)
                unresolved = attempt is not None and not confirmed and decision != '{"delivered":false}'
                self.assertEqual(bool(c.unresolved_send_runs(self.reports)), unresolved)
                blocked = False
                try:
                    b.check_edition(self.out)
                except SystemExit:
                    blocked = True
                except (ValueError, TypeError, AttributeError) as error:
                    self.fail('Edition guard crashed instead of deciding: ' + type(error).__name__)
                self.assertEqual(blocked, confirmed or unresolved)

    def test_operator_can_clear_damaged_attempt(self):
        self.state('send_attempt.json', '{')
        with patch.object(b, 'send_lock', return_value=nullcontext()), redirect_stdout(StringIO()):
            result = b.reconcile(self.other, mark_undelivered=True, session=Mock())
        self.assertEqual(result, 0)
        self.assertEqual(c.unresolved_send_runs(self.reports), [])

    def test_reconcile_repairs_every_untrusted_receipt_decision_combination(self):
        for receipt, decision in itertools.product(
                [None, '{', '[]', '{"sent":false}', '{"sent":true}'],
                [None, '{', '[]', '{"delivered":true}', '{"delivered":false}']):
            with self.subTest(receipt=receipt, decision=decision):
                self.state('send_attempt.json', json.dumps(dict(sender='sender@example.org',
                    recipient='team@example.org', subject='Synthetic', message_id_header='<fixture>')))
                self.state('send_receipt.json', receipt)
                self.state('send_reconciliation.json', decision)
                with patch.object(b, 'send_lock', return_value=nullcontext()), \
                        patch.object(b, 'find_sent', return_value=({'id': 'm', 'labelIds': ['SENT']}, [])), \
                        redirect_stdout(StringIO()):
                    self.assertEqual(b.reconcile(self.other, session=Mock()), 0)
                self.assertTrue(b.receipt_confirms_send(self.other))
                self.assertEqual(c.unresolved_send_runs(self.reports), [])

    def test_missing_notes_with_attempt_blocks_same_edition(self):
        self.state('send_attempt.json', '{}')
        (self.other / 'editorial_notes.json').unlink()
        with self.assertRaises(SystemExit):
            b.check_edition(self.out)

    def test_damaged_notes_with_undelivered_decision_do_not_block_forever(self):
        self.state('send_attempt.json', '{}')
        self.state('send_reconciliation.json', '{"delivered":false}')
        (self.other / 'editorial_notes.json').write_text('{')
        b.check_edition(self.out)


class RecipientParsing(unittest.TestCase):
    def test_editorial_wording_is_one_short_sentence(self):
        notes = b.editorial_notes(fixtures.NOW.date(), {}, [], {'results': []})
        self.assertEqual(notes['introduction'],
                         'The newest open BuyICT requests, each rechecked on its public listing this morning.')
        self.assertEqual(notes['additional_sections'], [])

    def search(self, to, recipient='team@example.org'):
        attempt = dict(sender='sender@example.org', recipient=recipient, subject='Synthetic', message_id_header='<fixture>')
        message = {'id': 'm', 'labelIds': ['SENT'], 'payload': {'headers': [
            {'name': 'To', 'value': to}, {'name': 'From', 'value': 'Sender <sender@example.org>'},
            {'name': 'Subject', 'value': 'Synthetic'}]}}
        session = Mock()
        def get(url, **kwargs):
            value = ({'emailAddress': 'sender@example.org'} if url.endswith('/profile') else
                     {'messages': [{'id': 'm'}]} if url.endswith('/messages') else message)
            return Mock(json=lambda: value)
        session.get.side_effect = get
        return b.find_sent(session, attempt)[0]

    def test_group_multiple_and_quoted_comma(self):
        for header in ['Group: Team <team@example.org>, other@example.org;',
                       'other@example.org, "Team, Procurement" <TEAM@example.org>']:
            with self.subTest(header=header):
                self.assertIsNotNone(self.search(header))

    def test_display_name_cannot_impersonate_address(self):
        self.assertIsNone(self.search('"team@example.org, Someone" <other@example.org>'))

    def test_attempt_recipient_is_trimmed(self):
        self.assertIsNotNone(self.search('Team <team@example.org>', ' TEAM@EXAMPLE.ORG '))

    def test_odd_live_record_shapes_drop_listing(self):
        for record in [None, [], {'content': {'mainDetails': 12}},
                       {'content': {'mainDetails': [{'label': [], 'value': 'x'}]}}]:
            with self.subTest(record=record):
                self.assertFalse(b.still_open(record, fixtures.NOW, lambda _: None))

    def test_odd_restricted_records_are_safe(self):
        for record in [None, [], {'content': []}, {'content': 'text'}]:
            with self.subTest(record=record):
                self.assertFalse(b.restricted_hint(record))

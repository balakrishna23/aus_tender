"""Inbox replay, stale evidence, strict wire validation and retained audit fields."""
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch

from api import review_support as wire
from market_intelligence import review_inbox as inbox


class ReviewInboxTests(unittest.TestCase):
    def entry(self):
        row = {'key': 'insurance', 'value': None, 'evidence_url': None, 'note': 'Required'}
        return dict(kind='profile', target='insurance', expected=wire.fingerprint('profile', row),
                    evidence_url='https://example.gov.au/proof', notes='Checked the certificate', value='current',
                    id='a' * 32, reviewer='staff-test', submitted_at=datetime.now(timezone.utc).isoformat())

    def run_import(self, entry, outcome=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queued, ledger = root / 'inbox', root / 'ledger'
            queued.mkdir()
            (queued / '001.jsonl').write_text(json.dumps(entry) + '\n', encoding='utf-8')
            engine = Mock()
            engine.connect.return_value = nullcontext(Mock())
            with patch.object(inbox, 'apply', side_effect=outcome) as apply:
                first = inbox.import_inbox(engine, queued, ledger)
                second = inbox.import_inbox(engine, queued, ledger)
                record = json.loads((ledger / (entry['id'] + '.json')).read_text())
                return first, second, record, apply.call_count

    def test_idempotent_audit_fields_and_note(self):
        first, second, record, calls = self.run_import(self.entry())
        self.assertEqual(first['applied'], 1)
        self.assertEqual(second['already_processed'], 1)
        self.assertEqual(calls, 1)
        self.assertEqual(record['reviewer'], 'staff-test')
        self.assertEqual(record['notes'], 'Checked the certificate')
        self.assertIn('processed_at', record)
        self.assertIn('submitted_at', record)

    def test_stale_rejection_is_not_replayed(self):
        first, second, record, calls = self.run_import(self.entry(), ValueError('Stale decision'))
        self.assertEqual(first['rejected'], 1)
        self.assertEqual(record['reason'], 'Stale decision')
        self.assertEqual(calls, 1)

    def test_current_evidence_must_match(self):
        data = self.entry()
        row = {'key': 'insurance', 'value': 'changed', 'evidence_url': None, 'note': 'Required'}
        with self.assertRaisesRegex(ValueError, 'Stale'):
            inbox.check_current('profile', data, row)

    def test_profile_apply_uses_existing_audit_function_and_reviewer(self):
        row = {'key': 'insurance', 'value': None, 'evidence_url': None, 'note': 'Required'}
        entry = self.entry()
        conn = MagicMock()
        conn.execute.return_value.mappings.return_value.first.return_value = row
        engine = MagicMock()
        engine.connect.return_value.execution_options.return_value.__enter__.return_value = conn
        with patch.object(inbox, 'set_profile') as save:
            inbox.apply(engine, entry)
        self.assertEqual(save.call_args.args[1:], ('insurance', 'current',
                         'https://example.gov.au/proof', 'staff-test'))
        queries = [str(c.args[0]) for c in conn.execute.call_args_list]
        self.assertTrue(any('FOR UPDATE' in q for q in queries))
        self.assertTrue(any('SET note=' in q for q in queries))

    def test_snapshot_fingerprint_matches_export_cell_types(self):
        from decimal import Decimal
        row = dict.fromkeys(wire.PANEL_COLUMNS)
        row.update(son_id='SON1', spend_through_panel_12m=Decimal('123.00'),
                   marked_relevant=True, needs_manual_check=True)
        sqlite_row = row | {'spend_through_panel_12m': 123.0, 'marked_relevant': 1, 'needs_manual_check': 1}
        self.assertEqual(wire.fingerprint('panel', row), wire.fingerprint('panel', sqlite_row))

    def test_invalid_approval_and_changed_register_cannot_apply(self):
        row = dict.fromkeys(wire.BUYER_COLUMNS)
        row.update(buyer_key='buyer', review_status='unreviewed', register_context_stale=False,
                   candidate_ids=['gov:one'], candidate_evidence=[], snapshots=[])
        entry = dict(kind='buyer', expected=wire.fingerprint('buyer', row), decision='approved',
                     organisation_id='gov:other')
        with self.assertRaisesRegex(ValueError, 'current candidate'):
            inbox.check_current('buyer', entry, row)
        row['register_context_stale'] = True
        entry['expected'] = wire.fingerprint('buyer', row)
        with self.assertRaisesRegex(ValueError, 'refresh proposals'):
            inbox.check_current('buyer', entry, row)

    def test_crash_intent_never_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ledger = root / 'ledger'
            ledger.mkdir()
            entry = self.entry()
            (root / '001.jsonl').write_text(json.dumps(entry))
            (ledger / (entry['id'] + '.json')).write_text(json.dumps(dict(entry, status='applying')))
            engine = Mock()
            engine.connect.return_value = nullcontext(Mock())
            with patch.object(inbox, 'apply') as apply:
                result = inbox.import_inbox(engine, root, ledger)
                apply.assert_not_called()
                self.assertEqual(result['rejected'], 1)
            record = json.loads((ledger / (entry['id'] + '.json')).read_text())
            self.assertIn('reconcile', record['reason'])

    def test_protocol_unknown_fields_controls_and_enums(self):
        entry = self.entry()
        data = {k: v for k, v in entry.items() if k not in ('id', 'reviewer', 'submitted_at')}
        wire.validate(data)
        for change in ({'reviewer': 'spoof'}, {'evidence_url': 'http://example.gov.au'},
                       {'notes': 'bad\ntext'}, {'kind': []}, {'value': True}):
            with self.assertRaises(ValueError):
                wire.validate(data | change)

    def test_cli_routes_inbox_without_migrations(self):
        from market_intelligence import __main__ as cli
        with patch.object(cli, 'engine', return_value=Mock()), patch.object(inbox, 'import_inbox', return_value={'applied': 0}) as apply:
            self.assertEqual(cli.main(['review-inbox', '--inbox', 'queue', '--ledger', 'ledger']), 0)
            self.assertEqual(apply.call_args.args[1:], ('queue', 'ledger'))

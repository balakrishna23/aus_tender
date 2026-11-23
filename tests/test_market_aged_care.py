"""Synthetic workbook tests; private retained rows must never be fixtures."""
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import openpyxl
from market_intelligence import aged_care as aged
from market_intelligence.parsers import SourceError
from test_market_health_acquisition import Publisher, WORKBOOK
from market_intelligence import acquire_health

TITLE = 'star-ratings-quarterly-data-extract-october-2026'


def synthetic_rows():
    row = dict.fromkeys(aged.DETAILED_HEADERS)
    row.update(dict(zip(aged.BASE, ['October 2026', 'Synthetic Home', 'Synthetic Organisation Ltd',
        'Testville', 'Not for Profit', 'Test region', 'NSW', 'Metropolitan', 'MM1', 'Small'])))
    row.update({h: 4 for h in aged.RATINGS})
    row.update(dict(zip(aged.MINUTES, [45, 40, 200, 190])))
    row[aged.DATES[0]] = datetime(2026, 10, 1)
    return [row]


def workbook_bytes(rows=None, mutate=None):
    rows = rows or synthetic_rows()
    workbook = openpyxl.Workbook()
    workbook.active.title = 'Notes'
    workbook['Notes'].append(['Star Ratings quarterly data extract'])
    for sheet, headers in [('Star Ratings', aged.STAR_HEADERS), ('Detailed data', aged.DETAILED_HEADERS)]:
        ws = workbook.create_sheet(sheet)
        ws.append(headers)
        for row in rows:
            ws.append([row[h] for h in headers])
    if mutate:
        mutate(workbook)
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class ParserTests(unittest.TestCase):
    def parse(self, payload):
        return aged.parse_aged_care(io.BytesIO(payload), TITLE)

    def test_nulls_selected_fields_and_distinct_duplicate_identity(self):
        row = synthetic_rows()[0]
        row[aged.RATINGS[0]] = None
        second = dict(row)
        second[aged.MINUTES[1]] = 42
        parsed = self.parse(workbook_bytes([row, second, row]))
        self.assertEqual(len({r['row_identity'] for r in parsed['rows']}), 3)
        self.assertEqual(parsed['metrics']['duplicate_full_key_groups'], 1)
        self.assertEqual(parsed['metrics']['duplicate_full_key_extra_rows'], 2)
        self.assertEqual(parsed['metrics']['blank_ratings'][aged.RATINGS[0]], 3)
        self.assertEqual(parsed['rows'][0]['content_sha256'], parsed['rows'][2]['content_sha256'])
        self.assertNotEqual(parsed['rows'][0]['content_sha256'], parsed['rows'][1]['content_sha256'])
        self.assertIsNone(parsed['rows'][0]['payload'][aged.RATINGS[0]])
        self.assertEqual(parsed['rows'][0]['payload'][aged.DATES[0]], '2026-10-01')

    def test_headers_row_accounting_shared_consistency_and_period(self):
        mutations = [lambda w: setattr(w['Star Ratings']['A1'], 'value', 'Unexpected'),
                     lambda w: w['Detailed data'].delete_rows(2),
                     lambda w: setattr(w['Detailed data']['K2'], 'value', 5),
                     lambda w: setattr(w['Star Ratings']['A2'], 'value', 'July 2026'),
                     lambda w: w['Star Ratings'].append([None] * 15)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(SourceError):
                self.parse(workbook_bytes(mutate=mutation))

    def test_domain_and_contact_data_fail_without_disclosure(self):
        for field, value in [(aged.RATINGS[0], 0), (aged.RATINGS[1], True), ('Size', 'Huge'),
                ('MMM Code', 'MM7'), (aged.MINUTES[0], -1), (aged.DATES[0], 'yesterday'),
                ('[RE] Food - Always', 101), ('[C] Decision type', 'Unrecognised'),
                ('Provider Name', 'test@example.invalid'), ('Service Name', '02 1234 5678'),
                ('[C] Compliance Rating Reason', '1800 123 456')]:
            row = synthetic_rows()[0]
            row[field] = value
            with self.subTest(field=field), self.assertRaises(SourceError) as caught:
                self.parse(workbook_bytes([row]))
            self.assertNotIn(str(value), str(caught.exception))

    def test_reordered_shared_rows_match_by_multiset(self):
        second = dict(synthetic_rows()[0], **{'Service Name': 'Other Synthetic Home'})
        def reverse(w):
            sheet = w['Star Ratings']
            a, b = [c.value for c in sheet[2]], [c.value for c in sheet[3]]
            for i, (x, y) in enumerate(zip(b, a), 1):
                sheet.cell(2, i, x)
                sheet.cell(3, i, y)
        self.assertEqual(self.parse(workbook_bytes(synthetic_rows() + [second], reverse))['metrics']['imported_rows'], 2)

    def test_verified_manifest_and_tampering(self):
        publisher = Publisher()
        publisher.routes[WORKBOOK] = workbook_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = acquire_health.acquire(tmp, publisher)
            path, meta = aged.verified_manifest(manifest)
            self.assertEqual(aged.parse_aged_care(path, TITLE)['metrics']['imported_rows'], 1)
            path.write_bytes(b'tampered')
            with self.assertRaisesRegex(SourceError, 'size mismatch'):
                aged.verified_manifest(manifest)

    def test_idempotent_import_does_not_write_or_repoint(self):
        publisher = Publisher()
        publisher.routes[WORKBOOK] = workbook_bytes()
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        connection.execute.return_value.scalar.return_value = 'existing-id'
        with tempfile.TemporaryDirectory() as tmp:
            manifest = acquire_health.acquire(tmp, publisher)
            result = aged.import_aged_care(engine, manifest)
        self.assertEqual(result['status'], 'unchanged')
        self.assertEqual(connection.execute.call_count, 2)

    def test_invalid_source_never_opens_database_transaction(self):
        publisher = Publisher()
        publisher.routes[WORKBOOK] = workbook_bytes(mutate=lambda w: setattr(w['Detailed data']['K2'], 'value', 2))
        engine = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = acquire_health.acquire(tmp, publisher)
            with self.assertRaises(SourceError):
                aged.import_aged_care(engine, manifest)
        engine.begin.assert_not_called()

    def test_failed_row_insert_exits_transaction_before_pointer_update(self):
        publisher = Publisher()
        publisher.routes[WORKBOOK] = workbook_bytes()
        engine = MagicMock()
        connection = engine.begin.return_value.__enter__.return_value
        lookup = MagicMock()
        lookup.scalar.return_value = None
        connection.execute.side_effect = [None, lookup, None, RuntimeError('insert failed')]
        with tempfile.TemporaryDirectory() as tmp:
            manifest = acquire_health.acquire(tmp, publisher)
            with self.assertRaises(RuntimeError):
                aged.import_aged_care(engine, manifest)
        self.assertEqual(connection.execute.call_count, 4)
        self.assertIs(engine.begin.return_value.__exit__.call_args.args[0], RuntimeError)

    def test_report_wording_and_escaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = dict(rows=[dict(service_name='<synthetic>')], limitations=aged.LIMITATIONS)
            aged.write_aged_care_report(report, tmp)
            content = (Path(tmp) / 'aged_care.html').read_text(encoding='utf-8')
            self.assertIn('&lt;synthetic&gt;', content)
            self.assertIn('do not establish buying intent', content)


@unittest.skipUnless(os.environ.get('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Disposable PostgreSQL not configured')
class DatabaseTests(unittest.TestCase):
    def test_import_idempotence_nulls_shortfalls_and_immutable_evidence(self):
        from market_intelligence.db import engine, migrate
        from sqlalchemy import text
        db = engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if db.url.database != 'illoura_market_test':
            db.dispose()
            raise RuntimeError('Integration tests require the dedicated illoura_market_test database')
        migrate(db)
        publisher = Publisher()
        row = synthetic_rows()[0]
        row[aged.RATINGS[0]] = None
        publisher.routes[WORKBOOK] = workbook_bytes([row])
        try:
            with tempfile.TemporaryDirectory() as tmp:
                manifest = acquire_health.acquire(tmp, publisher)
                first = aged.import_aged_care(db, manifest)
                self.assertEqual(aged.import_aged_care(db, manifest)['status'], 'unchanged')
                with db.connect() as conn:
                    r = conn.execute(text('SELECT * FROM market.v_aged_care_quality')).mappings().one()
                    self.assertIsNone(r['overall_rating'])
                    self.assertEqual(r['rn_shortfall'], 5)
                    self.assertEqual(r['total_shortfall'], 10)
                with self.assertRaises(Exception), db.begin() as conn:
                    conn.execute(text('UPDATE market.aged_care_observations SET key_ordinal=2'))
                with db.connect() as conn:
                    self.assertEqual(str(conn.execute(text('SELECT snapshot_id FROM market.current_aged_care_snapshot')).scalar()), first['snapshot_id'])
        finally:
            db.dispose()

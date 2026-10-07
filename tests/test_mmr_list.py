"""NIAA MMR list: stored as received, matched to supplier ABNs, review decisions kept."""
import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mmr_list  # noqa: E402

CONTRACTS = [
    # cn_id, supplier, supplier_abn, family, value, end
    ('CN1', 'DXC TECHNOLOGY AUSTRALIA PTY LIMITED', '18 008 476 944', 'ICT services', 9_000_000, '2027-06-30'),
    ('CN2', 'DXC Technology Australia Pty Ltd', '18008476944', 'ICT services', 500_000, '2026-11-30'),
    ('CN3', 'DXC TECHNOLOGY AUSTRALIA PTY LIMITED', '18008476944', None, 100_000, '2024-01-01'),
    ('CN4', 'Jones Lang LaSalle (ACT) Pty Limited', '12345678901', None, 8_000_000, '2028-01-01'),
    ('CN5', 'AMES Australia', '98765432109', 'labour hire and recruitment', 50_000, '2027-01-01'),
    ('CN6', 'No Abn Supplier Pty Ltd', '', None, 1, '2027-01-01'),
]


class MmrListTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.workbook = self.write_list([('ACCENTURE AUSTRALIA PTY LTD', 17),
                                         ('DXC TECHNOLOGY AUSTRALIA PTY LIMITED', 5),
                                         ('JONES LANG LASALLE (ACT) PTY LIMITED', 5),
                                         ('AMES AUSTRALIA', 4), (None, None)])
        with closing(sqlite3.connect(self.dir / 'contract_api.db')) as db:
            db.execute('CREATE TABLE contracts (cn_id, supplier, supplier_abn, category_family, '
                       'current_value, current_end_date)')
            db.executemany('INSERT INTO contracts VALUES (?, ?, ?, ?, ?, ?)', CONTRACTS)
            db.commit()
        self.conn = mmr_list.connect()
        self.addCleanup(self.conn.close)

    def write_list(self, rows, name='list.xlsx'):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'Current MMR'
        sheet.append(['Organisation', 'Number of contracts'])
        for row in rows:
            sheet.append(row)
        path = self.dir / name
        book.save(path)
        return path

    def matches(self):
        return {(r['organisation'], r['supplier_abn']): dict(r)
                for r in self.conn.execute('SELECT * FROM mmr_matches')}

    def test_list_is_stored_as_received_and_never_overwritten(self):
        self.assertEqual(mmr_list.load(self.conn, self.workbook, '2026-10-07'), ('2026-10-07', 4))
        changed = self.write_list([('ACCENTURE AUSTRALIA PTY LTD', 99)], 'changed.xlsx')
        self.assertEqual(mmr_list.load(self.conn, changed, '2026-10-07'), ('2026-10-07', 0))
        row = self.conn.execute("SELECT contract_count, source_file, row_no FROM mmr_list "
                                "WHERE organisation LIKE 'ACCENTURE%'").fetchone()
        self.assertEqual(tuple(row), (17, 'list.xlsx', 2))

    def test_unexpected_workbook_is_refused(self):
        book = openpyxl.Workbook()
        book.active.append(['Supplier', 'Count'])
        book.save(self.dir / 'other.xlsx')
        with self.assertRaises(ValueError):
            mmr_list.load(self.conn, self.dir / 'other.xlsx', '2026-10-07')

    def test_names_link_to_one_clean_abn_with_contract_counts(self):
        mmr_list.load(self.conn, self.workbook, '2026-10-07')
        self.assertEqual(mmr_list.match(self.conn, today='2026-10-07'), (3, 4))
        dxc = self.matches()[('DXC TECHNOLOGY AUSTRALIA PTY LIMITED', '18008476944')]
        self.assertEqual((dxc['contracts'], dxc['ict_contracts'], dxc['active_contracts'],
                          dxc['active_over_threshold'], dxc['latest_end_date']), (3, 2, 2, 1, '2027-06-30'))
        self.assertEqual(dxc['active_value'], 9_500_000)
        self.assertEqual(dxc['match_method'], 'name')
        self.assertIn(('JONES LANG LASALLE (ACT) PTY LIMITED', '12345678901'), self.matches())
        self.assertEqual(self.matches()[('AMES AUSTRALIA', '98765432109')]['labour_hire_contracts'], 1)

    def test_review_survives_rematching(self):
        mmr_list.load(self.conn, self.workbook, '2026-10-07')
        mmr_list.match(self.conn, today='2026-10-07')
        review = self.dir / 'review.csv'
        mmr_list.export(self.conn, review)
        with open(review, newline='', encoding='utf-8-sig') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 4)  # three matched, Accenture unmatched with a blank ABN
        self.assertEqual([r['supplier_abn'] for r in rows if r['organisation'].startswith('ACCENTURE')], [''])
        for row in rows:
            if row['organisation'].startswith('DXC'):
                row['review'], row['note'] = 'Confirmed', 'checked on ABR'
        with open(review, 'w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        self.assertEqual(mmr_list.import_review(self.conn, review), 1)
        mmr_list.match(self.conn, today='2027-01-01')
        dxc = self.matches()[('DXC TECHNOLOGY AUSTRALIA PTY LIMITED', '18008476944')]
        self.assertEqual((dxc['review'], dxc['note'], dxc['active_contracts']), ('confirmed', 'checked on ABR', 1))

    def test_review_rejects_unknown_decisions(self):
        mmr_list.load(self.conn, self.workbook, '2026-10-07')
        mmr_list.match(self.conn, today='2026-10-07')
        review = self.dir / 'review.csv'
        with open(review, 'w', newline='', encoding='utf-8-sig') as handle:
            handle.write('organisation,supplier_abn,review\nAMES AUSTRALIA,98765432109,maybe\n')
        with self.assertRaises(ValueError):
            mmr_list.import_review(self.conn, review)

    def test_name_keys(self):
        self.assertEqual(mmr_list.name_keys('DXC Technology Australia Pty. Ltd.')[0], 'DXC TECHNOLOGY AUSTRALIA')
        self.assertEqual(mmr_list.name_keys('ERNST & YOUNG')[1], 'ERNST AND YOUNG')
        self.assertEqual(mmr_list.abn('18 008 476 944'), '18008476944')
        self.assertIsNone(mmr_list.abn('1800847694'))


if __name__ == '__main__':
    unittest.main()

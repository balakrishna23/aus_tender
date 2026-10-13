import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from openpyxl import Workbook
import buyict_sellers, company_coverage, contact_discovery, route_review, sync_approachable_routes


def candidate(kind, value, source='https://b.com.au/contact', purpose=None):
    return {'kind': kind, 'value': value, 'source_url': source,
            'purpose': purpose or contact_discovery.purpose(kind, value, 'b.com.au')}


class ChooseTests(unittest.TestCase):
    def test_partner_desk_beats_enquiries_beats_offices_beats_recruitment(self):
        found = [candidate('email', 'careers@b.com.au'), candidate('email', 'sydney@b.com.au'),
                 candidate('email', 'info@b.com.au'), candidate('email', 'partnerships@b.com.au'),
                 candidate('email', 'jane.citizen@b.com.au')]
        email, _ = route_review.choose(found)
        self.assertEqual(email['value'], 'partnerships@b.com.au')
        email, _ = route_review.choose(found[:3])
        self.assertEqual(email['value'], 'info@b.com.au')

    def test_named_people_and_foreign_numbers_are_never_proposed(self):
        email, phone = route_review.choose([candidate('email', 'jane.citizen@b.com.au'), candidate('phone', '+1 571 814 7801')])
        self.assertIsNone(email)
        self.assertIsNone(phone)

    def test_phone_on_contact_page_and_repeated_number_wins(self):
        found = [candidate('phone', '(02) 9000 1111', 'https://b.com.au/about'),
                 candidate('phone', '(02) 6230 0233', 'https://b.com.au/'),
                 candidate('phone', '02 6230 0233', 'https://b.com.au/contact')]
        _, phone = route_review.choose(found)
        self.assertEqual(phone['value'], '02 6230 0233')


class ImportTests(unittest.TestCase):
    def test_only_approved_rows_are_added_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'routes.json'
            source.write_text(json.dumps({'policy': 'p', 'records': []}), encoding='utf-8')
            with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
                db.executescript(company_coverage.SCHEMA + sync_approachable_routes.SCHEMA + buyict_sellers.SCHEMA
                                 + contact_discovery.SCHEMA)
                for abn in ('11111111111', '22222222222'):
                    db.execute('INSERT INTO suppliers (supplier_key, name, abn) VALUES (?, ?, ?)', ('ABN:' + abn, abn, abn))
                db.execute("""INSERT INTO contact_candidates (supplier_key, kind, value, checked_at_utc)
                    VALUES ('ABN:11111111111', 'email', 'info@one.com.au', '2026-10-13T10:00:00+00:00')""")
                db.commit()
            book = Workbook()
            sheet = book.active
            sheet.title = 'Review'
            sheet.append(route_review.COLUMNS)
            row = dict.fromkeys(route_review.COLUMNS, '')
            sheet.append(list({**row, 'Company': 'One', 'ABN': '11111111111', 'Proposed email': 'info@one.com.au',
                               'Email source page': 'https://one.com.au/contact', 'Proposed phone': '02 6230 0233',
                               'Decision': 'Approve', 'supplier_key': 'ABN:11111111111'}.values()))
            sheet.append(list({**row, 'Company': 'Two', 'Proposed email': 'info@two.com.au', 'Decision': 'reject',
                               'supplier_key': 'ABN:22222222222'}.values()))
            book.save(root / 'review.xlsx')
            with patch.object(sync_approachable_routes, 'SOURCE', source), \
                    patch.object(route_review.assistant, 'DB_DIR', root), patch('builtins.print'):
                self.assertEqual(route_review.import_approved(root / 'review.xlsx'), 1)
                self.assertEqual(route_review.import_approved(root / 'review.xlsx'), 0)
            records = json.loads(source.read_text(encoding='utf-8'))['records']
            with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
                routes = db.execute('SELECT supplier_key, preferred_value, secondary_value FROM approachable_routes').fetchall()
                status = dict(db.execute('SELECT supplier_key, contact_status FROM suppliers'))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['checked_at_utc'], '2026-10-13T10:00:00+00:00')
        self.assertEqual(routes, [('ABN:11111111111', 'info@one.com.au', '02 6230 0233')])
        self.assertEqual(status['ABN:11111111111'], 'reviewed public business route available')
        self.assertEqual(status['ABN:22222222222'], 'email/phone not established; web review pending')


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock

from database_manager import DatabaseManager
import listings_pipeline as lp


class NormaliserTests(unittest.TestCase):
    def test_phone_formats(self):
        self.assertEqual(lp.normalise_phone('(02) 9284 9600'), '+61 2 9284 9600')
        self.assertEqual(lp.normalise_phone('+61 2 6202 6958'), '+61 2 6202 6958')
        self.assertEqual(lp.normalise_phone('+61 (0)2 6202 6958'), '+61 2 6202 6958')
        self.assertEqual(lp.normalise_phone('0412 345 678'), '+61 412 345 678')
        self.assertEqual(lp.normalise_phone('1300651698'), '1300 651 698')
        self.assertEqual(lp.normalise_phone('0'), '')

    def test_extract_from_free_text(self):
        text = "Contact Jo Smith (Jo.Smith@Agency.gov.au) on 02 6123 4567 or 0412 345 678."
        self.assertEqual(lp.extract_emails(text), ['jo.smith@agency.gov.au'])
        self.assertEqual(lp.extract_phones(text), ['+61 2 6123 4567', '+61 412 345 678'])
        self.assertEqual(lp.extract_emails("No contact details reported."), [])

    def test_dates(self):
        self.assertEqual(lp.iso(lp.parse_date('8-Oct-2026 12:00 pm')), '2026-10-08')
        self.assertEqual(lp.iso(lp.parse_date('Friday, 02 October 2026 • 11:59pm, Canberra time')), '2026-10-02')
        self.assertEqual(lp.iso(lp.parse_date('Tue, 25 August 2026 2:00 pm')), '2026-08-25')
        self.assertIsNone(lp.parse_date('Quarter 4 2025/2026'))

    def test_states(self):
        self.assertEqual(lp.postcode_state('2601'), 'ACT')
        self.assertEqual(lp.postcode_state('2127'), 'NSW')
        self.assertEqual(lp.postcode_state('0810'), 'NT')
        self.assertEqual(lp.extract_states('ACT Canberra'), 'ACT')
        self.assertEqual(lp.extract_states('QLD, WA, ACT, VIC, NSW, NT, SA, TAS'), 'National')

    def test_sectors_use_word_boundaries(self):
        self.assertEqual(lp.classify_sector('DMP2: ICT Labour Hire'), 'Labour Hire & Recruitment')
        self.assertEqual(lp.classify_sector('Computer services'), 'ICT & Digital')
        self.assertEqual(lp.classify_sector('Environmental management'), 'Construction, Facilities & Logistics')
        self.assertEqual(lp.classify_sector('District office fit-out'), 'Other')

    def test_inbox_type(self):
        self.assertEqual(lp.inbox_type('sun.seol@defence.gov.au'), 'Personal')
        self.assertEqual(lp.inbox_type('tenders@finance.gov.au'), 'Generic')
        self.assertEqual(lp.inbox_type('casg-msd.austenderenquiries@defence.gov.au'), 'Generic')
        self.assertEqual(lp.inbox_type('dnpprocurement@environment.gov.au'), 'Generic')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = DatabaseManager(self.temp.name)

    def test_listings_and_contacts(self):
        self.db.initialize_database('aus_tender')
        with self.db.get_connection('aus_tender') as conn:
            conn.executemany(
                'INSERT INTO tenders (atm_id, service_name, agency, category, location, publish_date,'
                ' close_date_time, contact_name, contact_email, contact_phone, url) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                [('A1', 'Cloud platform', 'Dept A', '81110000 - Computer services', 'ACT Canberra',
                  '1-Sep-2026', '20-Oct-2026 2:00 pm', 'Jo Smith', 'Jo.Smith@a.gov.au', '02 6123 4567', 'https://x/1'),
                 ('A2', 'Old cleaning', 'Dept A', '76110000 - Cleaning services', 'NSW',
                  '1-Jan-2025', '1-Feb-2025 2:00 pm', 'Jo Smith', 'jo.smith@a.gov.au', '', 'https://x/2'),
                 ('A3', 'Phone only', 'Dept B', 'Software', 'VIC',
                  '1-Mar-2026', '1-Apr-2026', 'Desk', '', '0412 345 678', 'https://x/3')])
            conn.commit()
        listings = lp.build_listings(self.db, today=datetime(2026, 10, 2))
        self.assertEqual(len(listings), 3)
        first = listings[listings.listing_id == 'A1'].iloc[0]
        self.assertEqual((first.status, first.states, first.sector), ('Open', 'ACT', 'ICT & Digital'))
        self.assertEqual(first.contact_phone, '+61 2 6123 4567')

        contacts = lp.build_contacts(listings)
        self.assertEqual(len(contacts), 2)
        jo = contacts[contacts.email == 'jo.smith@a.gov.au'].iloc[0]
        self.assertEqual((jo.listings_total, jo.listings_live, jo.states), (2, 1, 'ACT, NSW'))
        self.assertEqual(jo.latest_url, 'https://x/1')
        self.assertIn('+61 412 345 678', contacts.phone.tolist())

        summary = lp.build_summary(listings, contacts)
        self.assertEqual(summary.iloc[0]['listings'], 3)

    def test_share_skips_existing(self):
        sheet = Mock()
        sheet.list_permissions.return_value = [{'emailAddress': 'Owner@gmail.com'}]
        shared = lp.share(sheet, ['owner@gmail.com', 'new@gmail.com'], 'reader')
        self.assertEqual(shared, ['new@gmail.com'])
        sheet.share.assert_called_once()
        self.assertEqual(sheet.share.call_args.args[0], 'new@gmail.com')


if __name__ == '__main__':
    unittest.main()

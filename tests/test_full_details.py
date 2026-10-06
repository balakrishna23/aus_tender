"""Every field a notice page shows is stored: new columns, the all_details copy, migrations."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scrapy.http import HtmlResponse, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))

from database_manager import DatabaseManager  # noqa: E402
from spiders.aus_contract import AusContractSpider  # noqa: E402
from spiders.austender_common import page_details  # noqa: E402
from spiders.buy_ict import BuyIctApiSpider  # noqa: E402
import nsw_tender_fetch  # noqa: E402

CN_URL = 'https://www.tenders.gov.au/Cn/Show/bfdaf184-c515-4ffd-aae2-2fae3c986544'
CN_PAGE = """<html><body><p class="lead">eDiscovery</p>
<div class="list-desc"><span>CN ID:</span><div class="list-desc-inner">CN4280573</div></div>
<div class="list-desc"><span>Publish Date:</span><div class="list-desc-inner">5-Oct-2026</div></div>
<div class="list-desc"><span>ATM ID:</span><div class="list-desc-inner">DTA-ICT-109</div></div>
<div class="list-desc"><span>Was an Australian business Engaged?:</span><div class="list-desc-inner">Yes</div></div>
<div class="list-desc"><span>Some New Field:</span><div class="list-desc-inner">shown on the site</div></div>
<div class="contact-long">
  <p class="contact-heading">Agency Details</p>
  <p><span>Contact Name:</span><br> PROCUREMENT OFFICER </p>
  <p><span>Phone:</span><br> 02 6123 4567 </p>
  <p><span>Email Address:</span><br><a href="mailto:p@fwo.gov.au" class="email">p@fwo.gov.au</a></p>
  <p><span>Branch:</span><br> Procurement Branch </p>
  <p><span>Division:</span><br> Corporate </p>
</div></body></html>"""

NSW_PAGE = """Moss Vale Sewer Upgrades
Description
Upgrade of sewer pump stations
Overview
Managed:
Wingecarribee Shire Council
Primary contact:
Kristian Draxl
procurement@wsc.nsw.gov.au
1300 680 888
Type:
RFT (Open)
Locations:
All regions
Covered procurement:
No
Estimated timeframe for delivery:
Publish date:
06-Oct-2026
Estimated decision date:
14-Dec-2026
Project goals
Upgrade existing sewer pump stations.
Rate:
Not a label, part of the goals
Scope
Refer to Volume 1.
Related files
Invitation to Tender.pdf (199 KB)
Requirements
Opportunity requirements
1.
Mandatory - Public Liability Insurance
Evaluation
Assessment Criteria
General assessment criteria that was used to evaluate this opportunity
Capability
Price
Notices
No notices have been posted for this opportunity.
Amendments
No amendments have been made to this opportunity
Log in to respond"""


def response(url, body):
    return HtmlResponse(url, body=body.encode(), encoding='utf-8', request=Request(url))


class AusTenderDetailTests(unittest.TestCase):
    def test_contract_notice_keeps_contact_phone_branch_division_and_every_field(self):
        item = next(AusContractSpider().parse_contract(response(CN_URL, CN_PAGE)))
        self.assertEqual(item['Contact Phone'], '02 6123 4567')
        self.assertEqual(item['Branch'], 'Procurement Branch')
        self.assertEqual(item['Division'], 'Corporate')
        self.assertEqual(item['ATM ID'], 'DTA-ICT-109')
        self.assertEqual(item['Australian Business Engaged'], 'Yes')
        # A field the spider has no column for is still kept.
        self.assertEqual(item['All Details']['Some New Field'], 'shown on the site')
        self.assertEqual(item['All Details']['Contact Name'], 'PROCUREMENT OFFICER')

    def test_atm_phone_read_once_although_the_page_repeats_the_contact_block(self):
        from spiders.aus_tender import AusTenderSpider
        block = ('<div class="contact-long"><p>Contract Officer</p><p><span><label for="PhoneNumber">Phone</label>:'
                 '</span> (02) 6166 4500 </p></div>')
        url = 'https://www.tenders.gov.au/Atm/Show/6499b340-05f0-4e2f-9b24-93d0a666d35c'
        item = next(AusTenderSpider().parse_tender(response(url, f'<html><body>{block}{block}</body></html>')))
        self.assertEqual(item['Contact Phone'], '(02) 6166 4500')

    def test_unlabelled_contact_line_is_the_name(self):
        page = ('<div class="contact-long"><p class="contact-heading">Contact Details</p>'
                '<p>People Panel Team</p><p><span>Phone:</span><br>6315 3080</p></div>')
        details = page_details(response(CN_URL, page))
        self.assertEqual(details, {'Contact Name': 'People Panel Team', 'Contact Phone': '6315 3080'})


class NswDetailTests(unittest.TestCase):
    def test_every_overview_field_and_section(self):
        info = nsw_tender_fetch._parse_opportunity_detail(NSW_PAGE)
        self.assertEqual((info['contact_name'], info['contact_email'], info['contact_phone']),
                         ('Kristian Draxl', 'procurement@wsc.nsw.gov.au', '1300 680 888'))
        self.assertEqual(info['publish_date'], '06-Oct-2026')
        self.assertEqual(info['procurement_type'], 'RFT (Open)')
        self.assertEqual(info['estimated_decision_date'], '14-Dec-2026')
        self.assertEqual(info['timeframe_for_delivery'], '')  # shown empty on the page
        self.assertIn('Rate:', info['project_goals'])  # a colon line inside a section is text
        self.assertEqual(info['assessment_criteria'], 'Capability\nPrice')
        self.assertEqual(info['requirements'], '1.\nMandatory - Public Liability Insurance')
        self.assertEqual((info['notices'], info['amendments']), ('', ''))
        details = json.loads(info['all_details'])
        self.assertEqual(details['Overview']['Managed'], 'Wingecarribee Shire Council')
        self.assertNotIn('Log in to respond', details)


class BuyIctDetailTests(unittest.TestCase):
    def test_keeps_content_and_drops_page_state(self):
        spider = BuyIctApiSpider()
        kept = spider._other_details({'key_duties': '<ul><li>Design</li></ul>', 'isLoggedIn': False,
                                      'user': {'name': 'guest'}, 'allCriteria': [{'label': 'Skills'}],
                                      'requirementsLabel': 'Requirements', 'empty': ''})
        self.assertEqual(kept, {'key_duties': '• Design', 'allCriteria': [{'label': 'Skills'}]})


class MigrationTests(unittest.TestCase):
    def test_new_schema_columns_are_added_to_an_old_database(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(sqlite3.connect(Path(folder) / 'aus_contract.db')) as conn:
                conn.execute('CREATE TABLE contracts (id INTEGER PRIMARY KEY, cn_id TEXT UNIQUE, '
                             'url TEXT UNIQUE, scraped_at TEXT, created_at TEXT, updated_at TEXT)')
            DatabaseManager(folder).initialize_database('aus_contract')
            with closing(sqlite3.connect(Path(folder) / 'aus_contract.db')) as conn:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(contracts)')}
            self.assertTrue({'contact_phone', 'branch', 'division', 'atm_id', 'all_details'} <= columns)

    def test_details_are_stored_as_json(self):
        with tempfile.TemporaryDirectory() as folder:
            dbm = DatabaseManager(folder)
            dbm.initialize_database('aus_contract')
            item = next(AusContractSpider().parse_contract(response(CN_URL, CN_PAGE)))
            self.assertEqual(dbm.insert_item('aus_contract', item), 'new')
            with dbm.get_connection('aus_contract') as conn:
                row = conn.execute('SELECT contact_phone, branch, all_details FROM contracts').fetchone()
            self.assertEqual(row[0], '02 6123 4567')
            self.assertEqual(json.loads(row[2])['Some New Field'], 'shown on the site')


if __name__ == '__main__':
    unittest.main()

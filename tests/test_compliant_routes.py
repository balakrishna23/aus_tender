"""AusTender spiders only request pages robots.txt allows: contract notices come from the API's list,
open tenders from the Current ATM list, panels from links on contract notices."""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from scrapy.http import HtmlResponse, Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import austender_api  # noqa: E402
from database_manager import DatabaseManager  # noqa: E402
from spiders.aus_contract import AusContractSpider  # noqa: E402
from spiders.aus_tender import AusTenderSpider  # noqa: E402
from spiders.standing_offer import StandingOfferSpider  # noqa: E402

DISALLOWED = ('/Search/', '/Reports/', '/Cn/List', '/Son/List')
PAGE = 'https://www.tenders.gov.au/Cn/Show/{}'


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.db = DatabaseManager()

    def assertAllowed(self, requests):
        for request in requests:
            self.assertFalse(any(part in request.url for part in DISALLOWED), request.url)


class PageLinkTests(unittest.TestCase):
    def test_page_link_from_the_item_id_names_that_very_notice(self):
        self.assertEqual(austender_api.page_url('86000000-bf3d9ab816df458b8e3f587e50eacfb8', 'CN1-8c74'),
                         PAGE.format('bf3d9ab8-16df-458b-8e3f-587e50eacfb8'))
        self.assertEqual(austender_api.page_url(None, 'CN4281101-1343968e3d42426ba8c3ac06dec47f16'),
                         PAGE.format('1343968e-3d42-426b-a8c3-ac06dec47f16'))
        self.assertIsNone(austender_api.page_url(None, 'CN1'))


class ContractTests(Base):
    def test_notices_come_from_the_api_and_stored_ones_are_skipped(self):
        with closing(austender_api.connect()) as api:
            api.executemany('INSERT INTO contract_versions (release_id, cn_id, notice_id, amendment_no, published_on, '
                            'page_url) VALUES (?, ?, ?, ?, ?, ?)',
                            [('r1', 'CN1', 'CN1', 0, '2026-10-06', PAGE.format('a')),
                             ('r2', 'CN1', 'CN1-A1', 1, '2026-10-07', PAGE.format('b')),
                             ('r3', 'CN2', 'CN2', 0, '2026-10-07', PAGE.format('c')),
                             ('r4', 'CN3', 'CN3', 0, '2026-01-01', PAGE.format('d'))])
            api.commit()
        self.db.initialize_database('aus_contract')
        self.db.insert_item('aus_contract', {'CN ID': 'CN2', 'URL': PAGE.format('c'), 'Agency': 'X'})
        spider = AusContractSpider(since='2026-10-01')
        requests = list(spider.start_requests())
        self.assertEqual([r.meta['notice_id'] for r in requests], ['CN1', 'CN1-A1'])
        self.assertAllowed(requests)

    def test_a_withdrawn_notice_page_yields_nothing(self):
        spider = AusContractSpider()
        spider.crawler = Mock()
        url = 'https://www.tenders.gov.au/System/NotFound?objectType=Cn'
        self.assertEqual(list(spider.parse_contract(HtmlResponse(url, body=b'<html></html>', request=Request(url)))), [])


class AtmTests(Base):
    def test_walks_the_current_list_and_rechecks_atms_that_left_it(self):
        self.db.initialize_database('aus_tender')
        for guid, closed in (('11111111-1111-1111-1111-111111111111', False),
                             ('22222222-2222-2222-2222-222222222222', True),
                             ('33333333-3333-3333-3333-333333333333', False)):
            path = 'ShowClosed' if closed else 'Show'
            self.db.insert_item('aus_tender', {'ATM ID': guid[:4], 'URL': f'https://www.tenders.gov.au/Atm/{path}/{guid}'})
        self.db.set_state('aus_tender', 'gone:33333333-3333-3333-3333-333333333333', '2026-10-01')
        spider = AusTenderSpider()
        spider.crawler = Mock()
        first = list(spider.start_requests())
        self.assertEqual([r.url for r in first], ['https://www.tenders.gov.au/Atm'])
        listing = HtmlResponse('https://www.tenders.gov.au/Atm', encoding='utf-8', request=first[0], body=b'''
            <div class="list-desc"><span>ATM ID:</span><div class="list-desc-inner">
            <a href="/Atm/Show/44444444-4444-4444-4444-444444444444">A1</a></div></div>
            of <strong>1</strong> records''')
        follow = list(spider.parse(listing))
        urls = sorted(r.url for r in follow)
        self.assertEqual(urls, ['https://www.tenders.gov.au/Atm/Show/11111111-1111-1111-1111-111111111111',
                                'https://www.tenders.gov.au/Atm/Show/44444444-4444-4444-4444-444444444444'])
        self.assertAllowed(first + follow)


class StandingOfferTests(Base):
    def test_new_panels_come_from_contract_notice_links(self):
        self.db.initialize_database('standing_offer')
        self.db.initialize_database('aus_contract')
        son = 'https://www.tenders.gov.au/Son/Show/90c61f3b-be27-3313-bcd6-93db777921c7'
        self.db.insert_item('aus_contract', {'CN ID': 'CN1', 'URL': PAGE.format('a'), 'SON URL': son})
        requests = list(StandingOfferSpider().start_requests())
        self.assertEqual([r.url for r in requests], [son])
        self.assertAllowed(requests)


if __name__ == '__main__':
    unittest.main()

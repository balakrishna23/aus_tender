import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scrapy
from scrapy.http import HtmlResponse, Response

from database_manager import DatabaseManager
from middlewares import RateLimitBackoffMiddleware
from spiders.austender_common import date_windows, notice_guid, parse_day
from spiders.aus_tender import AusTenderSpider
from spiders.buy_ict import BuyIctApiSpider

GUID_A = '11111111-2222-3333-4444-555555555555'
GUID_B = '66666666-7777-8888-9999-000000000000'
SYS_ID = 'a' * 32


class ChangeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = DatabaseManager(self.temp.name)

    def test_new_unchanged_updated(self):
        self.db.initialize_database('aus_tender')
        item = {'ATM ID': 'A1', 'Agency': 'One', 'URL': 'https://example.test/a'}
        self.assertEqual(self.db.insert_item('aus_tender', item), 'new')
        self.assertEqual(self.db.insert_item('aus_tender', item), 'unchanged')
        self.assertEqual(self.db.insert_item('aus_tender', {**item, 'Agency': 'Two'}), 'updated')
        self.assertEqual(self.db.get_stats('aus_tender')['total_records'], 1)

    def test_buyict_legacy_url_duplicates_are_merged(self):
        self.db.initialize_database('buy_ict')
        with self.db.get_connection('buy_ict') as conn:
            conn.execute('DROP INDEX idx_buy_ict_sys_id')
            conn.execute("INSERT INTO buy_ict_tenders (url, buyer, scraped_at, sys_id) VALUES (?, 'Old buyer', '2025-01-01', NULL)",
                         (f'https://www.buyict.gov.au/sp?id=opportunity_details&table=u_lh_procurement&sys_id={SYS_ID}&entry=opp_page',))
            conn.execute("INSERT INTO buy_ict_tenders (url, title, scraped_at, sys_id) VALUES (?, 'Title', '2026-01-01', NULL)",
                         (f'https://www.buyict.gov.au/public?id=opportunity_details&table=u_lh_procurement&sys_id={SYS_ID}&entry=opp_page',))
            conn.commit()
        self.db.initialize_database('buy_ict')
        with self.db.get_connection('buy_ict') as conn:
            rows = [dict(r) for r in conn.execute('SELECT sys_id, title, buyer, url FROM buy_ict_tenders')]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['sys_id'], rows[0]['title'], rows[0]['buyer']), (SYS_ID, 'Title', 'Old buyer'))
        self.assertIn('/public?id=', rows[0]['url'])

    def test_crawl_state(self):
        self.db.initialize_database('aus_contract')
        self.assertIsNone(self.db.get_state('aus_contract', 'covered_through'))
        self.db.set_state('aus_contract', 'covered_through', '2026-10-01')
        self.db.set_state('aus_contract', 'covered_through', '2026-10-02')
        self.assertEqual(self.db.get_state('aus_contract', 'covered_through'), '2026-10-02')


class ListingDecisionTests(unittest.TestCase):
    def test_date_helpers(self):
        self.assertEqual(parse_day('2-Oct-2026 2:00 pm'), date(2026, 10, 2))
        self.assertEqual(notice_guid(f'https://x/Atm/ShowClosed/{GUID_A.upper()}?PreviewMode=False'), GUID_A)
        windows = list(date_windows(date(2026, 1, 1), date(2026, 1, 15), 7))
        self.assertEqual(windows, [(date(2026, 1, 1), date(2026, 1, 7)), (date(2026, 1, 8), date(2026, 1, 14)),
                                   (date(2026, 1, 15), date(2026, 1, 15))])

    def _spider(self, stored):
        with patch('spiders.austender_common.DatabaseManager'):
            spider = AusTenderSpider()
        spider.crawler = SimpleNamespace(stats=Mock())
        spider.stored_urls = stored
        return spider

    def _listing(self, kind, total, guids):
        body = f'Showing 1-15 of <strong>{total}</strong> records' + ''.join(
            f'<div class="list-desc"><span>ATM ID:</span><div class="list-desc-inner">'
            f'<a href="/Atm/Show/{g}">ID</a></div></div>' for g in guids)
        request = scrapy.Request('https://example.test/list', meta={'listing_kind': kind, 'window': kind})
        return HtmlResponse(request.url, body=body.encode(), encoding='utf-8', request=request)

    def test_closed_notice_already_saved_closed_is_skipped(self):
        spider = self._spider({GUID_A: f'https://x/Atm/ShowClosed/{GUID_A}', GUID_B: f'https://x/Atm/Show/{GUID_B}'})
        requests = list(spider.parse(self._listing('closed', 2, [GUID_A, GUID_B])))
        # GUID_B was last saved while open, so its final closed copy is fetched once.
        self.assertEqual([notice_guid(r.url) for r in requests], [GUID_B])

    def test_open_notices_are_always_refreshed(self):
        spider = self._spider({GUID_A: f'https://x/Atm/Show/{GUID_A}'})
        self.assertEqual(len(list(spider.parse(self._listing('published', 1, [GUID_A])))), 1)

    def test_coverage_gap_is_a_failure(self):
        spider = self._spider({})
        list(spider.parse(self._listing('closed', 3, [GUID_A, GUID_B])))
        with self.assertLogs('aus_tender', level='ERROR'):
            spider.closed('finished')
        spider.crawler.stats.inc_value.assert_any_call('crawl/coverage_failed')


class BuyIctDecisionTests(unittest.TestCase):
    def test_only_open_or_unfinalised_opportunities_are_fetched(self):
        with patch('spiders.buy_ict.DatabaseManager'):
            spider = BuyIctApiSpider()
        spider.stored = {'closed': ('closed', False), 'was_open': ('open', True),
                         'legacy_final': (None, False), 'legacy_open': (None, True)}
        self.assertFalse(spider._needs_detail('closed', closed=True))
        self.assertTrue(spider._needs_detail('closed', closed=False))
        self.assertTrue(spider._needs_detail('was_open', closed=True))
        self.assertFalse(spider._needs_detail('legacy_final', closed=True))
        self.assertTrue(spider._needs_detail('legacy_open', closed=True))
        self.assertTrue(spider._needs_detail('unknown', closed=True))


class RateLimitTests(unittest.TestCase):
    def test_429_slows_slot_and_retries(self):
        slot = SimpleNamespace(delay=1.0)
        crawler = SimpleNamespace(settings=scrapy.settings.Settings(), stats=Mock(),
                                  engine=SimpleNamespace(downloader=SimpleNamespace(slots={'example.test': slot})))
        middleware = RateLimitBackoffMiddleware(crawler)
        spider = SimpleNamespace(logger=Mock(), crawler=crawler, name='test')
        request = scrapy.Request('https://example.test/a')
        response = Response(request.url, status=429, headers={'Retry-After': '12'}, request=request)
        with patch('middlewares.get_retry_request', return_value=request.replace(dont_filter=True)) as retry:
            result = middleware.process_response(request, response, spider)
        self.assertIsInstance(result, scrapy.Request)
        self.assertEqual(slot.delay, 12.0)
        self.assertEqual(retry.call_args.kwargs['max_retry_times'], 8)
        ok = Response(request.url, status=200, request=request)
        self.assertIs(middleware.process_response(request, ok, spider), ok)


if __name__ == '__main__':
    unittest.main()


class RecentlyUpdatedContractTests(unittest.TestCase):
    """Amendments to older contracts are only found via the newest-first 'Last Updated' list."""

    def _spider(self, stored):
        from spiders.aus_contract import AusContractSpider
        with patch('spiders.austender_common.DatabaseManager'):
            spider = AusContractSpider()
        spider.crawler = SimpleNamespace(stats=Mock())
        spider.stored_urls = {g: f'https://x/Cn/Show/{g}' for g in stored}
        return spider

    def _page(self, guids, page_no=1, quiet=0, more=True):
        body = ''.join(f'<div class="list-desc"><span>CN ID:</span><div class="list-desc-inner">'
                       f'<a href="/Cn/Show/{g}">CN</a></div></div>' for g in guids)
        if more:
            body += f'<ul><li class="next"><a href="?OrderBy=Last+Updated&page={page_no + 1}">Next</a></li></ul>'
        request = scrapy.Request('https://www.tenders.gov.au/Search/CnAdvancedSearch?OrderBy=Last+Updated',
                                 meta={'listing_kind': 'updated', 'page_no': page_no, 'quiet_pages': quiet})
        return HtmlResponse(request.url, body=body.encode(), encoding='utf-8', request=request)

    def test_only_unknown_notices_fetched_and_walk_continues(self):
        spider = self._spider([GUID_A])
        out = list(spider.parse_recent(self._page([GUID_A, GUID_B])))
        details = [r for r in out if '/Cn/Show/' in r.url]
        pages = [r for r in out if 'page=' in r.url]
        self.assertEqual([notice_guid(r.url) for r in details], [GUID_B])
        self.assertEqual(pages[0].meta['quiet_pages'], 0)
        self.assertEqual(pages[0].meta['page_no'], 2)

    def test_stops_after_quiet_pages(self):
        spider = self._spider([GUID_A, GUID_B])
        out = list(spider.parse_recent(self._page([GUID_A, GUID_B], page_no=4, quiet=1)))
        self.assertEqual([r.meta['quiet_pages'] for r in out], [2])  # second quiet page: keep going
        out = list(spider.parse_recent(self._page([GUID_A, GUID_B], page_no=5, quiet=2)))
        self.assertEqual(out, [])  # third quiet page in a row: stop

    def test_page_limit_is_respected(self):
        spider = self._spider([])
        with self.assertLogs('aus_contract', level='WARNING'):
            out = list(spider.parse_recent(self._page([GUID_A], page_no=spider.MAX_RECENT_PAGES)))
        self.assertEqual([notice_guid(r.url) for r in out], [GUID_A])

    def test_recent_list_does_not_count_toward_window_coverage(self):
        spider = self._spider([])
        list(spider.parse_recent(self._page([GUID_A, GUID_B])))
        self.assertEqual(spider.window_seen, {})
        self.assertEqual(spider.window_totals, {})

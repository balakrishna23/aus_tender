"""Health canary, durable publication retries and explicitly empty ATM listings.

All database operations use an in-memory fake and all responses are local HTML.
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapy import Request
from scrapy.http import HtmlResponse
from spiders.aus_tender import AusTenderSpider
from test_collectors_unpublished import spider_with, URL


class Stats:
    def __init__(self):
        self.values = {}

    def get_stats(self):
        return self.values.copy()

    def set_value(self, key, value):
        self.values[key] = value

    def inc_value(self, key):
        self.values[key] = self.values.get(key, 0) + 1


def response(request, body='', url=None, status=200):
    return HtmlResponse(url or request.url, request=request, body=body.encode(),
                        encoding='utf-8', status=status)


def contract_html(notice='CN-STORED'):
    return ''.join('<div class="list-desc"><span>{}:</span>'
                   '<div class="list-desc-inner">{}</div></div>'.format(k, v)
                   for k, v in [('CN ID', notice), ('Agency', 'Test agency')])


def contract_spider():
    spider = spider_with({})
    spider.today = date(2026, 11, 13)
    spider.crawler.stats = Stats()
    return spider


def start(spider, notices):
    with patch.object(spider, '_api_notices', return_value=notices):
        return list(spider.start_requests())


class CanaryTests(unittest.TestCase):
    def batch(self, count, canary=True, canary_body=None, canary_notfound=False):
        spider = contract_spider()
        if canary:
            spider.db_manager.conn.execute('INSERT INTO contracts (cn_id, url) VALUES (?, ?)',
                                           ('CN-STORED', URL.format('stored')))
        requests = start(spider, [('CN' + str(i), URL.format(str(i))) for i in range(count)])
        for request in requests:
            if request.meta.get('contract_canary'):
                result = list(spider.parse_contract(response(
                    request, contract_html() if canary_body is None else canary_body,
                    url='https://www.tenders.gov.au/System/NotFound' if canary_notfound else None)))
                self.assertEqual(result, [])  # Canary never reaches any pipeline.
            else:
                self.assertEqual(list(spider.parse_contract(response(
                    request, url='https://www.tenders.gov.au/System/NotFound'))), [])
        spider.closed('finished')
        return spider

    def test_13_november_canary_ok(self):
        spider = self.batch(13)
        self.assertEqual(spider.crawler.stats.values['crawl/nothing_due'], 1)
        self.assertEqual(spider.crawler.stats.values['contract/page_not_found'], 13)
        self.assertEqual(spider.crawler.stats.values['contract/canary_ok'], 1)

    def test_40_unpublished_with_healthy_canary(self):
        self.assertEqual(self.batch(40).crawler.stats.values['crawl/nothing_due'], 1)

    def test_canary_notfound_fails_without_polluting_notice_count(self):
        stats = self.batch(13, canary_notfound=True).crawler.stats.values
        self.assertNotIn('crawl/nothing_due', stats)
        self.assertEqual(stats['contract/page_not_found'], 13)
        self.assertEqual(stats['contract/canary_ok'], 0)

    def test_canary_wrong_identity_fails(self):
        self.assertNotIn('crawl/nothing_due', self.batch(
            13, canary_body=contract_html('CN-WRONG')).crawler.stats.values)

    def test_canary_empty_html_fails(self):
        self.assertNotIn('crawl/nothing_due', self.batch(13, canary_body='').crawler.stats.values)

    def test_no_canary_26_fails(self):
        self.assertNotIn('crawl/nothing_due', self.batch(26, canary=False).crawler.stats.values)

    def test_canary_transport_failure_fails(self):
        spider = contract_spider()
        spider.canary_requested = True
        spider.requested_notices = 13
        spider.crawler.stats.values.update({'contract/page_not_found': 13, 'contract/canary_ok': 0})
        spider.closed('finished')
        self.assertNotIn('crawl/nothing_due', spider.crawler.stats.values)

    def test_canary_uses_newest_stored_row(self):
        spider = contract_spider()
        spider.db_manager.conn.executemany('INSERT INTO contracts (cn_id, url) VALUES (?, ?)',
            [('CN-OLD', URL.format('old')), ('CN-STORED', URL.format('stored')), ('CN-NOURL', '')])
        requests = start(spider, [('CN1', URL.format('new'))])
        self.assertEqual(len(requests), 2)
        self.assertTrue(requests[0].meta['contract_canary'])
        self.assertEqual(requests[0].meta['notice_id'], 'CN-STORED')
        self.assertEqual(spider.requested_notices, 1)


class PendingTests(unittest.TestCase):
    def seed(self, spider, age=4):
        spider._pending_schema(spider.db_manager.conn)
        first = (spider.today - timedelta(days=age)).isoformat()
        spider.db_manager.conn.execute('INSERT INTO pending_notices VALUES (?, ?, ?, ?, ?)',
                                      ('CN-PENDING', URL.format('pending'), first, first, 1))

    def test_retry_outside_window_and_deduplicate(self):
        spider = contract_spider()
        self.seed(spider)
        requests = start(spider, [])
        self.assertEqual([r.meta['notice_id'] for r in requests], ['CN-PENDING'])
        self.assertEqual(spider.db_manager.conn.execute(
            'SELECT attempts, last_checked FROM pending_notices').fetchone(), (2, '2026-11-13'))
        requests = start(spider, [('CN-PENDING', URL.format('pending'))])
        self.assertEqual(len(requests), 1)

    def test_notfound_persists_original_url_and_does_not_block_coverage(self):
        spider = contract_spider()
        request = start(spider, [('CN-PENDING', URL.format('pending'))])[0]
        list(spider.parse_contract(response(request, url='https://www.tenders.gov.au/System/NotFound')))
        spider.closed('finished')
        self.assertEqual(spider.db_manager.conn.execute('SELECT * FROM pending_notices').fetchone(),
                         ('CN-PENDING', URL.format('pending'), '2026-11-13', '2026-11-13', 1))
        spider.db_manager.set_state.assert_called_once_with('aus_contract', 'covered_through', '2026-11-13')

    def test_remove_only_after_collection_is_stored(self):
        spider = contract_spider()
        self.seed(spider)
        request = start(spider, [])[0]
        items = list(spider.parse_contract(response(request, contract_html('CN-PENDING'))))
        self.assertEqual(items[0]['CN ID'], 'CN-PENDING')
        self.assertEqual(spider.db_manager.conn.execute('SELECT count(*) FROM pending_notices').fetchone()[0], 1)
        # Model the successful pipeline save before spider.closed.
        spider.db_manager.conn.execute('INSERT INTO contracts (cn_id, url) VALUES (?, ?)',
                                      ('CN-PENDING', request.url))
        spider.closed('finished')
        self.assertEqual(spider.db_manager.conn.execute('SELECT count(*) FROM pending_notices').fetchone()[0], 0)

    def test_failed_save_remains_pending(self):
        spider = contract_spider()
        self.seed(spider)
        start(spider, [])
        spider.crawler.stats.values['database/items_failed'] = 1
        spider.closed('finished')
        self.assertEqual(spider.db_manager.conn.execute('SELECT count(*) FROM pending_notices').fetchone()[0], 1)

    def test_30_day_giveup_is_warning_and_not_restarted_by_api(self):
        spider = contract_spider()
        self.seed(spider, age=30)
        with self.assertLogs(spider.name, level='WARNING') as logs:
            requests = start(spider, [('CN-PENDING', URL.format('pending'))])
        self.assertEqual(requests, [])
        self.assertIn('30 days', logs.output[0])
        self.assertEqual(spider.crawler.stats.values['crawl/nothing_due'], 1)
        self.assertEqual(spider.db_manager.conn.execute('SELECT attempts FROM pending_notices').fetchone()[0], 1)

    def test_29_day_notice_still_retried(self):
        spider = contract_spider()
        self.seed(spider, age=29)
        self.assertEqual(len(start(spider, [])), 1)


class EmptyATMTests(unittest.TestCase):
    def spider(self):
        with patch('spiders.austender_common.DatabaseManager'):
            spider = AusTenderSpider()
        spider.crawler = SimpleNamespace(stats=Stats())
        return spider

    def listing(self, spider, body, window='empty'):
        request = spider.listing_request('https://www.tenders.gov.au/Atm', 'default', window)
        return list(spider.parse(response(request, body)))

    def test_verified_empty_marker(self):
        spider = self.spider()
        self.assertEqual(self.listing(spider, 'There are no results that match your selection'), [])
        spider.closed('finished')
        self.assertEqual(spider.crawler.stats.values['crawl/nothing_due'], 1)

    def test_verified_zero_total(self):
        spider = self.spider()
        self.listing(spider, 'of <strong>0</strong> records')
        spider.closed('finished')
        self.assertEqual(spider.crawler.stats.values['crawl/nothing_due'], 1)

    def test_no_links_without_empty_marker_still_fails(self):
        spider = self.spider()
        self.listing(spider, '<html>unexpected layout</html>')
        spider.closed('finished')
        self.assertEqual(spider.crawler.stats.values['crawl/extraction_failed'], 1)
        self.assertNotIn('crawl/nothing_due', spider.crawler.stats.values)

    def test_other_failed_window_prevents_nothing_due(self):
        spider = self.spider()
        self.listing(spider, 'of <strong>0</strong> records')
        self.listing(spider, 'unexpected layout', 'broken')
        spider.closed('finished')
        self.assertNotIn('crawl/nothing_due', spider.crawler.stats.values)

    def test_incomplete_or_failed_crawl_does_not_mark_empty(self):
        for reason, stats in [('shutdown', {}), ('finished', {'database/items_failed': 1}),
                              ('finished', {'item_scraped_count': 1}),
                              ('finished', {'retry/max_reached': 1}),
                              ('finished', {'downloader/response_status_count/500': 1}),
                              ('finished', {'crawl/extraction_failed': 1})]:
            with self.subTest(reason=reason, stats=stats):
                spider = self.spider()
                self.listing(spider, 'of <strong>0</strong> records')
                spider.crawler.stats.values.update(stats)
                spider.closed(reason)
                self.assertNotIn('crawl/nothing_due', spider.crawler.stats.values)


if __name__ == '__main__':
    unittest.main()

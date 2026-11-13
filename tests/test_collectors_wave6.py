"""Wave 6 guards: inline fixtures, mocked browsers/DB managers, in-memory SQLite only."""
import json
import logging
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from scrapy import Request
from scrapy.http import HtmlResponse, TextResponse
import nsw_tender_fetch as nsw
from pipelines import DatabasePipeline
from spiders.planned_procurement import PlannedProcurementSpider
from spiders.act_tender import ActTenderSpider, EXTRACT_ROWS_JS
from spiders.buy_ict import BuyIctApiSpider


class Stats:
    def __init__(self):
        self.values = {}
    def inc_value(self, key):
        self.values[key] = self.values.get(key, 0) + 1
    def set_value(self, key, value):
        self.values[key] = value


def attach(spider):
    spider.crawler = SimpleNamespace(stats=Stats())
    return spider


def failed(spider):
    return any(value for key, value in spider.crawler.stats.values.items() if key.endswith('_failed'))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.spider = attach(PlannedProcurementSpider())
    def parse(self, body, plan='one', query=''):
        url = 'https://www.tenders.gov.au/App/Show/' + plan + query
        return list(self.spider.parse_plan(HtmlResponse(url, body=body.encode(), encoding='utf-8')))
    def finish(self):
        self.spider.closed('finished')
    def test_normal_and_explicit_empty(self):
        self.parse('<div class="row"><p class="lead">A plan</p><div id="pp-1"></div></div>')
        self.parse('<p>No planned procurements available.</p>', 'two')
        self.finish()
        self.assertFalse(failed(self.spider))
    def test_one_empty_out_of_two_only_warns(self):
        self.parse('<p>Changed markup</p>')
        self.parse('<p>No planned procurements found</p>', 'two')
        self.finish()
        self.assertFalse(failed(self.spider))
        self.assertEqual(self.spider.crawler.stats.values['crawl/planned_unexplained_empty_plans'], 1)
    def test_majority_empty_fails(self):
        self.parse('<p>Changed markup</p>')
        self.finish()
        self.assertEqual(self.spider.crawler.stats.values['crawl/planned_extraction_failed'], 1)
    def test_pagination_total_warns_and_preserves_next(self):
        result = self.parse('<p>2 planned procurements</p><div class="row"><p class="lead">A</p>'
                            '<div id="pp-1"></div></div><li class="next"><a href="?page=2">Next</a></li>')
        self.assertIsInstance(result[-1], Request)
        self.finish()
        self.assertEqual(self.spider.crawler.stats.values['crawl/planned_pagination_warnings'], 1)
        self.assertFalse(failed(self.spider))
    def test_pages_count_once_and_reconcile(self):
        for i in (1, 2):
            self.parse(f'<p>2 planned procurements</p><div class="row"><p class="lead">A</p>'
                       f'<div id="pp-{i}"></div></div>', query=f'?page={i}')
        self.finish()
        self.assertEqual(len(self.spider.plan_checks), 1)
        self.assertFalse(failed(self.spider))
        self.assertNotIn('crawl/planned_pagination_warnings', self.spider.crawler.stats.values)


class ActTests(unittest.IsolatedAsyncioTestCase):
    async def parse_page(self, spider, total, refs, next_href=None, kind='open'):
        page = AsyncMock()
        page.inner_text.return_value = total
        rows = [dict(cell0=ref+'\nOpen\nRFT', cell1='Title', cell2='', href='/tender/view?id='+ref)
                for ref in refs]
        page.evaluate.side_effect = lambda script: rows if script == EXTRACT_ROWS_JS else next_href
        url = spider.BASE + '/tenders/' + kind
        response = HtmlResponse(url, request=Request(url, meta={'playwright_page': page}))
        result = [item async for item in spider.parse(response)]
        page.close.assert_awaited_once()
        return result
    async def test_total_reconciles_over_pages_and_deduplicates(self):
        spider = attach(ActTenderSpider())
        result = await self.parse_page(spider, 'Showing 1 to 1 of 2 entries', ['a'], '?page=2')
        self.assertIsInstance(result[-1], Request)
        await self.parse_page(spider, 'Showing 2 to 2 of 2 entries', ['b'])
        spider.closed('finished')
        self.assertFalse(failed(spider))
    async def test_missing_next_with_reported_total_fails(self):
        spider = attach(ActTenderSpider())
        await self.parse_page(spider, 'Total: 2 tenders', ['a', 'a'])
        spider.closed('finished')
        self.assertEqual(spider.crawler.stats.values['crawl/act_pagination_failed'], 1)
    async def test_unknown_total_warns_only(self):
        spider = attach(ActTenderSpider())
        await self.parse_page(spider, 'Changed count markup', ['a'])
        spider.closed('finished')
        self.assertFalse(failed(spider))
        self.assertEqual(spider.crawler.stats.values['crawl/act_pagination_unverified'], 1)
    async def test_open_closed_totals_are_separate(self):
        spider = attach(ActTenderSpider())
        await self.parse_page(spider, 'Total: 1 tenders', ['a'])
        await self.parse_page(spider, 'Total: 1 tenders', ['b'], kind='closed')
        spider.closed('finished')
        self.assertFalse(failed(spider))


class FieldTests(unittest.TestCase):
    def check(self, source, items):
        with patch('pipelines.DatabaseManager'):
            pipeline = DatabasePipeline()
        spider = attach(SimpleNamespace(name=source))
        for item in items:
            pipeline._count_fields(item, spider)
        pipeline._finish_field_checks(spider)
        return spider
    def test_normal_sources(self):
        for source, fields in DatabasePipeline.KEY_FIELDS.items():
            with self.subTest(source=source):
                self.assertFalse(failed(self.check(source, [dict.fromkeys(fields, 'content')]*10)))
    def test_majority_loss_all_sources(self):
        for source, fields in DatabasePipeline.KEY_FIELDS.items():
            for field in fields:
                with self.subTest(source=source, field=field):
                    good = dict.fromkeys(fields, 'content')
                    bad = {**good, field: '  '}
                    self.assertTrue(failed(self.check(source, [bad]*6+[good]*4)))
    def test_exactly_half_and_small_runs_stay_ok(self):
        self.assertFalse(failed(self.check('standing_offer', [{}]*9)))
        self.assertFalse(failed(self.check('standing_offer', [{}]*5+[{'Description':'ok'}]*5)))
    def test_zero_value_is_present(self):
        self.assertFalse(failed(self.check('aus_contract', [{'Supplier Name':'A','Contract Value (AUD)':0}]*10)))
    def test_final_ratio_not_transient_ratio(self):
        self.assertFalse(failed(self.check('standing_offer', [{}]*10+[{'Description':'ok'}]*11)))
    def test_process_and_close_hooks(self):
        with patch('pipelines.DatabaseManager') as db, patch('pipelines.activity_log.record_items'):
            pipeline = DatabasePipeline()
            spider = attach(SimpleNamespace(name='standing_offer'))
            spider.crawler.settings = Mock()
            spider.crawler.settings.getbool.return_value = False
            pipeline.open_spider(spider)
            pipeline._validate_item = Mock(return_value=True)
            db.return_value.insert_item.return_value = True
            db.return_value.get_stats.return_value = {}
            for _ in range(10):
                pipeline.process_item({'SON ID':'1','Agency':'A','URL':'https://example.test'}, spider)
            pipeline.close_spider(spider)
            self.assertTrue(failed(spider))


class BuyTests(unittest.TestCase):
    def setUp(self):
        with patch('spiders.buy_ict.DatabaseManager'):
            self.spider = attach(BuyIctApiSpider(summary_only=True, page_size=2))
        self.spider.db_manager.update_buy_ict_listing.return_value = 0
        self.spider._results_page_request = Mock(side_effect=lambda *args: args[-1])
    def parse(self, data, page=1):
        request = Request('https://example.test', meta={'token':'fixture','page_num':page,
                          'results_payload':{},'results_rectangle_id':'fixture'})
        response = TextResponse(request.url, request=request,
                                body=json.dumps({'result':{'data':{'pageItems':[], **data}}}).encode(), encoding='utf-8')
        return list(self.spider.parse_results_rectangle(response))
    def test_normal_total_preserves_sparse_pages(self):
        self.assertEqual(self.parse({'totalItems':5}), [2,3])
        self.assertFalse(failed(self.spider))
    def test_bad_total_validated_next_and_end(self):
        self.assertEqual(self.parse({'totalItems':'unknown','nextPage':2}), [2])
        self.assertEqual(self.parse({'totalItems':'unknown','hasNextPage':False}, 2), [])
        self.assertFalse(failed(self.spider))
    def test_bad_total_no_discovery_fails(self):
        for total in ('unknown', {}, -1, True, 1.5, None):
            with self.subTest(total=total):
                self.parse({'totalItems':total})
                self.assertTrue(failed(self.spider))
    def test_invalid_next_cannot_loop_or_skip(self):
        for next_page in (1, 3, 'bogus', True):
            with self.subTest(next_page=next_page):
                self.assertEqual(self.parse({'totalItems':'bad','nextPage':next_page}), [])
                self.assertTrue(failed(self.spider))
    def test_explicit_zero_total_ok(self):
        self.assertEqual(self.parse({'totalItems':0}), [])
        self.assertFalse(failed(self.spider))
    def test_unknown_total_cap_is_failure(self):
        self.spider.max_pages = 1
        self.parse({'totalItems':'bad','hasNextPage':True})
        self.assertTrue(failed(self.spider))


class NSWRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def run_fetch(self, ages, fail=False):
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        with patch.object(nsw.sqlite3, 'connect', return_value=con):
            nsw.init_db()
        now = datetime.now(timezone.utc)
        cards = []
        for i, age in enumerate(ages):
            url = nsw.BASE + f'/prcOpportunity/{i}'
            con.execute('INSERT INTO nsw_tenders (url,portal,status,publish_date,all_details,detail_refreshed_at) '
                        'VALUES (?,?,?,?,?,?)', (url,'nsw_opportunity','Open','2026-11-01','old details',
                        (now-timedelta(days=age)).isoformat() if age is not None else None))
            cards.append(dict(href=f'/prcOpportunity/{i}',title='A',agency='A',categories=[],
                              reference=str(i),description='Listing',type='RFT',closes='tomorrow'))
        listing = AsyncMock()
        listing.goto.return_value = SimpleNamespace(status=200)
        listing.inner_text.return_value = f'{len(cards)} results'
        rounds = iter([cards, []])
        listing.evaluate.side_effect = lambda script: next(rounds) if script == nsw.EXTRACT_OPPORTUNITIES_JS else []
        detail = AsyncMock()
        detail.goto.return_value = SimpleNamespace(status=503 if fail else 200)
        detail.inner_text.return_value = 'Description\nUpdated\nOverview\nPublish date:\n1 Nov 2026\nScope\nNew scope'
        context = SimpleNamespace(new_page=AsyncMock(side_effect=[listing, detail]))
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        manager = AsyncMock()
        manager.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
        with patch.object(nsw, 'async_playwright', return_value=manager), \
             patch.object(nsw, 'stealth_async', new=AsyncMock()), \
             patch.object(nsw.asyncio, 'sleep', new=AsyncMock()), \
             patch.object(nsw.activity_log, 'record_items'), self.assertLogs(nsw.log, level='INFO') as logs:
            try:
                await nsw.fetch_opportunities(con)
            except RuntimeError:
                self.last_logs = '\n'.join(logs.output)
                raise
        return con, detail, '\n'.join(logs.output)
    async def test_recent_details_not_refetched(self):
        _, detail, logs = await self.run_fetch([1])
        detail.goto.assert_not_awaited()
        self.assertIn("'crawl/nsw_details_refreshed': 0", logs)
    async def test_stale_refresh_updates_details_and_timestamp(self):
        con, detail, logs = await self.run_fetch([8])
        detail.goto.assert_awaited_once()
        row = con.execute('SELECT scope, detail_refreshed_at FROM nsw_tenders').fetchone()
        self.assertEqual(row[0], 'New scope')
        self.assertGreater(row[1], (datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        self.assertIn("'crawl/nsw_details_refreshed': 1", logs)
    async def test_cap_and_legacy_timestamp(self):
        _, detail, _ = await self.run_fetch([None]*(nsw.DETAIL_REFRESH_CAP+1))
        self.assertEqual(detail.goto.await_count, nsw.DETAIL_REFRESH_CAP)
    async def test_failed_refresh_keeps_details_and_retries(self):
        con, _, logs = await self.run_fetch([8], fail=True)
        row = con.execute('SELECT all_details, detail_refreshed_at FROM nsw_tenders').fetchone()
        self.assertEqual(row[0], 'old details')
        self.assertLess(row[1], (datetime.now(timezone.utc)-timedelta(days=7)).isoformat())
        self.assertIn("'crawl/nsw_detail_refresh_errors': 1", logs)
    async def test_many_refresh_errors_follow_existing_rule(self):
        with self.assertRaisesRegex(RuntimeError, 'NSW detail pages failing: 4 of 4'):
            await self.run_fetch([8]*4, fail=True)

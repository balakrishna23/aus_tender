"""Changed-site and empty-window regressions; no network or application databases."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scrapy import Request, Selector
from scrapy.http import HtmlResponse
import austender_api as api
import nsw_tender_fetch as nsw
import run_spiders
from spiders.act_tender import ActTenderSpider, EXTRACT_ROWS_JS


class EnvelopeTests(unittest.TestCase):
    def http(self, *bodies):
        return SimpleNamespace(get=Mock(side_effect=[
            SimpleNamespace(status_code=200, json=lambda body=body: json.loads(body))
            for body in bodies]))

    def walk(self, *bodies):
        now = datetime(2026, 11, 13, tzinfo=timezone.utc)
        return list(api.walk('contractLastModified', now, now, self.http(*bodies), Mock()))

    def test_invalid_http_200_envelopes_fail(self):
        for body in ['{}', '[]', '{"error":"unavailable"}', '{"releases":null}',
                     '{"releases":{}}', '{"releases":[],"links":[]}',
                     '{"releases":[],"links":null}', '{"releases":[],"links":{"next":42}}',
                     '{"releases":[],"errors":[]}', '{"releases":[null]}']:
            with self.subTest(body=body), self.assertRaisesRegex(RuntimeError, 'Invalid OCDS envelope'):
                self.walk(body)

    def test_empty_window_is_valid(self):
        self.assertEqual(self.walk('{"releases":[],"links":{}}'), [])
        self.assertEqual(self.walk('{"releases":[]}'), [])

    def test_pagination_validates_each_page(self):
        with self.assertRaisesRegex(RuntimeError, 'Invalid OCDS envelope'):
            self.walk('{"releases":[],"links":{"next":"https://example.test/page2"}}', '{}')

    def test_failure_does_not_advance_watermark(self):
        with patch.object(api, 'get_state', return_value='2026-11-12'), \
                patch.object(api, 'set_state') as state, patch.object(api, 'save', return_value=([], set())), \
                patch.object(api, 'rollup'), patch.object(api.activity_log, 'record_items'):
            with self.assertRaisesRegex(RuntimeError, 'Invalid OCDS envelope'):
                api.run_incremental(Mock(), http=self.http(
                    '{"releases":[],"links":{"next":"https://example.test/page2"}}', '{}'), sleep=Mock())
            state.assert_not_called()

    def test_valid_empty_incremental_advances_watermark(self):
        with patch.object(api, 'get_state', return_value='2026-11-12'), \
                patch.object(api, 'set_state') as state, patch.object(api, 'save', return_value=([], set())), \
                patch.object(api, 'rollup'), patch.object(api.activity_log, 'record_items'):
            self.assertEqual(api.run_incremental(Mock(), http=self.http('{"releases":[]}'), sleep=Mock()), 0)
            self.assertEqual(state.call_args.args[1], 'modified_through')


def listing_html(total='1 results', next_page=False, scheme=False, card=True):
    return (f'<p>{total}</p><ul id="search-results">' +
            ('<li><a href="/prcOpportunity/current"><h3>Test opportunity</h3></a></li>' if card else '') +
            ('<li><a href="/scheme/register">Scheme</a></li>' if scheme else '') +
            '</ul>' + ('<a rel="next" href="?page=2">Next</a>' if next_page else ''))


class ListingPage:
    """Feed rendered inline HTML into the browser-dependent collector branches."""
    def __init__(self, bodies):
        self.bodies = iter(bodies)
        self.scripts = []
        self.urls = []
        self.close = AsyncMock()
        self.wait_for_selector = AsyncMock()

    async def goto(self, *args, **kwargs):
        self.urls.append(args[0])
        self.selector = Selector(text=next(self.bodies))
        return SimpleNamespace(status=200)

    async def inner_text(self, selector):
        return '\n'.join(self.selector.css('body ::text').getall())

    async def evaluate(self, script):
        self.scripts.append(script)
        if script == nsw.NEXT_PAGE_JS:
            return self.selector.css('a[rel="next"]::attr(href)').get()
        if '/scheme/' in script:
            return self.selector.css('a[href*="/scheme/"]::attr(href)').getall()
        if script == nsw.EXTRACT_NOTICES_JS:
            return [{'href': '/notices/test', 'text': 'Test notice'}]
        return [dict(href=href, title='Test', categories=[], reference='TEST', description='',
                     agency='Agency', type='RFT', closes='') for href in
                self.selector.css('a[href*="/prcOpportunity/"]::attr(href)').getall()]


def browser_for(page):
    ctx = SimpleNamespace(new_page=AsyncMock(return_value=page))
    browser = SimpleNamespace(new_context=AsyncMock(return_value=ctx), close=AsyncMock())
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(
        launch=AsyncMock(return_value=browser)))
    return manager, browser


class NSWCoverageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.addCleanup(self.con.close)
        self.con.execute('CREATE TABLE nsw_tenders (url TEXT, portal TEXT, publish_date TEXT, '
                         'all_details TEXT, status TEXT, reference TEXT, title TEXT, updated_at TEXT)')
        for name in ['current', 'absent']:
            self.con.execute('INSERT INTO nsw_tenders VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (nsw.BASE + '/prcOpportunity/' + name, 'nsw_opportunity',
                              '2026-11-12', 'details', 'Open', name, name, ''))
        self.con.commit()

    async def fetch(self, bodies, cap=5):
        page = ListingPage(bodies)
        manager, browser = browser_for(page)
        with patch.object(nsw, 'async_playwright', return_value=manager), \
                patch.object(nsw, 'stealth_async', new=AsyncMock()), \
                patch.object(nsw.asyncio, 'sleep', new=AsyncMock()), \
                patch.object(nsw, 'upsert_opportunity', return_value=False), \
                patch.object(nsw.activity_log, 'record_items') as events:
            result = await nsw.fetch_opportunities(self.con, max_pages=cap)
        browser.close.assert_awaited_once()
        self.assertNotIn(nsw.NEXT_PAGE_JS, page.scripts)
        self.assertEqual(page.urls, [f'{nsw.BASE}/opportunity/search?mode=regular&page={n}'
                                     for n in range(1, len(page.urls) + 1)])
        return result, events

    def absent_status(self):
        return self.con.execute("SELECT status FROM nsw_tenders WHERE reference='absent'").fetchone()[0]

    async def test_missing_total_skips_closing_and_reports_partial(self):
        with self.assertLogs(nsw.log, level='WARNING') as logs:
            _, events = await self.fetch([listing_html(total='Changed count markup'),
                                          listing_html(total='Changed count markup', card=False)])
        self.assertEqual(self.absent_status(), 'Open')
        self.assertEqual(events.call_args.args[0], [])
        self.assertIn("'crawl/nsw_opportunity_coverage_failed': 1", '\n'.join(logs.output))

    async def test_cap_skips_closing_even_when_count_reconciles(self):
        with self.assertLogs(nsw.log, level='WARNING'):
            await self.fetch([listing_html()], cap=1)
        self.assertEqual(self.absent_status(), 'Open')

    async def test_empty_page_before_cap_can_close(self):
        result, events = await self.fetch([listing_html(), listing_html(card=False)])
        self.assertEqual(result, 1)
        self.assertEqual(self.absent_status(), 'Closed')
        self.assertEqual(events.call_args.args[0][0][1], 'closed')

    async def test_scheme_counts_still_reconcile(self):
        await self.fetch([listing_html(total='2 results', scheme=True), listing_html(card=False)])
        self.assertEqual(self.absent_status(), 'Closed')

    async def test_formatted_case_insensitive_total_reconciles(self):
        await self.fetch([listing_html(total='1 RESULTS'), listing_html(card=False)])
        self.assertEqual(self.absent_status(), 'Closed')

    async def test_scheme_only_and_validated_zero_are_complete(self):
        for total, scheme in [('1 results', True), ('0 results', False)]:
            result, _ = await self.fetch([listing_html(total=total, card=False, scheme=scheme)])
            self.assertEqual(result, 0)
            self.assertEqual(self.absent_status(), 'Closed')

    async def test_unexplained_empty_still_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'No NSW opportunities'):
            await self.fetch([listing_html(total='Changed count', card=False)])

    async def test_under_count_fails_without_closing(self):
        with self.assertRaisesRegex(RuntimeError, 'opportunities incomplete'):
            await self.fetch([listing_html(total='2 results'), listing_html(card=False)])
        self.assertEqual(self.absent_status(), 'Open')

    async def test_no_fresh_cards_ends_walk_regardless_of_notice_next_link(self):
        await self.fetch([listing_html(next_page=True), listing_html(next_page=True)])
        self.assertEqual(self.absent_status(), 'Closed')

    async def test_13_november_counts_complete_without_notice_pagination(self):
        # Live evidence: 149 opportunities and 31 schemes of 180 reported.
        links = ['<li><a href="/prcOpportunity/current">Current</a></li>']
        for number in range(148):
            href = f'/prcOpportunity/test-{number}'
            links.append(f'<li><a href="{href}">Test</a></li>')
            self.con.execute('INSERT INTO nsw_tenders VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                             (nsw.BASE + href, 'nsw_opportunity', '2026-11-12',
                              'details', 'Open', str(number), 'Test', ''))
        schemes = ''.join(f'<li><a href="/scheme/{n}">Scheme</a></li>' for n in range(31))
        bodies = [f'<p>180 results</p><ul id="search-results">{"".join(links[:75])}</ul>',
                  f'<p>180 results</p><ul id="search-results">{"".join(links[75:])}{schemes}</ul>',
                  listing_html(total='180 results', card=False)]
        with self.assertLogs(nsw.log, level='INFO') as logs:
            result, _ = await self.fetch(bodies)
        self.assertEqual(result, 149)
        self.assertEqual(self.absent_status(), 'Closed')
        self.assertIn('149 open tenders + 31 prequalification schemes of 180', '\n'.join(logs.output))

    async def notices(self, next_page):
        manager, browser = browser_for(ListingPage([listing_html(next_page=next_page)]))
        with patch.object(nsw, 'async_playwright', return_value=manager), \
                patch.object(nsw, 'stealth_async', new=AsyncMock()), patch.object(nsw, 'upsert') as save:
            self.assertEqual(await nsw.fetch_notices('can', 1, self.con), 1)
        browser.close.assert_awaited_once()
        save.assert_called_once()

    async def test_notice_cap_with_more_pages_reports_coverage_failure(self):
        with self.assertLogs(nsw.log, level='WARNING') as logs:
            await self.notices(True)
        self.assertIn("'crawl/nsw_notice_coverage_failed': 1", '\n'.join(logs.output))

    async def test_notice_last_page_at_cap_is_complete(self):
        with self.assertNoLogs(nsw.log, level='WARNING'):
            await self.notices(False)


class ACTEmptyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.spider = ActTenderSpider()
        self.values = {}
        stats = Mock()
        stats.inc_value.side_effect = lambda key: self.values.update({key: self.values.get(key, 0) + 1})
        stats.set_value.side_effect = self.values.__setitem__
        self.spider.crawler = SimpleNamespace(stats=stats)

    async def parse(self, body, kind='open'):
        page = AsyncMock()
        page.evaluate.side_effect = lambda script: [] if script == EXTRACT_ROWS_JS else None
        page.inner_text.return_value = '\n'.join(
            ''.join(p.css('::text').getall()) for p in Selector(text=body).css('p'))
        url = self.spider.BASE + '/tenders/' + kind
        response = HtmlResponse(url, request=Request(url, meta={'playwright_page': page}),
                                body=body.encode(), encoding='utf-8')
        result = [item async for item in self.spider.parse(response)]
        page.close.assert_awaited_once()
        return result

    async def test_explicit_empty_open_and_closed_are_nothing_due(self):
        self.assertEqual(await self.parse('<p>No results found</p>'), [])
        self.assertEqual(await self.parse('<p>Total: <strong>0</strong> results</p>', 'closed'), [])
        self.spider.closed('finished')
        self.assertNotIn('crawl/extraction_failed', self.values)
        self.assertEqual(self.values['crawl/nothing_due'], 1)

    async def test_missing_rows_without_marker_fails(self):
        await self.parse('<p>Unexpected layout</p>')
        self.assertEqual(self.values['crawl/extraction_failed'], 1)

    async def test_one_empty_list_does_not_mask_other_failure(self):
        await self.parse('<p>No tenders available</p>')
        await self.parse('<p>Unexpected layout</p>', 'closed')
        self.spider.closed('finished')
        self.assertNotIn('crawl/nothing_due', self.values)

    async def test_interrupted_empty_crawl_is_not_nothing_due(self):
        await self.parse('<p>0 results</p>')
        await self.parse('<p>0 results</p>', 'closed')
        self.spider.closed('shutdown')
        self.assertNotIn('crawl/nothing_due', self.values)


class CoverageRunnerTests(unittest.TestCase):
    def test_nsw_coverage_stats_are_partial_in_report(self):
        for stat in ['nsw_notice_coverage_failed', 'nsw_opportunity_coverage_failed']:
            with self.subTest(stat=stat), tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / 'report.json'
                args = SimpleNamespace(report=report, spiders=['nsw_tender'], backfill_since=None,
                                       timeout=0, retries=0, fail_fast=False, no_feed=True)
                with patch.object(run_spiders, 'DatabaseManager'), \
                        patch.object(run_spiders, 'ping_healthcheck'), \
                        patch.object(run_spiders, 'rotate_log'), \
                        patch.object(run_spiders, 'collect_db_count', return_value=2), \
                        patch.object(run_spiders, 'run_standalone', return_value=0), \
                        patch.object(run_spiders, 'read_text', return_value=f"WARNING: 'crawl/{stat}': 1"), \
                        patch.object(run_spiders, 'archive_logs'), \
                        patch.object(run_spiders, 'data_quality_warnings', return_value=[]), \
                        patch.object(run_spiders, 'LOG_DIR', Path(directory)), \
                        patch('activity_log.start_run'), patch('activity_log.record_scrape'), \
                        patch('activity_log.finish_run'), patch.dict(os.environ), redirect_stdout(io.StringIO()):
                    self.assertEqual(run_spiders.run_batch(args, False), 1)
                result = json.loads(report.read_text())['results'][0]
                self.assertEqual(result['status'], 'PARTIAL')
                self.assertTrue(result['crawl_failed'])


if __name__ == '__main__':
    unittest.main()

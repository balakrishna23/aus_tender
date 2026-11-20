"""Site-format regressions from the 15 November scraper audit."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scrapy import Request
from scrapy.http import HtmlResponse, TextResponse
from spiders.buy_ict import BuyIctApiSpider
from spiders.austender_common import field_value
from spiders.aus_tender import AusTenderSpider
from spiders.planned_procurement import PlannedProcurementSpider
from pipelines import DatabasePipeline


class Stats:
    def __init__(self):
        self.values = {}
    def inc_value(self, key):
        self.values[key] = self.values.get(key, 0) + 1
    def set_value(self, key, value):
        self.values[key] = value
    def get_stats(self):
        return self.values


def attach(spider):
    spider.crawler = SimpleNamespace(stats=Stats())
    return spider


def response(body, url='https://www.tenders.gov.au/Atm', meta=None):
    return HtmlResponse(url, request=Request(url, meta=meta or {}),
                        body=body.encode(), encoding='utf-8')


class BuyChanges(unittest.TestCase):
    def setUp(self):
        with patch('spiders.buy_ict.DatabaseManager'):
            self.spider = attach(BuyIctApiSpider(summary_only=True, page_size=90))
        self.spider.db_manager.update_buy_ict_listing.return_value = 0

    def listing(self, data):
        req = Request('https://www.buyict.gov.au/api/test', meta={
            'token': 'fixture', 'page_num': 1, 'results_payload': {},
            'results_rectangle_id': 'fixture'})
        return list(self.spider.parse_results_rectangle(TextResponse(req.url, request=req,
            body=json.dumps({'result': {'data': data}}).encode(), encoding='utf-8')))

    def test_quoted_bootstrap_values(self):
        for quote in ("'", '"'):
            self.assertEqual(self.spider._extract_g_ck(f'window.g_ck = {quote}token{quote}'), 'token')
            self.assertEqual(self.spider._extract_portal_id(f'var portal_id = {quote}portal{quote}'), 'portal')

    def test_widget_instance_is_bound_to_widget_not_nearest_text(self):
        wrong, right = 'a' * 32, 'b' * 32
        data = {'widgets': [{'instance_id': wrong, 'widget': {'id': 'other'}},
                            {'instance_id': right, 'widget': {'id': 'opportunity_rfq_details'}}]}
        self.assertEqual(self.spider._discover_detail_rectangle_id_from_page_html(
            '<script>window.bootstrap = ' + json.dumps(data) + ';</script>'), right)

    def test_unrelated_or_malformed_widget_does_not_supply_instance(self):
        for data in ({'instance_id': 'a'*32, 'widget': 'bad'},
                     {'instance_id': 'a'*32, 'widget': {'id': 'other'}}):
            self.assertIsNone(self.spider._discover_detail_rectangle_id_from_page_html(json.dumps(data)))

    def test_label_variations_keep_canonical_fields(self):
        labels = self.spider._label_value_map([
            {'label': '  rfq   id : ', 'value': '123'},
            {'label': ' BUYER ', 'value': 'Agency'}, {'label': 'Other', 'value': 'evidence'}])
        self.assertEqual(labels, {'RFQ ID': '123', 'Buyer': 'Agency', 'Other': 'evidence'})

    def test_formatted_total_schedules_every_page(self):
        requests = self.listing({'pageItems': [], 'totalItems': '10,484'})
        self.assertEqual(len(requests), 116)
        self.assertEqual(self.spider.expected_pages, 117)

    def test_validated_zero_survives_close(self):
        self.listing({'pageItems': [], 'totalItems': 0})
        self.spider.closed('finished')
        self.assertEqual(self.spider.crawler.stats.values.get('crawl/nothing_due'), 1)
        self.assertNotIn('crawl/extraction_failed', self.spider.crawler.stats.values)

    def test_missing_payload_is_never_validated_empty(self):
        self.listing({'totalItems': 0})
        self.spider.closed('finished')
        self.assertGreater(self.spider.crawler.stats.values.get('crawl/extraction_failed', 0), 0)
        self.assertNotIn('crawl/nothing_due', self.spider.crawler.stats.values)

    def test_malformed_rows_fail_without_exception(self):
        for rows in (['bad'], None, {}):
            self.listing({'pageItems': rows, 'totalItems': 1})
        self.assertEqual(self.spider.crawler.stats.values.get('crawl/extraction_failed'), 3)

    def test_detail_recovers_stored_identity_only_for_same_sys_id(self):
        self.spider.stored_rfq_ids = {'a'*32: 'KNOWN-123'}
        for sys_id, expected in [('a'*32, 'KNOWN-123'), ('b'*32, None)]:
            req = Request('https://www.buyict.gov.au/api/test', meta={
                'detail_url': 'https://www.buyict.gov.au/public?id=opportunity&sys_id=' + sys_id})
            item = list(self.spider.parse_detail_rectangle(TextResponse(req.url, request=req,
                body=json.dumps({'result': {'data': {'mainDetails': [
                    {'label': 'Buyer', 'value': 'Agency'}]}}}).encode(), encoding='utf-8')))[0]
            self.assertEqual(item['rfq_id'], expected)

    def test_stored_identity_cannot_hide_broken_detail_payload(self):
        self.spider.stored_rfq_ids = {'a'*32: 'KNOWN-123'}
        req = Request('https://www.buyict.gov.au/api/test', meta={
            'detail_url': 'https://www.buyict.gov.au/public?id=opportunity&sys_id=' + 'a'*32,
            'fallback_attempted': True})
        for details in ({'changed': 'shape'}, [None], []):
            items = list(self.spider.parse_detail_rectangle(TextResponse(req.url, request=req,
                body=json.dumps({'result': {'data': {'mainDetails': details}}}).encode(), encoding='utf-8')))
            self.assertEqual(items, [])
        self.assertEqual(self.spider.crawler.stats.values['crawl/extraction_failed'], 3)


class FederalChanges(unittest.TestCase):
    def test_nested_labels_and_extra_css_tokens(self):
        page = response('<div class="extra list-desc"><span><label> Agency: </label></span>'
                        '<div class="extra list-desc-inner"><a>Some <b>Agency</b></a></div></div>')
        self.assertEqual(field_value(page, 'agency'), 'Some Agency')

    def test_listing_nested_id_label(self):
        spider = attach(AusTenderSpider())
        page = response('<div class="extra list-desc"><span><label> ATM ID: </label></span>'
                        '<div class="list-desc-inner extra"><a href="/Atm/Show/test">ID</a></div></div>')
        self.assertEqual(spider._notice_links(page), ['/Atm/Show/test'])

    def test_count_shortfall_gets_one_fresh_rewalk(self):
        spider = attach(AusTenderSpider())
        spider.window_totals['https://www.tenders.gov.au/Atm'] = 2
        page = response('<div class="list-desc"><span>ATM ID:</span>'
                        '<div class="list-desc-inner"><a href="/Atm/Show/one">one</a></div></div>')
        requests = list(spider.parse(page))
        recheck = requests[-1]
        self.assertTrue(recheck.meta['coverage_reset'])
        self.assertTrue(recheck.dont_filter)
        second = response(page.text, meta=recheck.meta)
        self.assertEqual(len(list(spider.parse(second))), 1)  # only the detail, no third walk

    def test_nested_plan_title(self):
        spider = attach(PlannedProcurementSpider())
        items = list(spider.parse_plan(response('<h1>Agency</h1><div class="row">'
            '<p class="lead"><a>A <span>plan</span></a></p><div id="pp-1"></div></div>',
            url='https://www.tenders.gov.au/App/Show/one')))
        self.assertEqual(items[0]['Title'], 'A plan')

    def test_recheck_uses_new_valid_total_but_retains_old_if_missing(self):
        for reported, expected in [('<p>of <strong>1</strong> records</p>', 1), ('', 2)]:
            spider = attach(AusTenderSpider())
            root = 'https://www.tenders.gov.au/Atm'
            spider.window_totals[root] = 2
            page = response(reported + '<div class="list-desc"><span>ATM ID:</span>'
                            '<div class="list-desc-inner"><a href="/Atm/Show/one">one</a></div></div>',
                            meta={'coverage_reset': True, 'coverage_recheck': True})
            list(spider.parse(page))
            self.assertEqual(spider.window_totals[root], expected)


class FieldBaselines(unittest.TestCase):
    def check(self, prior):
        with patch('pipelines.DatabaseManager'):
            pipeline = DatabasePipeline()
        pipeline.field_baselines = {'standing_offer': {'Description': prior}}
        spider = attach(SimpleNamespace(name='standing_offer'))
        for _ in range(10):
            pipeline._count_fields({'URL': 'https://example.test/one'}, spider)
        pipeline._finish_field_checks(spider)
        return spider.crawler.stats.values

    def test_same_stored_omission_warns_without_failure(self):
        stats = self.check({'https://example.test/one': None})
        self.assertEqual(stats['crawl/field_loss_known_omissions'], 10)
        self.assertNotIn('crawl/field_loss_failed', stats)

    def test_lost_previously_present_field_still_fails(self):
        self.assertEqual(self.check({'https://example.test/one': 'existing'})['crawl/field_loss_failed'], 1)

    def test_new_rows_without_fields_still_fail(self):
        self.assertEqual(self.check({})['crawl/field_loss_failed'], 1)

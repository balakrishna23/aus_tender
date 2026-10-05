import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import scrapy
from scrapy.http import HtmlResponse

from spiders.planned_procurement import PlannedProcurementSpider

PLAN = 'https://www.tenders.gov.au/App/Show/aaaa'


def listing(*contacts):
    rows = ''.join(
        f'<article><p class="lead"><a href="/App/Show/aaaa?ppId={i}#pp-{i}">Plan item {i}</a></p>'
        f'<div class="list-desc"><span>Contact:</span><div class="list-desc-inner">{c}</div></div></article>'
        for i, c in enumerate(contacts))
    url = 'https://www.tenders.gov.au/Search/PpAdvancedSearch?page=1'
    return HtmlResponse(url, body=rows.encode(), encoding='utf-8', request=scrapy.Request(url))


def plan_page():
    body = ('<div class="contact-long"><p class="contact-heading">Contact Details</p><p>Procurement Team</p>'
            '<p><span>Phone:</span><br/> 02 6000 0000</p><p><span>Email Address:</span><br />'
            '<a href="mailto:buy@agency.gov.au" class="u email">buy@agency.gov.au</a></p></div>')
    request = scrapy.Request(PLAN, cb_kwargs={'plan_url': PLAN})
    return HtmlResponse(PLAN, body=body.encode(), encoding='utf-8', request=request)


class PlanContactTests(unittest.TestCase):
    def setUp(self):
        self.spider = PlannedProcurementSpider()
        self.spider.crawler = SimpleNamespace(stats=Mock())

    def test_as_for_app_rows_take_the_plan_contact(self):
        out = list(self.spider.parse(listing('Jo Smith, jo@agency.gov.au, 02 1111 1111', 'As for APP', 'As for APP')))
        requests = [o for o in out if isinstance(o, scrapy.Request)]
        items = [o for o in out if isinstance(o, dict)]
        self.assertEqual([r.url for r in requests], [PLAN])  # one plan fetch for both rows
        self.assertEqual([i['Contact Email'] for i in items], ['jo@agency.gov.au'])
        filled = list(self.spider.parse_plan(plan_page(), plan_url=PLAN))
        self.assertEqual([(i['Contact Name'], i['Contact Phone'], i['Contact Email']) for i in filled],
                         [('Procurement Team', '02 6000 0000', 'buy@agency.gov.au')] * 2)
        # Later rows of the same plan are filled straight from the cache.
        later = list(self.spider.parse(listing('As for APP')))
        self.assertEqual(later[0]['Contact Email'], 'buy@agency.gov.au')

    def test_failed_plan_page_keeps_rows(self):
        list(self.spider.parse(listing('As for APP')))
        failure = SimpleNamespace(request=scrapy.Request(PLAN, cb_kwargs={'plan_url': PLAN}), value='timeout')
        with self.assertLogs('planned_procurement', level='WARNING'):
            kept = list(self.spider.plan_failed(failure))
        self.assertEqual([i['Contact Name'] for i in kept], ['As for APP'])
        self.spider.crawler.stats.inc_value.assert_called_with('crawl/plan_contact_missing')

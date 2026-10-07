import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import scrapy
from scrapy.http import HtmlResponse

from spiders.planned_procurement import PlannedProcurementSpider

PLAN = 'https://www.tenders.gov.au/App/Show/aaaa'


def plan_page(*contacts, contact_box=True):
    """An agency's plan page: its contact box and one item per contact."""
    box = ('<div class="contact-long"><p class="contact-heading">Contact Details</p><p>Procurement Team</p>'
           '<p><span>Phone:</span><br/> 02 6000 0000</p><p><span>Email Address:</span><br />'
           '<a href="mailto:buy@agency.gov.au" class="u email">buy@agency.gov.au</a></p></div>') if contact_box else ''
    rows = ''.join(
        f'<div class="row"><div class="col-sm-4"><div class="box boxY"><p class="lead">Plan item {i}</p></div></div>'
        f'<div class="col-sm-8"><div id="pp-{i}" class="box boxW listInner">'
        f'<div class="list-desc"><span>Contact:</span><div class="list-desc-inner">{c}</div></div></div></div></div>'
        for i, c in enumerate(contacts))
    return HtmlResponse(PLAN, body=(box + rows).encode(), encoding='utf-8', request=scrapy.Request(PLAN))


class PlanContactTests(unittest.TestCase):
    def setUp(self):
        self.spider = PlannedProcurementSpider()
        self.spider.crawler = SimpleNamespace(stats=Mock())

    def test_as_for_app_rows_take_the_plan_contact(self):
        items = list(self.spider.parse_plan(plan_page('Jo Smith, 02 1111 1111, jo@agency.gov.au', 'As for APP')))
        self.assertEqual([(i['Contact Name'], i['Contact Phone'], i['Contact Email']) for i in items],
                         [('Jo Smith', '02 1111 1111', 'jo@agency.gov.au'),
                          ('Procurement Team', '02 6000 0000', 'buy@agency.gov.au')])
        self.assertEqual(items[1]['URL'], f'{PLAN}?ppId=1#pp-1')

    def test_plan_without_a_contact_box_keeps_rows(self):
        items = list(self.spider.parse_plan(plan_page('As for APP', contact_box=False)))
        self.assertEqual([(i['Contact Name'], i['Contact Email']) for i in items], [('As for APP', '')])

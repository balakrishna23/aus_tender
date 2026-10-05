import unittest

import scrapy
from scrapy.http import HtmlResponse

from database_manager import DatabaseManager
from spiders.buy_ict import html_to_text
from spiders.standing_offer import StandingOfferSpider


class PlaceholderPhoneTests(unittest.TestCase):
    def test_filler_numbers_are_dropped_real_ones_kept(self):
        for value in ('0000000000', '00 0000 0000', '-----------', '0', '(00) 0000 0000'):
            self.assertTrue(DatabaseManager.is_placeholder_phone(value), value)
        for value in ('02 6261 1111', '+61 (02) 62079000', '1800 000 000', '', None):
            self.assertFalse(DatabaseManager.is_placeholder_phone(value), value)

    def test_prepared_item_has_no_placeholder_phone(self):
        prepared = DatabaseManager.__new__(DatabaseManager)
        prepared.logger = __import__('logging').getLogger('test')
        data = prepared._prepare_item_data({'Contact Phone': '0000000000', 'Title': 'x'}, 'planned_procurement')
        self.assertIsNone(data['contact_phone'])


class PostcodeStateTests(unittest.TestCase):
    def test_australia_post_ranges(self):
        cases = {'2600': 'ACT', '2913': 'ACT', '0200': 'ACT', '2000': 'NSW', '2620': 'NSW', '0800': 'NT',
                 '3001': 'VIC', '8001': 'VIC', '4000': 'QLD', '5000': 'SA', '6000': 'WA', '7000': 'TAS'}
        for postcode, state in cases.items():
            self.assertEqual(DatabaseManager.state_from_postcode(postcode), state, postcode)
        for bad in ('', 'NA', '123', '12345', None):
            self.assertIsNone(DatabaseManager.state_from_postcode(bad))


class BuyIctTextTests(unittest.TestCase):
    def test_rich_text_becomes_plain_text(self):
        value = '<div>\r\n<p>Managing GRC&nbsp;&amp; risk.</p><ul><li>One</li><li>Two</li></ul></div>'
        self.assertEqual(html_to_text(value), 'Managing GRC & risk.\n• One\n• Two')
        self.assertEqual(html_to_text('5 < 6 is true'), '5 < 6 is true')
        self.assertIsNone(html_to_text(None))


class StandingOfferAgencyTests(unittest.TestCase):
    def _son(self, agency_section):
        body = (f'<p class="lead">Panel</p><div id="CurrentSuppliers"><table><tr><th>Name</th></tr>'
                f'<tr><td>Acme</td><td>1</td><td></td><td>VIC</td><td>3000</td></tr></table></div>'
                f'<div id="Agency" class="collapse">{agency_section}</div>')
        url = 'https://www.tenders.gov.au/Son/Show/x'
        item = next(StandingOfferSpider().parse_son(
            HtmlResponse(url, body=body.encode(), encoding='utf-8', request=scrapy.Request(url))))
        return item['Participant Agencies']

    def test_listed_agencies_are_captured(self):
        agencies = self._son('The following agencies may participate. <table class="genT"><tr><th>Agency'
                             '<tr class=even><td>Australian Electoral Commission<tr><td>Comcare</table>')
        self.assertEqual(agencies, [{'Agency Name': 'Australian Electoral Commission'}, {'Agency Name': 'Comcare'}])

    def test_all_agencies_offer_has_no_list(self):
        self.assertEqual(self._son('All agencies may participate in this Standing Offer.'), [])

"""The supported Scrapy entry point must preserve ACT's browser metadata."""
import unittest
from spiders.act_tender import ActTenderSpider


class ActStartCompatibility(unittest.IsolatedAsyncioTestCase):
    async def test_async_start_requests_both_listing_pages_with_browser_setup(self):
        spider=ActTenderSpider()
        requests=[request async for request in spider.start()]
        self.assertEqual([r.url for r in requests],
                         [spider.BASE+'/tenders/open',spider.BASE+'/tenders/closed'])
        for request in requests:
            self.assertTrue(request.meta['playwright'])
            self.assertIn('playwright_page_init_callback',request.meta)

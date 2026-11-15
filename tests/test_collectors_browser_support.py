"""Every browser collector initializes stealth before navigation."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scrapy import Request
from scrapy.utils.misc import load_object
import nsw_tender_fetch as nsw
from spiders import browser_support
from spiders.act_tender import ActTenderSpider, PW_META


class BrowserInitialization(unittest.IsolatedAsyncioTestCase):
    async def test_scrapy_callback_initializes_before_marking_request(self):
        request = Request('https://example.test')
        page = SimpleNamespace()
        async def initialize(actual):
            self.assertIs(actual, page)
            self.assertNotIn('browser_stealth_initialized', request.meta)
        with patch.object(browser_support, '_stealth_async', new=AsyncMock(side_effect=initialize)) as apply:
            await load_object(PW_META['playwright_page_init_callback'])(page, request)
        apply.assert_awaited_once_with(page)
        self.assertTrue(request.meta['browser_stealth_initialized'])

    async def test_standalone_and_scrapy_use_the_same_initializer(self):
        self.assertIs(nsw.stealth_async, browser_support.stealth_async)
        page = SimpleNamespace()
        with patch.object(browser_support, '_stealth_async', new=AsyncMock()) as apply:
            await nsw.stealth_async(page)
        apply.assert_awaited_once_with(page)

    async def test_failed_initialization_is_not_marked_successful(self):
        request = Request('https://example.test')
        with patch.object(browser_support, '_stealth_async', new=AsyncMock(side_effect=RuntimeError('failed'))):
            with self.assertRaises(RuntimeError):
                await browser_support.stealth_async(SimpleNamespace(), request)
        self.assertNotIn('browser_stealth_initialized', request.meta)

    def test_every_act_start_request_sets_pre_navigation_callback(self):
        for request in ActTenderSpider().start_requests():
            self.assertTrue(request.meta['playwright'])
            self.assertEqual(request.meta['playwright_page_init_callback'],
                             'spiders.browser_support.stealth_async')

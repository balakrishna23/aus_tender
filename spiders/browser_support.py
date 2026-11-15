"""Shared pre-navigation initialization for every browser-based collector."""
from undetected_playwright import stealth_async as _stealth_async


async def stealth_async(page, request=None):
    """Initialize before goto(), or through Scrapy's page-init callback."""
    await _stealth_async(page)
    if request is not None:
        request.meta['browser_stealth_initialized'] = True

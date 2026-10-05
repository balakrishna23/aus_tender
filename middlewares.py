"""
Minimal middlewares used by the project.
- SpiderNameMiddleware: injects spider name into Request.meta for downstream consumers
- RateLimitBackoffMiddleware: slows a domain down on HTTP 429/503 instead of dropping requests
"""
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

from scrapy.downloadermiddlewares.retry import get_retry_request
from scrapy.utils.httpobj import urlparse_cached


class RateLimitBackoffMiddleware:
    """Back off the whole download slot when the server says it is overloaded.

    Scrapy's RetryMiddleware retries a 429 immediately (twice), so a throttled crawl
    loses notices. Here each 429/503 doubles the domain's delay (honouring
    Retry-After) and retries the same request up to RATE_LIMIT_MAX_RETRIES times.
    """

    CODES = (429, 503)

    def __init__(self, crawler):
        self.crawler = crawler
        self.max_retries = crawler.settings.getint('RATE_LIMIT_MAX_RETRIES', 8)
        self.max_delay = crawler.settings.getfloat('RATE_LIMIT_MAX_DELAY', 60.0)

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler)

    @staticmethod
    def _retry_after(response):
        value = response.headers.get('Retry-After')
        if not value:
            return None
        value = value.decode('latin-1').strip()
        if value.isdigit():
            return float(value)
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None

    def process_response(self, request, response, spider):
        if response.status not in self.CODES:
            return response
        slot_key = request.meta.get('download_slot') or urlparse_cached(request).hostname
        slot = self.crawler.engine.downloader.slots.get(slot_key)
        if slot is not None:
            wanted = self._retry_after(response) or max(slot.delay * 2, 5.0)
            slot.delay = min(max(slot.delay, wanted), self.max_delay)
            spider.logger.info('HTTP %s from %s; delay now %.1fs', response.status, slot_key, slot.delay)
        self.crawler.stats.inc_value(f'ratelimit/{response.status}')
        retry = get_retry_request(request, spider=spider, reason=f'rate_limited_{response.status}',
                                  max_retry_times=self.max_retries)
        return retry or response


class SpiderNameMiddleware:
    """Ensure all outgoing requests carry the spider name in request.meta."""

    def process_spider_output(self, response, result, spider):
        for item_or_request in result:
            if hasattr(item_or_request, 'meta'):
                item_or_request.meta['spider_name'] = spider.name
            yield item_or_request

    async def process_spider_output_async(self, response, result, spider):
        async for item_or_request in result:
            if hasattr(item_or_request, 'meta'):
                item_or_request.meta['spider_name'] = spider.name
            yield item_or_request

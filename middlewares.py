"""
Minimal middlewares used by the project.
- SpiderNameMiddleware: injects spider name into Request.meta for downstream consumers
"""


class SpiderNameMiddleware:
    """Ensure all outgoing requests carry the spider name in request.meta."""

    def process_spider_output(self, response, result, spider):
        for item_or_request in result:
            if hasattr(item_or_request, 'meta'):
                if not item_or_request.meta:
                    item_or_request.meta = {}
                item_or_request.meta['spider_name'] = spider.name
            yield item_or_request

    async def process_spider_output_async(self, response, result, spider):
        async for item_or_request in result:
            if hasattr(item_or_request, 'meta'):
                if not item_or_request.meta:
                    item_or_request.meta = {}
                item_or_request.meta['spider_name'] = spider.name
            yield item_or_request

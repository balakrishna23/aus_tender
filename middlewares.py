"""
Minimal middlewares: only what the project actually uses.
- SpiderNameMiddleware: injects spider name into Request.meta for downstream consumers (kept for compatibility)
- AusUserAgentMiddleware: rotates user agents per request
"""


class SpiderNameMiddleware:
    """
    Middleware to ensure all requests have spider name in metadata.
    This helps components (e.g., duplicate filter) identify which database to check.
    """

    def process_spider_output(self, response, result, spider):
        for item_or_request in result:
            # If it's a Request-like object, ensure meta exists and add spider name
            if hasattr(item_or_request, 'meta'):
                if not item_or_request.meta:
                    item_or_request.meta = {}
                item_or_request.meta['spider_name'] = spider.name
            yield item_or_request


class AusUserAgentMiddleware:
    """Downloader middleware that sets a random User-Agent header from
    the USER_AGENTS list in settings for every outgoing request.
    """
    def __init__(self, user_agents):
        self.user_agents = list(user_agents) if user_agents else []

    @classmethod
    def from_crawler(cls, crawler):
        # crawler.settings.getlist returns an empty list if setting not present
        return cls(crawler.settings.getlist("USER_AGENTS"))

    def process_request(self, request, spider):
        if not self.user_agents:
            return None
        import random

        ua = random.choice(self.user_agents)
        if ua:
            request.headers['User-Agent'] = ua
        return None

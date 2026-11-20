# Procurement collector instructions

- User preference confirmed 15 November 2026: enable stealth initialization for every
  browser-based procurement collector, before its first navigation. Use
  `spiders.browser_support.stealth_async` directly for standalone Playwright, or through
  Scrapy's `playwright_page_init_callback`. Apply this to new browser collectors too.
- Direct HTTP and API collectors do not use browser stealth. Keep their native transports,
  existing rate limits and retry behavior.
- Respect robots.txt and site terms. Never print or commit credentials, real mailbox
  addresses, server IPs or private business data.
- Before deploying collector changes, test the affected collector against the live site
  using isolated databases, inspect its coverage, then run server staging tests.
- Existing handoff approval rules remain: no pushes, merges, timer changes, deletions or
  additional email sends without the user's authorization. Never resend an edition.

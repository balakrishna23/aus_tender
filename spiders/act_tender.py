"""
ACT Government Tenders Spider -- Playwright enabled
Scrapes tenders.act.gov.au for open/closed tenders and contracts.

URL structure (confirmed):
  /tenders/open      = Open tenders list
  /tenders/closed    = Recently closed list
  /tender/view?id=X  = Individual tender detail (Cloudflare-challenges concurrent hits)

Strategy: extract ALL fields from listing table rows in a single page.evaluate()
call — no detail-page follows needed. Confirmed row cell structure:
  td[0] = "GS2514706\nOpen\nRequest for Tender"       → ref, status, category
  td[1] = "Private Provider Program\nIssued by: CHS"  → title, agency
  td[2] = "Released\nThu, 07 May 2026\nClosing\nTue..." → pub_date, close_date
  <a href="/tender/view?id=320403">                    → url
"""
import re
import scrapy
from scrapy_playwright.page import PageMethod
from datetime import datetime

HEALTH_KEYWORDS = [
    'nurs', 'midwif', 'enrolled nurse', 'registered nurse', 'clinical',
    'allied health', 'locum', 'agency staff', 'health worker', 'care worker',
    'personal care', 'aged care', 'home care', 'community nurs',
    'community health', 'palliative', 'wound care', 'ict labour',
    'labour hire', 'professional services', 'workforce', 'recruitment',
    'staffing', 'aboriginal health', 'indigenous health', 'first nations',
    'accho', 'ndis', 'disability support', 'support worker', 'veteran',
    'hospital', 'health service', 'health district', 'canberra health',
    'act health', 'calvary', 'medical', 'telehealth', 'ehealth',
    'medical services', 'private provider',
]

IT_KEYWORDS = [
    'ict',
    'information technology',
    'information and communication technology',
    'digital canberra',
    'digital transformation',
    'digital services',
    'software development',
    'software solution',
    'cyber security',
    'cybersecurity',
    'cloud services',
    'managed ict',
    'managed it',
    'helpdesk',
    'help desk',
    'service desk',
    'it support',
    'it services',
    'it labour',
    'it staff',
    'it contractor',
    'it recruitment',
    'it workforce',
    'it professional',
    'data analytics',
    'data science',
    'artificial intelligence',
    'machine learning',
    'crm',
    'erp system',
    'management system',
    'system integration',
    'scrum master',
    'agile delivery',
    'devops',
    'security operations centre',
    'soc analyst',
    'penetration test',
    'network security',
    'it infrastructure',
    'ict infrastructure',
]

ALL_KEYWORDS = HEALTH_KEYWORDS + IT_KEYWORDS

def is_relevant(text):
    t = (text or '').lower()
    return any(kw in t for kw in ALL_KEYWORDS)

PW_META = {
    'playwright': True,
    'playwright_include_page': True,
    'playwright_page_methods': [
        # 'load' fires after HTML + subresources, NOT waiting for XHR polling
        # (networkidle never fires: Cloudflare jsd + /serverTime XHR keep going)
        PageMethod('wait_for_load_state', 'load'),
    ],
}

# JavaScript that extracts tender rows from the listing page in one call.
# Returns a list of dicts with raw text from each cell plus the detail href.
EXTRACT_ROWS_JS = """
() => {
    const rows = [];
    document.querySelectorAll('table tbody tr').forEach(tr => {
        const tds = tr.querySelectorAll('td');
        if (tds.length < 2) return;
        const link = tr.querySelector('a[href*="/tender/view"]');
        rows.push({
            cell0: tds[0] ? tds[0].innerText.trim() : '',
            cell1: tds[1] ? tds[1].innerText.trim() : '',
            cell2: tds[2] ? tds[2].innerText.trim() : '',
            href:  link   ? link.getAttribute('href') : ''
        });
    });
    return rows;
}
"""

# Pagination: handles both "Next" buttons and numbered page links (e.g. "Pages: 1 2")
NEXT_PAGE_JS = """
() => {
    // Try rel=next or .next class first
    const relNext = document.querySelector('a[rel="next"], a.next');
    if (relNext) return relNext.getAttribute('href');

    // Try text-based "Next" / ">" / ">>" links
    const links = Array.from(document.querySelectorAll('a'));
    const nextBtn = links.find(l => /^(Next|>|>>)$/.test(l.textContent.trim()));
    if (nextBtn) return nextBtn.getAttribute('href');

    // Handle numbered pagination: find current page number and return link to page+1
    const pageLinks = links.filter(l => /[?&]page=[0-9]+/.test(l.getAttribute('href') || ''));
    if (pageLinks.length > 0) {
        const params = new URLSearchParams(window.location.search);
        const current = parseInt(params.get('page') || '1');
        const nextLink = pageLinks.find(l => {
            const p = new URLSearchParams(new URL(l.href).search);
            return parseInt(p.get('page') || '0') === current + 1;
        });
        if (nextLink) return nextLink.href;
    }
    return null;
}
"""


def _parse_cell0(text):
    """Parse 'ref\\nstatus\\ncategory' from cell 0."""
    parts = [p.strip() for p in text.split('\n') if p.strip()]
    ref      = parts[0] if len(parts) > 0 else ''
    status   = parts[1] if len(parts) > 1 else ''
    category = parts[2] if len(parts) > 2 else ''
    return ref, status, category


def _explicit_empty(text):
    """Conservative rendered empty-state markers; never infer empty from missing rows."""
    return any(re.fullmatch(
        r'(?:no (?:results|tenders)(?: found| available| to display)?[.!]?|'
        r'(?:total\s*:?\s*)?0 (?:results|tenders)(?: found)?[.!]?)',
        ' '.join(line.split()), re.I) for line in text.splitlines())


def _parse_cell1(text):
    """Parse 'title\\nIssued by: agency' from cell 1."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    title  = lines[0] if lines else ''
    agency = ''
    for line in lines[1:]:
        # Handles "Issued by: ...", "Prequalified by: ...", "Awarded to: ..."
        m = re.match(r'(?:Issued by|Prequalified by|Awarded to|Agency)[:\s]+(.+)', line, re.I)
        if m:
            agency = m.group(1).strip()
            break
    return title, agency


def _parse_cell2(text):
    """Parse release and closing dates from cell 2.
    Format: 'Released\\nDow, DD Mon YYYY HH:MM am/pm\\nClosing\\nDow, ...'
    """
    pub_date   = ''
    close_date = ''
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    try:
        rel_idx = next(i for i, l in enumerate(lines) if l.lower().startswith('released'))
        pub_date = lines[rel_idx + 1] if rel_idx + 1 < len(lines) else ''
    except StopIteration:
        pass
    try:
        clo_idx = next(i for i, l in enumerate(lines) if l.lower().startswith('clos'))
        close_date = lines[clo_idx + 1] if clo_idx + 1 < len(lines) else ''
    except StopIteration:
        pass
    return pub_date, close_date


class ActTenderSpider(scrapy.Spider):
    name = "act_tender"
    custom_settings = {
        'LOG_FILE': 'logs/act_tender.log',
        'DOWNLOAD_DELAY': 3,
        'AUTOTHROTTLE_ENABLED': True,
        'CONCURRENT_REQUESTS': 1,
        'CONCURRENT_REQUESTS_PER_DOMAIN': 1,
        'ROBOTSTXT_OBEY': False,
        'DOWNLOAD_HANDLERS': {
            'https': 'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
            'http':  'scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler',
        },
        'TWISTED_REACTOR': 'twisted.internet.asyncioreactor.AsyncioSelectorReactor',
        'PLAYWRIGHT_BROWSER_TYPE': 'chromium',
        'PLAYWRIGHT_LAUNCH_OPTIONS': {
            'headless': True,
            'args': ['--disable-blink-features=AutomationControlled'],
        },
        'PLAYWRIGHT_CONTEXTS': {
            'default': {
                'user_agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
                'locale': 'en-AU',
                'viewport': {'width': 1280, 'height': 720},
            }
        },
        'PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT': 30000,
    }

    BASE = 'https://www.tenders.act.gov.au'

    def start_requests(self):
        for url in [
            f'{self.BASE}/tenders/open',
            f'{self.BASE}/tenders/closed',
        ]:
            yield scrapy.Request(
                url, callback=self.parse,
                meta=PW_META, errback=self.errback,
            )

    async def parse(self, response):
        page = response.meta.get('playwright_page')
        try:
            self.logger.info(f"ACT listing: {response.url} (HTTP {response.status})")

            # ── Wait for table rows to appear (up to 15s) ────────────────
            try:
                await page.wait_for_selector('table tbody tr td', timeout=15000)
            except Exception as e:
                self.logger.warning(f"Table row wait timed out on {response.url}: {e}")

            # ── Extract all rows in one JS call ──────────────────────────
            rows = await page.evaluate(EXTRACT_ROWS_JS)
            self.logger.info(f"Rows found: {len(rows)} on {response.url}")

            if not rows:
                if _explicit_empty(await page.inner_text('body')):
                    self.logger.info('Explicitly empty ACT listing: %s', response.url)
                    self.crawler.stats.inc_value('crawl/act_empty_lists')
                    empty = getattr(self, '_empty_lists', set())
                    empty.add(response.url)
                    self._empty_lists = empty
                    return
                self.logger.error('No ACT rows extracted from %s; verify listing and selectors', response.url)
                self.crawler.stats.inc_value('crawl/extraction_failed')

            for row in rows:
                ref, status, category = _parse_cell0(row.get('cell0', ''))
                title, agency         = _parse_cell1(row.get('cell1', ''))
                pub_date, close_date  = _parse_cell2(row.get('cell2', ''))
                href = row.get('href', '')
                if not title and not ref:
                    continue
                # url is the unique key: a row without its own link gets a per-notice
                # address, never the shared listing URL (which would merge rows).
                url = (self.BASE + href) if href.startswith('/') else (
                    href or f"{response.url.split('#')[0]}#{ref or title}")

                full_text        = f"{title} {category} {agency}"
                illoura_relevant = is_relevant(full_text)

                # Tag whether it's health, IT, or both (for alerting purposes)
                t = full_text.lower()
                is_health = any(kw in t for kw in HEALTH_KEYWORDS)
                is_it     = any(kw in t for kw in IT_KEYWORDS)
                if is_health and is_it:
                    sector = 'health+IT'
                elif is_health:
                    sector = 'health'
                elif is_it:
                    sector = 'IT'
                else:
                    sector = ''

                self.logger.info(
                    f"  [{ref}] {title[:50]} | {agency[:25]} | relevant={illoura_relevant} sector={sector}"
                )

                yield {
                    'portal':           'act_tender',
                    'reference':        ref,
                    'title':            title,
                    'agency':           agency,
                    'category':         category,
                    'status':           status,
                    'close_date':       close_date,
                    'publish_date':     pub_date,
                    'description':      f"sector:{sector}" if sector else '',
                    'contact_name':     '',
                    'contact_email':    '',
                    'url':              url,
                    'illoura_relevant': illoura_relevant,
                    'scraped_at':       datetime.utcnow().isoformat(),
                }

            # ── Pagination ───────────────────────────────────────────────
            next_href = await page.evaluate(NEXT_PAGE_JS)
            if next_href and next_href != '#':
                next_url = (self.BASE + next_href) if next_href.startswith('/') else next_href
                self.logger.info(f"Next page: {next_url}")
                yield scrapy.Request(
                    next_url, callback=self.parse,
                    meta=PW_META, errback=self.errback,
                )

        finally:
            if page:
                await page.close()

    def closed(self, reason):
        roots = {f'{self.BASE}/tenders/open', f'{self.BASE}/tenders/closed'}
        if reason == 'finished' and roots.issubset(getattr(self, '_empty_lists', set())):
            self.crawler.stats.set_value('crawl/nothing_due', 1)

    def errback(self, failure):
        self.logger.error(f"Request failed: {failure.request.url} -- {failure.value}")

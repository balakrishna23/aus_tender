"""Shared AusTender (tenders.gov.au) listing logic.

AusTender search stops at 10,000 results, so large searches are split into
date windows. Every listing page is cross-checked against the "of N records"
total so a run can prove it saw every notice, and detail pages are only
requested for notices that are new or can still change.
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

import scrapy

from database_manager import DatabaseManager

SEARCH_CAP = 10000
GUID_RE = re.compile(r'/(?:Show|ShowClosed)/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})')
TOTAL_RE = re.compile(r'of\s*<strong>\s*([\d,]+)\s*</strong>\s*records', re.I)


def notice_guid(url):
    match = GUID_RE.search(url or '')
    return match.group(1).lower() if match else None


def parse_day(text):
    """Parse '2-Oct-2026', '02-Oct-2026 2:00 pm' or '2026-10-02'."""
    text = (text or '').strip()
    for fmt, size in (('%Y-%m-%d', 10), ('%d-%b-%Y', 11)):
        try:
            return datetime.strptime(text[:size].strip(), fmt).date()
        except ValueError:
            continue
    match = re.match(r'(\d{1,2}-[A-Za-z]{3}-\d{4})', text)
    return datetime.strptime(match.group(1), '%d-%b-%Y').date() if match else None


def austender_day(day):
    return f"{day.day}-{day.strftime('%b-%Y')}"


def date_windows(start, end, days):
    """Inclusive [start, end] split into consecutive windows of at most `days` days."""
    while start <= end:
        stop = min(start + timedelta(days=days - 1), end)
        yield start, stop
        start = stop + timedelta(days=1)


def search_url(path, **params):
    return f"https://www.tenders.gov.au/Search/{path}?{urlencode(params)}"


def _clean(parts):
    return ' '.join(' '.join(parts).split())


def field_value(response, label):
    """Read a labelled value independent of CSS token order or nested label text."""
    wanted = label.strip().rstrip(':').casefold()
    for block in response.css('div.list-desc'):
        actual = _clean(block.xpath('./span//text()').getall()).rstrip(' :').casefold()
        if actual == wanted:
            return _clean(block.css('div.list-desc-inner ::text').getall())
    return ''


def page_details(response):
    """Every labelled field on a notice page ({label: text}), including the contact block,
    so whatever the site shows is kept even when a spider has no column for it yet."""
    details = {}
    for block in response.xpath('//div[contains(@class, "list-desc")][span]'):
        label = _clean(block.xpath('./span//text()').getall()).rstrip(' :')
        value = _clean(block.xpath('./div[contains(@class, "list-desc-inner")]//text()').getall())
        if label and value:
            details.setdefault(label, value)
    for line in response.xpath('(//div[contains(@class, "contact-long")])[1]/p[not(contains(@class, "heading"))]'):
        label = _clean(line.xpath('./span//text()').getall()).rstrip(' :') or 'Name'  # unlabelled line: the name
        value = _clean(line.xpath('./node()[not(self::span)]//text() | ./text()').getall()).lstrip(': ')
        if value and value != label:
            details.setdefault(label if label.startswith('Contact') else f'Contact {label}', value)
    return details


class AusTenderListingMixin:
    """Walks AusTender listings and decides which detail pages to fetch.

    Spiders set ``id_label`` (e.g. "CN ID") and implement ``detail_callback``
    and ``should_fetch(guid, stored_url, listing_kind)``.
    """

    id_label = None
    # Newest-first lists without a usable total (e.g. "Last Updated"): walk until this many
    # pages in a row bring nothing new, never further than MAX_RECENT_PAGES.
    QUIET_PAGES = 3
    MAX_RECENT_PAGES = 60

    def _init_listing(self, since=None, refresh_existing=False):
        self.since = parse_day(since) if since else None
        self.refresh_existing = str(refresh_existing).lower() in ('true', '1', 'yes')
        self.today = date.today()
        self.db_manager = DatabaseManager()
        self.stored_urls = {}
        self.window_totals = {}
        self.window_seen = {}
        self.window_roots = {}
        self.empty_windows = set()
        self.skipped_known = 0
        self.recent_fetched = 0

    def load_stored(self):
        """Map notice GUID -> stored URL, so a run never re-downloads a final notice."""
        self.db_manager.initialize_database(self.name)
        for url in self.db_manager.load_column_map(self.name, 'url', 'url'):
            guid = notice_guid(url)
            if guid:
                self.stored_urls[guid] = url
        self.logger.info('Loaded %s stored notices', len(self.stored_urls))

    def refetch_missing_details(self, table, since=None):
        """Re-open stored notices saved before all_details was captured, published since `since`
        (a date; None = all). Resumable: each saved notice drops out of the next run's list."""
        self.db_manager.initialize_database(self.name)
        with self.db_manager.get_connection(self.name) as conn:
            rows = conn.execute(f'SELECT url, publish_date FROM {table} WHERE all_details IS NULL '
                                f"AND url LIKE 'https://www.tenders.gov.au/%'").fetchall()
        urls = [url for url, published in rows if since is None or (parse_day(published) or since) >= since]
        self.logger.info('Re-fetching %s stored notices to capture every page detail', len(urls))
        for url in urls:
            yield scrapy.Request(url, callback=self.detail_callback, meta={'refresh_existing': True})

    def listing_request(self, url, kind, window=None):
        return scrapy.Request(url, callback=self.parse,
                              meta={'listing_kind': kind, 'window': window or url})

    def recent_request(self, url, kind):
        """A newest-first listing, walked only as far as it brings new notices."""
        return scrapy.Request(url, callback=self.parse_recent,
                              meta={'listing_kind': kind, 'page_no': 1, 'quiet_pages': 0})

    def _notice_links(self, response):
        links = []
        for block in response.css('div.list-desc'):
            label = _clean(block.xpath('./span//text()').getall()).rstrip(' :')
            if label.casefold() == self.id_label.casefold():
                links.extend(block.css('div.list-desc-inner a::attr(href)').getall())
        return links

    def _is_known(self, guid, kind):
        stored = self.stored_urls.get(guid) if guid else None
        return not self.refresh_existing and stored and not self.should_fetch(guid, stored, kind)

    def parse_recent(self, response):
        meta = response.request.meta if response.request is not None else {}
        kind, page_no = meta.get('listing_kind', 'recent'), meta.get('page_no', 1)
        links = self._notice_links(response)
        if not links and 'There are no results that match your selection' not in response.text:
            self._record_failure('No notice links extracted from %s; verify listing and selectors', response.url)
        fresh = 0
        for link in links:
            if self._is_known(notice_guid(response.urljoin(link)), kind):
                continue
            fresh += 1
            yield response.follow(link, callback=self.detail_callback,
                                  meta={'refresh_existing': True, 'listing_kind': kind})
        self.recent_fetched += fresh
        quiet = 0 if fresh else meta.get('quiet_pages', 0) + 1
        next_page = response.css('li.next a::attr(href)').get()
        if next_page and quiet < self.QUIET_PAGES and page_no < self.MAX_RECENT_PAGES:
            yield response.follow(next_page, callback=self.parse_recent,
                                  meta={'listing_kind': kind, 'page_no': page_no + 1, 'quiet_pages': quiet})
        elif next_page and quiet < self.QUIET_PAGES:
            self.logger.warning('Recently updated list: stopped at the %s-page limit while still finding '
                                'new notices', self.MAX_RECENT_PAGES)
        else:
            self.logger.info('Recently updated list: %s new notices in %s pages', self.recent_fetched, page_no)

    def parse(self, response):
        meta = response.request.meta if response.request is not None else {}
        kind = meta.get('listing_kind', 'default')
        window = meta.get('window', response.url)
        self.window_roots.setdefault(window, response.url)
        if meta.get('coverage_reset'):
            self.window_seen[window] = set()
        total = TOTAL_RE.search(response.text)
        if total and (window not in self.window_totals or meta.get('coverage_reset')):
            reported = int(total.group(1).replace(',', ''))
            self.window_totals[window] = reported
            if reported >= SEARCH_CAP:
                self._record_failure('Search window hit the %s result cap, narrow it: %s', SEARCH_CAP, window)

        links = self._notice_links(response)
        empty = 'There are no results that match your selection' in response.text
        if empty:
            self.window_totals.setdefault(window, 0)
        if not links and (empty or (total and int(total.group(1).replace(',', '')) == 0)):
            self.empty_windows.add(window)
        if not links and not empty and not (total and int(total.group(1).replace(',', '')) == 0):
            self.logger.error('No notice links extracted from %s; verify listing and selectors', response.url)
            self.crawler.stats.inc_value('crawl/extraction_failed')

        seen = self.window_seen.setdefault(window, set())
        for link in links:
            url = response.urljoin(link)
            guid = notice_guid(url)
            seen.add(guid or url)
            if self._is_known(guid, kind):
                self.skipped_known += 1
                self.crawler.stats.inc_value('crawl/items_unchanged')
                continue
            yield response.follow(link, callback=self.detail_callback,
                                  meta={'refresh_existing': True, 'listing_kind': kind})

        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page, callback=self.parse,
                                  dont_filter=bool(meta.get('coverage_recheck')),
                                  meta={'listing_kind': kind, 'window': window,
                                        'coverage_recheck': meta.get('coverage_recheck', False)})
        elif len(seen) < self.window_totals.get(window, 0) and not meta.get('coverage_recheck'):
            # Notices can be withdrawn while paging. Rewalk once against fresh totals.
            self.logger.warning('Rechecking changed listing total for %s', window)
            yield scrapy.Request(self.window_roots[window], callback=self.parse, dont_filter=True,
                                 meta={'listing_kind': kind, 'window': window,
                                       'coverage_recheck': True, 'coverage_reset': True})

    def _record_failure(self, message, *args):
        self.logger.error(message, *args)
        self.crawler.stats.inc_value('crawl/extraction_failed')

    def closed(self, reason):
        listed = sum(len(v) for v in self.window_seen.values())
        reported = sum(self.window_totals.get(w, 0) for w in self.window_seen)
        for window, seen in self.window_seen.items():
            expected = self.window_totals.get(window)
            # Listings can gain a few notices mid-crawl; losing any is a coverage gap.
            if expected is not None and len(seen) < expected:
                self.logger.error('Coverage gap: listed %s of %s notices in %s',
                                  len(seen), expected, window)
                self.crawler.stats.inc_value('crawl/coverage_failed')
        self.logger.info('Coverage: listed %s of %s reported notices across %s searches; '
                         'skipped %s already-final notices; reason=%s',
                         listed, reported, len(self.window_seen), self.skipped_known, reason)

        stats = self.crawler.stats.get_stats()
        if (reason == 'finished' and self.window_seen
                and self.empty_windows == set(self.window_seen) and not listed
                and not any(value for key, value in stats.items()
                            if key.startswith('downloader/response_status_count/')
                            and int(key.rsplit('/', 1)[1]) >= 400)
                and not any(stats.get(k) for k in (
                    'item_scraped_count', 'crawl/extraction_failed', 'crawl/coverage_failed',
                    'retry/max_reached', 'database/items_failed',
                    'downloader/exception_count', 'spider_exceptions/count'))):
            self.crawler.stats.set_value('crawl/nothing_due', 1)

import sqlite3
from contextlib import closing
from datetime import timedelta

import scrapy

from project_paths import database_directory
from spiders.austender_common import AusTenderListingMixin, page_details, parse_day


class AusContractSpider(AusTenderListingMixin, scrapy.Spider):
    """Contract notice pages, for the fields AusTender's API leaves out (SON ID, ATM ID,
    consultancy, confidentiality, suppliers invited, extension options, office postcode).

    Which notices exist comes from the API (austender_api.py, run first): every notice and
    amendment published in the window that isn't stored yet. Only the notices' own pages are
    requested (/Cn/Show, allowed by robots.txt); the /Search pages robots.txt disallows are not.

    Default (incremental): from a few days before the last complete run to today.
    ``-a since=2025-01-01`` fills in every notice published since then.
    ``-a scope=active_it`` fills in contracts still running in the ICT-services and labour-hire families.
    Contract notices never change once published (amendments get their own CN ID).
    """

    name = "aus_contract"
    id_label = "CN ID"
    custom_settings = {
        'LOG_FILE': 'logs/aus_contract.log',
        'LOG_LEVEL': 'INFO',
        'AUTOTHROTTLE_ENABLED': True,
    }
    OVERLAP_DAYS = 3
    # Without a stored health canary, retain the conservative fallback.
    # AusTender lists some notices in its API before their page is on the site (they redirect
    # to /System/NotFound, often overnight amendments). A run where every new notice is like
    # that has nothing to save and is a normal day, up to this many; more than this at once
    # looks like the site itself changing, so the run still fails loudly.
    MAX_UNPUBLISHED_NOTICES = 25
    CANARY_NOTICES = 3

    def __init__(self, since=None, refresh_existing=False, refetch=None, scope=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_listing(since, refresh_existing)
        # refetch=missing_supplier: re-open stored notices from the last year whose supplier
        # town is blank (saved by an older version of this spider). Resumable: rerun to continue.
        # refetch=missing_details: re-open notices saved before every page field was captured,
        # published since `since` (all of them without it).
        self.refetch = refetch
        self.scope = scope
        self.detail_callback = self.parse_contract
        self.window_start = None

    def start_requests(self):
        if self.refetch == 'missing_supplier':
            yield from self._refetch_missing_supplier()
            return
        if self.refetch == 'missing_details':
            yield from self.refetch_missing_details('contracts', self.since)
            return
        if getattr(self, 'start_urls', None):
            for url in self.start_urls:
                yield scrapy.Request(url, callback=self.parse_contract)
            return
        self.db_manager.initialize_database(self.name)
        stored = set(self.db_manager.load_column_map(self.name, 'cn_id', 'cn_id'))
        pending, expired = self._pending_notices(stored)
        if self.scope == 'active_it':
            # Original notices only: the panel (SON ID) and the other page-only fields are set when
            # the contract is let; amendments don't change them.
            notices = self._api_notices("v.amendment_no = 0 AND v.cn_id IN (SELECT cn_id FROM contracts "
                                        "WHERE current_end_date >= ? AND category_family IN "
                                        "('ICT services', 'labour hire and recruitment'))", self.today.isoformat())
            self.logger.info('Running ICT-services and labour-hire contracts: %s notices', len(notices))
        else:
            self.window_start = self.since or self._incremental_start()
            notices = self._api_notices('v.published_on >= ?', self.window_start.isoformat())
            self.logger.info('Contract notices published %s to %s: %s in the API', self.window_start,
                             self.today, len(notices))
        todo = dict((notice, url) for notice, url in notices
                    if notice not in stored and notice not in expired)
        todo.update(pending)
        self.logger.info('%s notices to fetch, %s already stored', len(todo), sum(notice in stored for notice, _ in notices))
        self.requested_notices = len(todo)
        if not todo:
            # Nothing new in the API is a normal day, not a failed crawl: run_spiders reads this stat.
            self.crawler.stats.set_value('crawl/nothing_due', 1)
        # Direct request enumeration (without an attached Scrapy crawler) is also used by
        # offline callers; only a running crawler can record a canary's health result.
        if todo and getattr(self, 'crawler', None) is not None:
            with self.db_manager.get_connection(self.name) as conn:
                canaries = conn.execute("SELECT cn_id, url FROM contracts WHERE COALESCE(url, '') != '' "
                                        "ORDER BY id DESC LIMIT ?", (self.CANARY_NOTICES,)).fetchall()
            self.canary_requested = bool(canaries)
            self.crawler.stats.set_value('contract/canary_ok', 0)
            # Several recent notices: one withdrawn page must not make a quiet day look like
            # a broken site. The site is healthy when any of them opens correctly.
            for notice, url in canaries:
                yield scrapy.Request(url, callback=self.parse_contract, dont_filter=True,
                                     meta={'contract_canary': True, 'notice_id': notice})
        for notice, url in todo.items():
            # dont_filter: unpublished notices all redirect to the same NotFound page, and each
            # must still reach parse_contract to be counted rather than dropped as a duplicate.
            yield scrapy.Request(url, callback=self.parse_contract, dont_filter=True,
                                 meta={'refresh_existing': True, 'notice_id': notice, 'notice_url': url})

    async def start(self):
        for request in self.start_requests():
            yield request

    def _api_notices(self, where, value):
        """(notice id, page URL) for API versions matching `where`."""
        path = database_directory() / 'contract_api.db'
        if not path.exists():
            self._record_failure('No contract API database at %s: run austender_api.py first', path)
            return []
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as api:
            return api.execute(f'SELECT DISTINCT v.notice_id, v.page_url FROM contract_versions v '
                               f'WHERE {where} AND v.page_url IS NOT NULL ORDER BY v.notice_id',
                               (value,)).fetchall()

    @staticmethod
    def _pending_schema(conn):
        conn.execute('CREATE TABLE IF NOT EXISTS pending_notices ('
                     'notice_id TEXT PRIMARY KEY, page_url TEXT NOT NULL, '
                     'first_seen TEXT NOT NULL, last_checked TEXT NOT NULL, '
                     'attempts INTEGER NOT NULL DEFAULT 1)')

    def _pending_notices(self, stored):
        """Keep expired rows as tombstones so an API backfill cannot restart retries."""
        pending, expired = {}, set()
        with self.db_manager.get_connection(self.name) as conn:
            self._pending_schema(conn)
            conn.execute('DELETE FROM pending_notices WHERE notice_id IN (SELECT cn_id FROM contracts)')
            rows = conn.execute('SELECT notice_id, page_url, first_seen FROM pending_notices').fetchall()
            for notice, url, first_seen in rows:
                if notice in stored:
                    continue
                # An unreadable first-seen date must not stop the whole collection: such a
                # row is treated as first seen today and so keeps being retried.
                if (self.today - (parse_day(first_seen) or self.today)).days >= 30:
                    expired.add(notice)
                    self.logger.warning('Giving up unpublished notice %s after 30 days', notice)
                else:
                    pending[notice] = url
                    conn.execute('UPDATE pending_notices SET last_checked = ?, attempts = attempts + 1 '
                                 'WHERE notice_id = ?', (self.today.isoformat(), notice))
            conn.commit()
        return pending, expired

    def _remember_pending(self, response):
        notice = response.meta.get('notice_id')
        url = response.meta.get('notice_url')
        if not notice or not url:
            return
        with self.db_manager.get_connection(self.name) as conn:
            self._pending_schema(conn)
            conn.execute('INSERT INTO pending_notices '
                         '(notice_id, page_url, first_seen, last_checked, attempts) VALUES (?, ?, ?, ?, 1) '
                         'ON CONFLICT(notice_id) DO UPDATE SET page_url = excluded.page_url, '
                         'last_checked = excluded.last_checked',
                         (notice, url, self.today.isoformat(), self.today.isoformat()))
            conn.commit()

    def _refetch_missing_supplier(self):
        self.db_manager.initialize_database(self.name)
        cutoff = self.today - timedelta(days=365)
        with self.db_manager.get_connection(self.name) as conn:
            rows = conn.execute("SELECT url, publish_date FROM contracts WHERE COALESCE(supplier_town_city, '') = '' "
                                "AND COALESCE(supplier_name, '') != ''").fetchall()
        urls = [url for url, published in rows if (parse_day(published) or cutoff) >= cutoff]
        self.logger.info('Re-fetching %s notices with a blank supplier town', len(urls))
        for url in urls:
            yield scrapy.Request(url, callback=self.parse_contract, meta={'refresh_existing': True})

    def _incremental_start(self):
        """Resume from the last date a run proved complete, never from the newest row."""
        covered = parse_day(self.db_manager.get_state(self.name, 'covered_through') or '')
        if covered is None:
            return self.today - timedelta(days=30)
        return min(covered, self.today) - timedelta(days=self.OVERLAP_DAYS)

    def closed(self, reason):
        stats = self.crawler.stats.get_stats()
        requested = getattr(self, 'requested_notices', 0)
        unpublished = stats.get('contract/page_not_found', 0)
        if (reason == 'finished' and requested and not stats.get('item_scraped_count')
                and unpublished == requested
                and not any(stats.get(k) for k in ('crawl/extraction_failed', 'retry/max_reached',
                                                   'database/items_failed'))
                and (stats.get('contract/canary_ok') == 1 if getattr(self, 'canary_requested', False)
                     else requested <= self.MAX_UNPUBLISHED_NOTICES)):
            self.logger.info('All %s new notices have no page on the site yet (not yet published or '
                             'withdrawn); nothing to save, later runs retry them', requested)
            self.crawler.stats.set_value('crawl/nothing_due', 1)
        complete = reason == 'finished' and not any(
            stats.get(k) for k in ('crawl/extraction_failed', 'retry/max_reached', 'database/items_failed',
                                   'downloader/response_status_count/404'))
        if getattr(self, 'requested_notices', 0):
            with self.db_manager.get_connection(self.name) as conn:
                self._pending_schema(conn)
                conn.execute('DELETE FROM pending_notices WHERE notice_id IN (SELECT cn_id FROM contracts)')
                conn.commit()
        if complete and self.window_start and not self.refetch:
            previous = parse_day(self.db_manager.get_state(self.name, 'covered_through') or '')
            # A backfill only extends coverage if it joins up with what was already covered.
            if previous is None or self.window_start <= previous + timedelta(days=1):
                self.db_manager.set_state(self.name, 'covered_through', self.today.isoformat())
                self.logger.info('Contract notices complete through %s', self.today)

    def should_fetch(self, guid, stored_url, listing_kind):
        return False

    def parse_contract(self, response):
        if '/System/NotFound' in response.url:
            # In the API but not (or no longer) shown on the site, e.g. withdrawn: nothing to add.
            self.logger.info('No page on the site for %s', response.meta.get('notice_id') or response.url)
            if response.meta.get('contract_canary'):
                return  # stays 0 unless another canary succeeds
            self.crawler.stats.inc_value('contract/page_not_found')
            self._remember_pending(response)
            return

        def get_detail(label):
            xpath = f'//div[@class="list-desc"][span/text()="{label}:"]/div[@class="list-desc-inner"]//text()'
            results = response.xpath(xpath).getall()
            for result in results:
                if result.strip():
                    return result.strip()
            return ""

        def get_agency_detail(label):
            return response.xpath(f'//div[@class="contact-long"]//p[normalize-space(span) = "{label}:"]/text()[last()]').get(default="").strip()

        def get_supplier_detail(label):
            return response.xpath(f'//div[@class="list-desc"][span/text()="{label}:"]/div[@class="list-desc-inner"]//text()').get(default="").strip()

        publish_date = get_detail("Publish Date") or get_detail("Amendment Publish Date")
        son_link = response.xpath('//div[@class="list-desc"][span/text()="SON ID:"]//a/@href').get()

        if response.meta.get('contract_canary'):
            # Same required identity fields as DatabasePipeline, plus the expected CN identity.
            # Return before yielding: no database writes, activity events or scraped items.
            ok = (response.status == 200 and get_detail("CN ID") == response.meta.get('notice_id')
                  and bool(get_detail("Agency")))
            if ok:
                self.crawler.stats.set_value('contract/canary_ok', 1)
            return

        yield {
            "Professional services": response.css('p.lead::text').get(),
            "Contact Name": get_agency_detail("Contact Name"),
            "Email Address": response.xpath('//div[@class="contact-long"]//p[normalize-space(span) = "Email Address:"]/a/text()').get(default="").strip(),
            "Office Postcode": get_agency_detail("Office Postcode"),
            "CN ID": get_detail("CN ID"),
            "Agency": get_detail("Agency"),
            "Publish Date": publish_date,
            "Category": get_detail("Category"),
            "Execution Date": get_detail("Execution Date"),
            "Contract Period": get_detail("Contract Period"),
            "Extension Options": get_detail("Extension Options"),
            "Max End Date": get_detail("Max End Date"),
            "Contract Value (AUD)": get_detail("Contract Value (AUD)"),
            "Description": get_detail("Description"),
            "Procurement Method": get_detail("Procurement Method"),
            "SON ID": get_detail("SON ID"),
            "Suppliers Invited": get_detail("Suppliers Invited"),
            "Confidentiality - Contract": get_detail("Confidentiality - Contract"),
            "Confidentiality - Outputs": get_detail("Confidentiality - Outputs"),
            "Confidentiality Reason(s) - Outputs": get_detail("Confidentiality Reason(s) - Outputs"),
            "Consultancy": get_detail("Consultancy"),
            "Agency Reference ID": get_detail("Agency Reference ID"),
            "ATM ID": get_detail("ATM ID"),
            "Australian Business Engaged": get_detail("Was an Australian business Engaged?"),
            "SON URL": response.urljoin(son_link) if son_link else "",
            "Contact Phone": get_agency_detail("Phone"),
            "Branch": get_agency_detail("Branch"),
            "Division": get_agency_detail("Division"),
            "All Details": page_details(response),
            "Supplier Name": get_supplier_detail("Name"),
            "Supplier Town/City": get_supplier_detail("Town/City"),
            "Supplier Postcode": get_supplier_detail("Postcode"),
            "Supplier State/Territory": get_supplier_detail("State/Territory"),
            "Supplier Country": get_supplier_detail("Country"),
            "Supplier ABN": get_supplier_detail("ABN"),
            "URL": response.url,
        }

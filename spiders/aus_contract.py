from datetime import timedelta

import scrapy

from spiders.austender_common import (AusTenderListingMixin, austender_day, date_windows, page_details,
                                      parse_day, search_url)


class AusContractSpider(AusTenderListingMixin, scrapy.Spider):
    """Contract notices by publish date, plus the "recently updated" list.

    Default (incremental): from a few days before the last proven-complete run to today.
    ``-a since=2025-01-01`` backfills from that date. Contract notices never change
    once published (amendments get their own CN ID), so stored ones are skipped.

    The site files an amendment (CN123-A1) under the ORIGINAL contract's publish date,
    so a new amendment to an older contract never appears in a recent date window.
    The newest-first "Last Updated" list catches those.
    """

    name = "aus_contract"
    id_label = "CN ID"
    custom_settings = {
        'LOG_FILE': 'logs/aus_contract.log',
        'LOG_LEVEL': 'INFO',
        'AUTOTHROTTLE_ENABLED': True,
    }
    # ~1,100 notices are published per week; weekly windows stay far below the 10,000 cap.
    WINDOW_DAYS = 7
    OVERLAP_DAYS = 3

    def __init__(self, since=None, refresh_existing=False, refetch=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_listing(since, refresh_existing)
        # refetch=missing_supplier: re-open stored notices from the last year whose supplier
        # town is blank (saved by an older version of this spider). Resumable: rerun to continue.
        # refetch=missing_details: re-open notices saved before every page field was captured,
        # published since `since` (all of them without it).
        self.refetch = refetch
        self.detail_callback = self.parse_contract

    def start_requests(self):
        if self.refetch == 'missing_supplier':
            yield from self._refetch_missing_supplier()
            return
        if self.refetch == 'missing_details':
            yield from self.refetch_missing_details('contracts', self.since)
            return
        self.load_stored()
        if getattr(self, 'start_urls', None):
            for url in self.start_urls:
                yield self.listing_request(url, 'default')
            return
        since = self.since or self._incremental_start()
        self.window_start = since
        self.logger.info('Contract notices published %s to %s', since, self.today)
        for start, end in date_windows(since, self.today, self.WINDOW_DAYS):
            yield self.listing_request(search_url(
                'CnAdvancedSearch', SearchFrom='CnSearch', Type='Cn', AgencyStatus='-1',
                KeywordTypeSearch='AllWord', DateType='Publish Date', OrderBy='Publish Date',
                DateStart=austender_day(start), DateEnd=austender_day(end)), 'published')
        yield self.recent_request(search_url(
            'CnAdvancedSearch', SearchFrom='CnSearch', Type='Cn', AgencyStatus='-1',
            KeywordTypeSearch='AllWord', OrderBy='Last Updated'), 'updated')

    async def start(self):
        for request in self.start_requests():
            yield request

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
        """Resume from the last date a run proved complete, never from the newest row.

        Starting from the newest stored notice would silently skip any gap left by an
        interrupted or partial run.
        """
        covered = parse_day(self.db_manager.get_state(self.name, 'covered_through') or '')
        if covered is None:
            dates = self.db_manager.load_column_map(self.name, 'publish_date', 'publish_date')
            covered = max(filter(None, (parse_day(d) for d in dates)), default=None)
            self.logger.warning('No completed-run marker yet; resuming from newest stored notice %s', covered)
        if covered is None:
            return self.today - timedelta(days=30)
        return min(covered, self.today) - timedelta(days=self.OVERLAP_DAYS)

    def closed(self, reason):
        super().closed(reason)
        stats = self.crawler.stats.get_stats()
        complete = reason == 'finished' and not any(
            stats.get(k) for k in ('crawl/extraction_failed', 'crawl/coverage_failed', 'retry/max_reached',
                                   'database/items_failed'))
        if complete and not getattr(self, 'start_urls', None) and not self.refetch:
            previous = parse_day(self.db_manager.get_state(self.name, 'covered_through') or '')
            # A backfill only extends coverage if it joins up with what was already covered.
            if previous is None or self.window_start <= previous + timedelta(days=1):
                self.db_manager.set_state(self.name, 'covered_through', self.today.isoformat())
                self.logger.info('Contract notices complete through %s', self.today)

    def should_fetch(self, guid, stored_url, listing_kind):
        return False

    def parse_contract(self, response):
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

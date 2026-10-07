from datetime import datetime, timedelta

import scrapy

from spiders.austender_common import AusTenderListingMixin, notice_guid, page_details, parse_day


class StandingOfferSpider(AusTenderListingMixin, scrapy.Spider):
    """Standing offer notices (panels), from their own pages (/Son/Show, allowed by robots.txt;
    the /Search and /Son/List pages it disallows are not used).

    New panels are found through contract notices: each notice bought under a standing offer
    links to it (aus_contract stores the link), so every panel in use is picked up. Stored
    panels that are still current are re-checked once their copy is older than RECHECK_DAYS
    (SONs get amended and gain suppliers).
    """

    name = "standing_offer"
    id_label = "SON ID"
    custom_settings = {
        'LOG_FILE': 'logs/standing_offer.log',
        'LOG_LEVEL': 'INFO',
        'AUTOTHROTTLE_ENABLED': True,
    }
    RECHECK_DAYS = 7

    def __init__(self, since=None, refresh_existing=False, refetch=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.full = str(since).lower() == 'all'
        self._init_listing(None, refresh_existing)
        # refetch=missing_details: re-open stored offers saved before every page field was
        # captured, published since `since` (YYYY-MM-DD; all of them with since=all or none).
        self.refetch = refetch
        self.refetch_since = None if self.full else parse_day(since) if since else None
        self.detail_callback = self.parse_son
        self.checked_at = {}

    def start_requests(self):
        if self.refetch == 'missing_details':
            yield from self.refetch_missing_details('standing_offers', self.refetch_since)
            return
        self.load_stored()
        if getattr(self, 'start_urls', None):
            for url in self.start_urls:
                yield scrapy.Request(url, callback=self.parse_son, meta={'refresh_existing': True})
            return
        with self.db_manager.get_connection(self.name) as conn:
            stored = conn.execute('SELECT url, scraped_at, standing_offer_period FROM standing_offers').fetchall()
        due = [url for url, checked, period in stored if self._current(period) and self._stale(checked)]
        self.db_manager.initialize_database('aus_contract')
        linked = self.db_manager.load_column_map('aus_contract', 'son_url', 'son_url')
        new = sorted({url for url in linked if url and notice_guid(url) not in self.stored_urls})
        self.logger.info('Standing offers: %s new from contract notices; %s current ones due a re-check',
                         len(new), len(due))
        for url in new + due:
            yield scrapy.Request(url, callback=self.parse_son, meta={'refresh_existing': True})

    async def start(self):
        for request in self.start_requests():
            yield request

    def _current(self, period):
        end = parse_day((period or '').split(' to ')[-1])
        return end is None or end >= self.today

    def _stale(self, checked):
        try:
            return datetime.now() - datetime.fromisoformat(str(checked)) > timedelta(days=self.RECHECK_DAYS)
        except ValueError:
            return True

    def should_fetch(self, guid, stored_url, listing_kind):
        return False

    def parse_son(self, response):
        def get_detail(label):
            return response.xpath(f'string(//div[@class="list-desc"][span/text()="{label}:"]/div[@class="list-desc-inner"])').get(default="").strip()

        def get_contact_detail(label):
            return response.xpath(f'//div[@class="contact-long"]//p[normalize-space(span) = "{label}:"]/text()[last()]').get(default="").strip()

        def get_table_data(table_id):
            suppliers = []
            table = response.xpath(f'//div[@id="{table_id}"]//table/tbody/tr')
            for row in table[1:]:
                suppliers.append({
                    "Supplier Name": row.xpath('./td[1]//text()').get(default="").strip(),
                    "ABN": row.xpath('./td[2]//text()').get(default="").strip(),
                    "State": row.xpath('./td[4]//text()').get(default="").strip(),
                    "Postcode": row.xpath('./td[5]//text()').get(default="").strip(),
                    "Deed Start Date": row.xpath('./td[6]//text()').get(default="").strip(),
                    "Deed End Date": row.xpath('./td[7]//text()').get(default="").strip(),
                })
            return suppliers

        def participant_agencies():
            """The 'Participant Agencies' section (div#Agency): a table of agency names when the
            offer is limited to certain agencies; just a sentence when all agencies may use it."""
            names = [n.strip() for n in response.xpath('//div[@id="Agency"]//table//tr/td[1]//text()').getall()]
            return [{"Agency Name": n} for n in names if n]

        # Extract Panel ID and Panel URL if this is a panel arrangement
        panel_id = get_detail("Panel ID") or get_detail("PNL ID")
        panel_url = ""
        
        # Look for panel links and make them absolute URLs
        panel_link = response.xpath('//a[contains(@href, "/Panel/Show/")]/@href').get()
        if panel_link:
            panel_url = response.urljoin(panel_link)

        yield {
            "Title": response.css('p.lead::text').get(),
            "Contact Name": response.xpath('//div[@class="contact-long"]/p[2]/text()').get(default="").strip(),
            "Email Address": response.xpath('//div[@class="contact-long"]//a[contains(@href, "mailto")]/text()').get(default="").strip(),
            "Office Postcode": get_contact_detail("Office Postcode"),
            "Contact Phone": get_contact_detail("Phone"),
            "All Details": page_details(response),
            "SON ID": get_detail("SON ID"),
            "Agency": get_detail("Agency"),
            "Publish Date": get_detail("Publish Date"),
            "Primary Category": get_detail("Primary Category"),
            "Standing Offer Period": get_detail("Standing Offer Period"),
            "Description": get_detail("Description"),
            "Amendments": get_detail("Amendments"),
            "Procurement Method": get_detail("Procurement Method"),
            "ATM ID": get_detail("ATM ID"),
            "Multi Agency Access": get_detail("Multi Agency Access"),
            "Multi Agency Access Type": get_detail("Multi Agency Access Type"),
            "Panel Arrangement": get_detail("Panel Arrangement"),
            "Panel ID": panel_id,
            "Panel URL": panel_url,
            "Agency Reference ID": get_detail("Agency Reference ID"),
            "Current Suppliers": get_table_data("CurrentSuppliers"),
            "Previous Suppliers": get_table_data("PreviousSuppliers"),
            "Participant Agencies": participant_agencies(),
            "URL": response.url,
        }

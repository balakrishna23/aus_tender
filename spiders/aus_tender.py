import scrapy

from spiders.austender_common import AusTenderListingMixin, notice_guid, page_details, field_value

ATM_LIST = 'https://www.tenders.gov.au/Atm'


class AusTenderSpider(AusTenderListingMixin, scrapy.Spider):
    """Approaches to market (ATMs).

    Every run walks AusTender's Current ATM list (/Atm, allowed by robots.txt; the /Search
    pages it disallows are not used) and fetches every open ATM (they get addenda and new close
    dates). A stored ATM that has dropped off the list is opened once more: its page then
    redirects to the closed version, which is final.
    """

    name = "aus_tender"
    id_label = "ATM ID"
    custom_settings = {
        'LOG_FILE': 'logs/aus_tender.log',
        'LOG_LEVEL': 'INFO',
        'AUTOTHROTTLE_ENABLED': True,
    }

    def __init__(self, since=None, refresh_existing=False, refetch=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._init_listing(since, refresh_existing)
        # refetch=missing_details: re-open stored ATMs saved before every page field was
        # captured, published since `since` (all of them without it).
        self.refetch = refetch
        self.detail_callback = self.parse_tender

    def start_requests(self):
        if self.refetch == 'missing_details':
            yield from self.refetch_missing_details('tenders', self.since)
            return
        if self.since:
            self.logger.warning('since= is ignored: the archive is only searchable through /Search, '
                                'which robots.txt disallows')
        self.load_stored()
        with self.db_manager.get_connection(self.name) as conn:
            self.gone = {key[5:] for (key,) in conn.execute("SELECT key FROM crawl_state WHERE key LIKE 'gone:%'")}
        if getattr(self, 'start_urls', None):
            for url in self.start_urls:
                yield self.listing_request(url, 'default')
            return
        yield self.listing_request(ATM_LIST, 'published')

    async def start(self):
        for request in self.start_requests():
            yield request

    def should_fetch(self, guid, stored_url, listing_kind):
        # Open notices change; a stored closed notice is final.
        return listing_kind in ('published', 'default') or '/ShowClosed/' not in stored_url

    def parse(self, response):
        yield from super().parse(response)
        window = response.meta.get('window', response.url)
        if (response.css('li.next a::attr(href)').get()
                or response.meta.get('listing_kind') != 'published'
                or len(self.window_seen.get(window, set())) < self.window_totals.get(window, 0)):
            return
        # Last page of the list: anything stored as open but no longer listed has closed.
        listed = self.window_seen.get(window, set())
        dropped = [url for guid, url in self.stored_urls.items()
                   if '/ShowClosed/' not in url and guid not in listed and guid not in self.gone]
        self.logger.info('Current ATM list: %s listed; re-checking %s stored ATMs no longer listed',
                         len(listed), len(dropped))
        for url in dropped:
            yield scrapy.Request(url, callback=self.parse_tender, meta={'refresh_existing': True})

    def parse_tender(self, response):
        if '/System/NotFound' in response.url:
            # Withdrawn: the page no longer exists. Remember it so it isn't re-checked every run.
            guid = notice_guid((response.meta.get('redirect_urls') or [response.url])[0])
            if guid:
                self.db_manager.set_state(self.name, f'gone:{guid}', self.today.isoformat())
            self.logger.info('ATM page no longer on the site: %s', guid)
            return
        """
        Parse individual ATM tender page
        Updated with tested selectors for ATM Documents and Addenda links
        """
        def get_value(label, all_text=False):
            selector = f'//div[contains(concat(" ",normalize-space(@class)," ")," list-desc ")][normalize-space(span)="{label}" or normalize-space(span)="{label}:"]/div[contains(concat(" ",normalize-space(@class)," ")," list-desc-inner ")]'
            if all_text:
                parts = response.xpath(selector + '//text()').getall()
                cleaned_parts = [part.strip() for part in parts if part.strip() and part.strip() not in ['(ACT Local Time)', 'Show close time for other time zones']]
                return " ".join(cleaned_parts)
            else:
                return field_value(response, label)

        close_date_raw = get_value("Close Date & Time", all_text=True)
        close_date = close_date_raw

        # Extract ATM Documents link using tested selector
        atm_docs_link_relative = response.xpath('//a[@class="rBtn" and contains(text(), "ATM Documents")]/@href').get()
        atm_docs_link_absolute = response.urljoin(atm_docs_link_relative) if atm_docs_link_relative else ""

        # Extract Addenda link using tested selector
        addenda_link_relative = response.xpath('//a[contains(text(), "View Addenda")]/@href').get()
        addenda_link_absolute = response.urljoin(addenda_link_relative) if addenda_link_relative else ""
        
        # Extract contact email using tested selector (fallback method)
        contact_email_href = response.xpath('//a[contains(@href, "mailto:")]/@href').get()
        contact_email_from_href = contact_email_href.replace('mailto:', '') if contact_email_href else ""

        contact_name_selector = response.css("div.contact-long p:nth-child(2)::text").get(default="").strip()
        if not contact_name_selector:
            contact_name_selector = response.xpath('//div[contains(@class, "contact-long")]/p[1]/text()').get(default="").strip()

        contact_phone_selector = response.xpath('//div[contains(@class, "contact-long")]//p[span/label/@for="PhoneNumber"]/br/following-sibling::text()').get(default="").strip()
        if not contact_phone_selector:
            # The page repeats the contact block (desktop and mobile layouts): read the first only.
            contact_phone_selector = " ".join("".join(response.xpath('(//div[contains(@class, "contact-long")])[1]//p[span/label[normalize-space(text())="Phone"]]/text()').getall()).split())

        yield {
            "Service Name": response.css('p.lead::text').get(default="").strip(),
            "ATM ID": get_value("ATM ID"),
            "Agency": get_value("Agency"),
            "Category": get_value("Category"),
            "Close Date & Time": close_date,
            "Publish Date": get_value("Publish Date"),
            "Location": get_value("Location", all_text=True),
            "ATM Type": get_value("ATM Type"),
            "Description": get_value("Description", all_text=True),
            "Conditions for Participation": get_value("Conditions for Participation", all_text=True),
            "Multi Agency Access": get_value("Multi Agency Access"),
            "Panel Arrangement": get_value("Panel Arrangement"),
            "Multi-stage": get_value("Multi-stage"),
            "Other Instructions": get_value("Other Instructions", all_text=True),
            "Timeframe for Delivery": get_value("Timeframe for Delivery", all_text=True),
            "Address for Lodgement": get_value("Address for Lodgement", all_text=True),
            "All Details": page_details(response),
            "Contact Name": contact_name_selector,
            "Contact Email": response.css("div.contact-long a.email::text").get(default="").strip() or contact_email_from_href,
            "Contact Phone": contact_phone_selector,
            "Addenda Link": addenda_link_absolute,
            "ATM Documents Link": atm_docs_link_absolute,
            "URL": response.url
        }

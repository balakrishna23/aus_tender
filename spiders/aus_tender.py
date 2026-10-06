from datetime import timedelta

import scrapy

from spiders.austender_common import (AusTenderListingMixin, austender_day, date_windows, page_details,
                                      search_url)


class AusTenderSpider(AusTenderListingMixin, scrapy.Spider):
    """Approaches to market (ATMs).

    Every run: all open ATMs (re-fetched, they get addenda and new close dates),
    all recently closed ATMs and ATMs archived in the last ARCHIVE_LOOKBACK_DAYS.
    A closed ATM is fetched once; after that its stored /ShowClosed/ page is final.
    ``-a since=2015-01-01`` backfills the archive in monthly windows.
    """

    name = "aus_tender"
    id_label = "ATM ID"
    custom_settings = {
        'LOG_FILE': 'logs/aus_tender.log',
        'LOG_LEVEL': 'INFO',
        'AUTOTHROTTLE_ENABLED': True,
    }
    # Only a safety net for downtime: anything newer was already seen as open or closed.
    ARCHIVE_LOOKBACK_DAYS = 14
    # ~85 ATMs are published per month; monthly windows stay far below the 10,000 cap.
    WINDOW_DAYS = 31

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
        self.load_stored()
        if getattr(self, 'start_urls', None):
            for url in self.start_urls:
                yield self.listing_request(url, 'default')
            return
        base = dict(Type='Atm', AgencyStatus='0', KeywordTypeSearch='AllWord')
        for kind in ('published', 'closed'):
            yield self.listing_request(search_url('AtmAdvancedSearch', AtmType=kind, **base), kind)
        since = self.since or (self.today - timedelta(days=self.ARCHIVE_LOOKBACK_DAYS))
        for start, end in date_windows(since, self.today, self.WINDOW_DAYS):
            yield self.listing_request(search_url(
                'AtmAdvancedSearch', AtmType='archived', PublishFrom=austender_day(start),
                PublishTo=austender_day(end), **base), 'archived')

    async def start(self):
        for request in self.start_requests():
            yield request

    def should_fetch(self, guid, stored_url, listing_kind):
        # Open notices change; a stored closed notice is final.
        return listing_kind in ('published', 'default') or '/ShowClosed/' not in stored_url

    def parse_tender(self, response):
        """
        Parse individual ATM tender page
        Updated with tested selectors for ATM Documents and Addenda links
        """
        def get_value(label, all_text=False):
            selector = f'//div[@class="list-desc"][span/label[normalize-space(text())="{label}"]]/div[@class="list-desc-inner"]'
            if all_text:
                parts = response.xpath(selector + '//text()').getall()
                cleaned_parts = [part.strip() for part in parts if part.strip() and part.strip() not in ['(ACT Local Time)', 'Show close time for other time zones']]
                return " ".join(cleaned_parts)
            else:
                return response.xpath(selector + '/text()[1]').get(default="").strip()

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

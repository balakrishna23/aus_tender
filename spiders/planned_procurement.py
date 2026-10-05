import scrapy
import re

class PlannedProcurementSpider(scrapy.Spider):
    name = "planned_procurement"
    custom_settings = {
        'LOG_FILE': 'logs/planned_procurement.log',
    }
    
    
    start_urls = [
        "https://www.tenders.gov.au/Search/PpAdvancedSearch?Type=Pp&AgencyStatus=0&KeywordTypeSearch=AllWord&page=1"
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Many rows say "As for APP": the contact is the one on the agency's Annual Procurement
        # Plan page. Each plan page is fetched once per run; its rows wait for it.
        self.plan_contacts = {}
        self.waiting_for_plan = {}

    def parse(self, response):
        articles = response.xpath('//article')
        if not articles:
            self.logger.error('No procurement rows extracted from %s; verify listing and selectors', response.url)
            self.crawler.stats.inc_value('crawl/extraction_failed')
        for procurement in articles:
            def get_detail(label):
                return procurement.xpath(f'.//div[@class="list-desc"][span/text()="{label}:"]/div[@class="list-desc-inner"]//text()').get(default="").strip()

            title = procurement.css('p.lead a::text').get(default='').strip()
            if not title:
                title = procurement.css('p.lead ::text').get(default='').strip()

            agency = get_detail("Agency")
            if not agency:
                agency = response.css('h1::text').get(default='').replace('Annual Procurement Plan View -', '').strip()

            contact_raw = procurement.xpath('.//div[@class="list-desc"][span/text()="Contact:"]/div[@class="list-desc-inner"]//text()').getall()
            contact_text = ' '.join(contact_raw).strip()
            contact_parts = [part.strip() for part in contact_text.split(',')]
            contact_name = contact_parts[0] if contact_parts else ''
            contact_phone = ''
            contact_email = ''
            if len(contact_parts) > 1:
                for part in contact_parts[1:]:
                    if '@' in part:
                        contact_email = part.split(' ')[0]
                    elif re.match(r'^[\d\s]+$', part):
                        contact_phone = part

            last_updated_raw = procurement.xpath('.//div[@class="last-updated"]//text()').getall()
            last_updated_text = ' '.join(part.strip() for part in last_updated_raw if part.strip())
            last_updated_match = re.search(r'Last Updated: (\d{2}-[A-Za-z]{3}-\d{4} \d{1,2}:\d{2} [ap]m)', last_updated_text)
            last_updated = last_updated_match.group(1) if last_updated_match else ''

            atm_link_relative = procurement.xpath(".//div[@class=\"list-desc\"][span/text()=\"ATM's:\"]/div[@class=\"list-desc-inner\"]/a/@href").get(default='').strip()
            atm_link_absolute = response.urljoin(atm_link_relative) if atm_link_relative else ""

            status_raw = get_detail("Status")
            status_clean = ' '.join(status_raw.split())

            detail_url_relative = procurement.css('p.lead a::attr(href)').get(default="").strip()
            if not detail_url_relative:
                self.logger.error('Skipping procurement without a unique detail URL: %s', title)
                self.crawler.stats.inc_value('crawl/extraction_failed')
                continue
            detail_url_absolute = response.urljoin(detail_url_relative)

            item = {
                "Title": title,
                "Agency": agency,
                "Agency Reference": get_detail("Agency Reference"),
                "Category": get_detail("Category"),
                "Estimated Date of Approach to Market": get_detail("Estimated Date of Approach to Market"),
                "Multi Agency Access": get_detail("Multi Agency Access"),
                "Multi Agency Access Type": get_detail("Multi Agency Access Type"),
                "Status": status_clean,
                "Change Comments": get_detail("Change Comments"),
                "ATM's": atm_link_absolute,
                "Contact Name": contact_name,
                "Contact Phone": contact_phone,
                "Contact Email": contact_email,
                "Last Updated": last_updated,
                "URL": detail_url_absolute,
            }
            if not contact_email and contact_name.lower().startswith('as for app'):
                yield from self._with_plan_contact(item, detail_url_absolute.split('?')[0].split('#')[0])
            else:
                yield item

        # Follow pagination if available
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            self.logger.info(f"Following next page: {response.urljoin(next_page)}")
            yield response.follow(next_page, callback=self.parse)

    def _with_plan_contact(self, item, plan_url):
        if plan_url in self.plan_contacts:
            yield self._fill_contact(item, self.plan_contacts[plan_url])
        elif plan_url in self.waiting_for_plan:
            self.waiting_for_plan[plan_url].append(item)
        else:
            self.waiting_for_plan[plan_url] = [item]
            yield scrapy.Request(plan_url, callback=self.parse_plan, errback=self.plan_failed,
                                 cb_kwargs={'plan_url': plan_url})

    @staticmethod
    def _fill_contact(item, contact):
        if contact.get('email'):
            item = dict(item, **{'Contact Name': contact['name'] or item['Contact Name'],
                                 'Contact Phone': contact['phone'], 'Contact Email': contact['email']})
        return item

    def parse_plan(self, response, plan_url):
        """The plan's 'Contact Details' box: team name, phone and email."""
        box = response.css('div.contact-long')[:1]
        name = box.xpath('./p[not(contains(@class, "contact-heading"))][1]/text()').get(default='').strip()
        phone = ' '.join(box.xpath('./p[span[contains(text(), "Phone")]]/text()').getall()).strip()
        email = (box.css('a.email::text').get() or box.xpath('.//a[starts-with(@href, "mailto:")]/text()').get()
                 or '').strip()
        if not email:
            self.logger.warning('No contact email on procurement plan %s', plan_url)
            self.crawler.stats.inc_value('crawl/plan_contact_missing')
        contact = {'name': name, 'phone': phone, 'email': email}
        self.plan_contacts[plan_url] = contact
        for item in self.waiting_for_plan.pop(plan_url, []):
            yield self._fill_contact(item, contact)

    def plan_failed(self, failure):
        """Keep the rows (contact stays 'As for APP'); the next run tries the plan page again."""
        plan_url = failure.request.cb_kwargs['plan_url']
        self.logger.warning('Could not load procurement plan %s: %s', plan_url, failure.value)
        self.crawler.stats.inc_value('crawl/plan_contact_missing')
        yield from self.waiting_for_plan.pop(plan_url, [])

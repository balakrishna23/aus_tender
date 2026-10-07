import re

import scrapy

AGENCY_LIST = 'https://www.tenders.gov.au/App/List?Tab=List%20By%20Agency'


class PlannedProcurementSpider(scrapy.Spider):
    """Planned procurements, from each agency's Annual Procurement Plan page.

    The "List By Agency" tab (/App/List) links every agency's plan (/App/Show/<plan>), which lists
    all of its planned procurements, 15 to a page. Both are allowed by robots.txt; the /Search
    pages it disallows are not used. Each item is saved under the same link as before
    (/App/Show/<plan>?ppId=<item>), and a contact given as "As for APP" takes the plan's contact.
    """

    name = "planned_procurement"
    custom_settings = {
        'LOG_FILE': 'logs/planned_procurement.log',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plans = 0

    def start_requests(self):
        yield scrapy.Request(AGENCY_LIST, callback=self.parse_agencies)

    async def start(self):
        for request in self.start_requests():
            yield request

    def parse_agencies(self, response):
        links = response.xpath('//a[contains(@href, "/App/Show/")]/@href').getall()
        if not links:
            self._failed('No agency plans listed on %s; verify the page and selectors', response.url)
        for link in dict.fromkeys(links):
            self.plans += 1
            yield response.follow(link.split('?')[0], callback=self.parse_plan)
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page, callback=self.parse_agencies)
        else:
            self.logger.info('Annual procurement plans listed: %s', self.plans)

    def parse_plan(self, response):
        plan_url = response.url.split('?')[0].split('#')[0]
        agency = ' '.join(response.css('h1 ::text').getall()).replace('Annual Procurement Plan View -', '').strip()
        plan_contact = self._plan_contact(response)
        for item in response.xpath('//div[starts-with(@id, "pp-")]'):
            pp_id = item.attrib['id'][3:]
            row = item.xpath('./ancestor::div[contains(@class, "row")][1]')
            title = ' '.join(t.strip() for t in row.xpath(
                './/p[contains(@class, "lead")]/text()').getall() if t.strip())

            def get_detail(label):
                inner = item.xpath(f'./div[@class="list-desc"][normalize-space(span)="{label}:"]'
                                   f'/div[@class="list-desc-inner"]')
                # "Original: ..." notes (p.changes) record past values; keep the current one.
                return ' '.join(' '.join(inner.xpath('./text() | ./*[not(contains(@class, "changes"))]//text()')
                                         .getall()).split())

            contact_name, contact_phone, contact_email = self._split_contact(get_detail('Contact'))
            if not contact_email and contact_name.lower().startswith('as for app'):
                contact_name = plan_contact['name'] or contact_name
                contact_phone, contact_email = plan_contact['phone'], plan_contact['email']
            last_updated = re.search(r'(\d{1,2}-[A-Za-z]{3}-\d{4} \d{1,2}:\d{2} [ap]m)',
                                     ' '.join(item.css('div.last-updated ::text').getall()))
            atm_link = item.xpath('./div[@class="list-desc"][normalize-space(span)="ATM\'s:"]//a/@href').get()
            if not title:
                self._failed('Planned procurement without a title on %s (%s)', response.url, pp_id)
                continue
            yield {
                "Title": title,
                "Agency": agency,
                "Agency Reference": get_detail("Agency Reference"),
                "Category": get_detail("Category"),
                "Estimated Date of Approach to Market": get_detail("Estimated Date of Approach to Market"),
                "Multi Agency Access": get_detail("Multi Agency Access"),
                "Multi Agency Access Type": get_detail("Multi Agency Access Type"),
                "Status": get_detail("Status"),
                "Change Comments": get_detail("Change Comments"),
                "ATM's": response.urljoin(atm_link) if atm_link else "",
                "Contact Name": contact_name,
                "Contact Phone": contact_phone,
                "Contact Email": contact_email,
                "Last Updated": last_updated.group(1) if last_updated else "",
                "URL": f'{plan_url}?ppId={pp_id}#pp-{pp_id}',
            }
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            yield response.follow(next_page, callback=self.parse_plan)

    @staticmethod
    def _split_contact(text):
        """'Name, 02 6123 4567, name@agency.gov.au' -> (name, phone, email)."""
        parts = [part.strip() for part in (text or '').split(',')]
        name, phone, email = (parts[0] if parts else ''), '', ''
        for part in parts[1:]:
            if '@' in part:
                email = part.split(' ')[0]
            elif re.match(r'^[\d\s()+]+$', part):
                phone = part
        return name, phone, email

    def _plan_contact(self, response):
        """The plan's 'Contact Details' box: team name, phone and email."""
        box = response.css('div.contact-long')[:1]
        name = box.xpath('./p[not(contains(@class, "contact-heading"))][1]/text()').get(default='').strip()
        phone = ' '.join(box.xpath('./p[span[contains(text(), "Phone")]]/text()').getall()).strip()
        email = (box.css('a.email::text').get() or box.xpath('.//a[starts-with(@href, "mailto:")]/text()').get()
                 or '').strip()
        return {'name': name, 'phone': phone, 'email': email}

    def _failed(self, message, *args):
        self.logger.error(message, *args)
        self.crawler.stats.inc_value('crawl/extraction_failed')

import scrapy
import re

class PlannedProcurementSpider(scrapy.Spider):
    name = "planned_procurement"
    
    start_urls = [
        "https://www.tenders.gov.au/Search/PpAdvancedSearch?Type=Pp&AgencyStatus=0&KeywordTypeSearch=AllWord&page=1"
    ]

    def parse(self, response):
        for procurement in response.xpath('//article'):
            def get_detail(label):
                return procurement.xpath(f'.//div[@class="list-desc"][span/text()="{label}:"]/div[@class="list-desc-inner"]//text()').get(default="").strip()

            contact_raw = procurement.xpath('.//div[@class="list-desc"][span/text()="Contact:"]/div[@class="list-desc-inner"]//text()').getall()
            contact_text = ' '.join(contact_raw).strip()
            contact_parts = [part.strip() for part in contact_text.split(',')]
            contact_name = contact_parts[0] if contact_parts else ''
            contact_phone = ''
            contact_email = ''
            if len(contact_parts) > 1:
                for part in contact_parts[1:]:
                    if '@' in part:
                        contact_email = part
                    elif re.match(r'^[\d\s]+$', part):
                        contact_phone = part

            last_updated_raw = procurement.xpath('.//div[@class="last-updated"]//text()').getall()
            last_updated_text = ' '.join(part.strip() for part in last_updated_raw if part.strip())
            last_updated_match = re.search(r'Last Updated: (\d{2}-[A-Za-z]{3}-\d{4} \d{1,2}:\d{2} [ap]m)', last_updated_text)
            last_updated = last_updated_match.group(1) if last_updated_match else ''

            atm_link_relative = procurement.xpath(""".//div[@class="list-desc"][span/text()="ATM's:"]/div[@class="list-desc-inner"]/a/@href""").get(default="").strip()
            atm_link_absolute = response.urljoin(atm_link_relative) if atm_link_relative else ""

            status_raw = get_detail("Status")
            status_clean = ' '.join(status_raw.split())

            yield {
                "Title": procurement.css('p.lead a::text').get(default="").strip(),
                "Agency": get_detail("Agency"),
                "Agency Reference": get_detail("Agency Reference"),
                "Category": get_detail("Category"),
                "Estimated Date of Approach to Market": get_detail("Estimated Date of Approach to Market"),
                "Multi Agency Access": get_detail("Multi Agency Access"),
                "Status": status_clean,
                "Change Comments": get_detail("Change Comments"),
                "ATM's": atm_link_absolute,
                "Contact Name": contact_name,
                "Contact Phone": contact_phone,
                "Contact Email": contact_email,
                "Last Updated": last_updated,
                "URL": response.url,
            }

        # Follow pagination if available
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            self.logger.info(f"Following next page: {response.urljoin(next_page)}")
            yield response.follow(next_page, callback=self.parse)

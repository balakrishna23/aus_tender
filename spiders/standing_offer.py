import scrapy

class StandingOfferSpider(scrapy.Spider):
    name = "standing_offer"
    custom_settings = {
        'LOG_FILE': 'logs/standing_offer.log',
    }
    
    
    start_urls = [
        "https://www.tenders.gov.au/Search/SonAdvancedSearch?SearchFrom=SonSearch&Type=Son&AgencyStatus=-1&KeywordTypeSearch=AllWord&DateType=Publish%20Date"
    ]

    def parse(self, response):
        # Extract all SON links from the listing page
        son_links = response.xpath('//div[@class="list-desc" and ./span/text()="SON ID:"]/div[@class="list-desc-inner"]/a/@href').getall()
        
        for link in son_links:
            yield response.follow(link, callback=self.parse_son)

        # Follow pagination if available
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            self.logger.info(f"Following next page: {response.urljoin(next_page)}")
            yield response.follow(next_page, callback=self.parse)

    def parse_son(self, response):
        def get_detail(label):
            return response.xpath(f'//div[@class="list-desc"][span/text()="{label}:"]/div[@class="list-desc-inner"]//text()').get(default="").strip()

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
            "Participant Agencies": get_table_data("ParticipantAgencies"),
            "URL": response.url,
        }

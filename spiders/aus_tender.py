import scrapy


class AusTenderSpider(scrapy.Spider):
    name = "aus_tender"
    
    start_urls = [
        "https://www.tenders.gov.au/Search/AtmAdvancedSearch?Type=Atm&AtmType=published%2Cclosed%2Carchived&AgencyStatus=0&KeywordTypeSearch=AllWord"
    ]

    def parse(self, response):
        # Extract all tender links from the listing page
        # Look for links that go to /Atm/Show/ or /Advert/Show/ pages
        tender_links = response.xpath('//div[@class="list-desc" and ./span/text()="ATM ID:"]/div[@class="list-desc-inner"]/a/@href').getall()
        
        
    
        for link in tender_links:
            yield response.follow(link, callback=self.parse_tender)

        # Follow pagination if available
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            self.logger.info(f"Following next page: {response.urljoin(next_page)}")
            yield response.follow(next_page, callback=self.parse)

    def parse_tender(self, response):
        def get_value(label, all_text=False):
            selector = f'//div[@class="list-desc"][span/label[normalize-space(text())="{label}"]]/div[@class="list-desc-inner"]'
            if all_text:
                parts = response.xpath(selector + '//text()').getall()
                cleaned_parts = [part.strip() for part in parts if part.strip() and part.strip() not in ['(ACT Local Time)', 'Show close time for other time zones']]
                return " ".join(cleaned_parts)
            else:
                return response.xpath(selector + '/text()[1]').get(default="").strip()

        close_date_raw = get_value("Close Date & Time", all_text=True)
        close_date_parts = close_date_raw.split()
        if len(close_date_parts) >= 3:
            close_date = f"{close_date_parts[0]} {close_date_parts[1]} {close_date_parts[2]}"
        else:
            close_date = close_date_raw

        addenda_link_relative = response.xpath('//div[span/label/text()="Addenda Available"]/div/a/@href').get(default="").strip()
        addenda_link_absolute = response.urljoin(addenda_link_relative) if addenda_link_relative else ""

        atm_docs_link_relative = response.css('div.btn-actions a:contains("ATM Documents")::attr(href)').get(default="")
        atm_docs_link_absolute = response.urljoin(atm_docs_link_relative) if atm_docs_link_relative else ""

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
            "Contact Name": response.css("div.contact-long p:nth-child(2)::text").get(default="").strip(),
            "Contact Email": response.css("div.contact-long a.email::text").get(default="").strip(),
            "Contact Phone": response.xpath('//div[contains(@class, "contact-long")]//p[span/label/@for="PhoneNumber"]/br/following-sibling::text()').get(default="").strip(),
            "Addenda Link": addenda_link_absolute,
            "ATM Documents Link": atm_docs_link_absolute,
            "URL": response.url
        }

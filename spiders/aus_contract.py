import scrapy


class AusContractSpider(scrapy.Spider):
    name = "aus_contract"
    
    start_urls = [
        "https://www.tenders.gov.au/Search/CnAdvancedSearch?Type=Cn&AgencyStatus=0&KeywordTypeSearch=AllWord&DateType=Publish+Date&page=1"
    ]

    def parse(self, response):
        # Extract all contract links from the listing page
        contract_links = response.xpath('//div[@class="list-desc" and ./span/text()="CN ID:"]/div[@class="list-desc-inner"]/a/@href').getall()
    
        for link in contract_links:
            yield response.follow(link, callback=self.parse_contract)

        # Follow pagination if available
        next_page = response.css('li.next a::attr(href)').get()
        if next_page:
            self.logger.info(f"Following next page: {response.urljoin(next_page)}")
            yield response.follow(next_page, callback=self.parse)

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
            "Supplier Name": get_supplier_detail("Name"),
            "Supplier Town/City": get_supplier_detail("Town/City"),
            "Supplier Postcode": get_supplier_detail("Postcode"),
            "Supplier State/Territory": get_supplier_detail("State/Territory"),
            "Supplier Country": get_supplier_detail("Country"),
            "Supplier ABN": get_supplier_detail("ABN"),
            "URL": response.url,
        }

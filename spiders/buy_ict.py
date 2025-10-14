import scrapy
import urllib.parse


class BuyIctSpider(scrapy.Spider):
    name = 'buy_ict'
    custom_settings = {
        'LOG_FILE': 'logs/buy_ict.log',
        # Keep Playwright fast and stable
        'PLAYWRIGHT_LAUNCH_OPTIONS': {
            'headless': False,
        }
        
        
        # Right-size concurrency for browser automation
        
    }

    async def start(self):
        yield scrapy.Request(
            url='https://www.buyict.gov.au/sp?id=opportunities',
            meta=dict(
                playwright=True,
                playwright_include_page=True,
                # Avoid networkidle; use fast waits
                playwright_page_goto_kwargs={'wait_until': 'domcontentloaded', 'timeout': 30000},
                errback=self.errback,
                spider_name=self.name  # Add spider name to meta
            ),
        )

    async def parse(self, response):
        page = response.meta['playwright_page']
        base_url = 'https://www.buyict.gov.au/sp'
        opportunity_links = []
        next_page_click_count = 0

        # Optional: adjust filters
        try:
            await page.get_by_role('button', name='Status filter').click(timeout=5000)
            for text in ('Closed', 'Closing soon'):
                try:
                    await page.get_by_text(text, exact=True).click(timeout=1500)
                except Exception:
                    pass
        except Exception as e:
            self.logger.debug(f'Status filter interaction skipped: {e}')

        # Ensure listings loaded
        try:
            await page.wait_for_selector("//a[contains(@href,'opportunity_details')]", timeout=10000)
        except Exception:
            self.logger.warning('Listing items selector did not appear; continuing')

        while True:
            # Prefer anchors carrying the real href
            anchors = await page.query_selector_all("//a[contains(@href,'opportunity_details')]")
            if not anchors:
                self.logger.warning(f"No opportunity links found on page {page.url}")
            for el in anchors:
                try:
                    href = await el.get_attribute('href')
                except Exception:
                    href = None
                if not href:
                    continue
                absolute_link = urllib.parse.urljoin(base_url, href)
                opportunity_links.append(absolute_link)

                yield scrapy.Request(
                    url=absolute_link,
                    callback=self.parse_opportunity,
                    meta=dict(
                        playwright=True,
                        playwright_include_page=True,
                        playwright_page_goto_kwargs={'wait_until': 'domcontentloaded', 'timeout': 30000},
                        errback=self.errback,
                    ),
                )

            # Pagination: click next if enabled
            try:
                next_li = page.locator("//li[contains(@class, 'pagination-next')]")
                class_name = await next_li.get_attribute('class')
                if class_name and 'disabled' in class_name:
                    break
                next_link = next_li.locator('a')
                if await next_link.count() == 0:
                    break
                await next_link.click()
                next_page_click_count += 1
                self.logger.info(f"Next page clicked {next_page_click_count} times.")
                await page.wait_for_selector("//a[contains(@href,'opportunity_details')]", timeout=10000)
            except Exception:
                break

        self.logger.info(f"Total opportunity links found: {len(opportunity_links)}")
        self.logger.info(f"Total next page clicks: {next_page_click_count}")
        self.logger.debug('Closing listing page')
        await page.close()

    async def parse_opportunity(self, response):
        page = response.meta['playwright_page']
        details = {}

        async def get_text_if_present(locator, timeout=800):
            try:
                if await locator.count() == 0:
                    return None
                return await locator.first.inner_text(timeout=timeout)
            except Exception:
                return None

        async def get_value_by_labels(labels, timeout=800):
            for label in labels:
                try:
                    loc = page.locator(f"//div[./strong[normalize-space(text())='{label}']]//following-sibling::div[1]")
                    val = await get_text_if_present(loc, timeout=timeout)
                    if val:
                        return val
                except Exception:
                    continue
            return None

        # Try to wait briefly for the title; if not present, continue best-effort
        try:
            await page.wait_for_selector('h2.ng-binding', timeout=3000)
        except Exception:
            pass

        try:
            details['title'] = await get_text_if_present(page.locator('h2.ng-binding'))
            details['rfq_type'] = await get_value_by_labels(['RFQ type', 'RFI type'])
            details['rfq_id'] = await get_value_by_labels(['RFQ ID', 'RFI ID'])
            details['rfq_published_date'] = await get_value_by_labels(['RFQ published date', 'RFI published date'])
            details['deadline_for_asking_questions'] = await get_value_by_labels(['Deadline for asking questions'])
            details['rfq_closing_date'] = await get_value_by_labels(['RFQ closing date', 'RFI closing date'])
            details['buyer'] = await get_value_by_labels(['Buyer'])
            details['buyer_contact'] = await get_value_by_labels(['Buyer contact'])
            details['estimated_start_date'] = await get_value_by_labels(['Estimated start date'])
            details['initial_contract_duration'] = await get_value_by_labels(['Initial contract duration'])
            details['extension_term'] = await get_value_by_labels(['Extension term'])
            details['number_of_extensions'] = await get_value_by_labels(['Number of extensions'])
            details['location_of_work'] = await get_value_by_labels(['Location of work'])
            details['working_arrangements'] = await get_value_by_labels(['Working arrangements'])
            details['industry_briefing'] = await get_value_by_labels(['Industry briefing'])
            details['marketplace'] = await get_text_if_present(page.locator('p.offering-badge.ng-binding.ng-scope'))

            # Requirements
            requirements = {}
            rows = page.locator("//div[contains(@ng-repeat, 'requirementDetail')]")
            count = await rows.count()
            for i in range(count):
                row = rows.nth(i)
                key = await get_text_if_present(row.locator('strong'))
                val = await get_text_if_present(row.locator("div.ng-binding[ng-bind-html]"))
                if not val:
                    val = await get_text_if_present(row.locator("span[ng-if='requirementDetail.value']"))
                if not val:
                    val = await get_text_if_present(row.locator('div.col-md-8'))
                if not val:
                    # list items fallback
                    li = row.locator('li')
                    li_count = await li.count()
                    if li_count:
                        items = []
                        for j in range(li_count):
                            t = await get_text_if_present(li.nth(j))
                            if t:
                                items.append(t)
                        val = '; '.join(items) if items else None
                if key:
                    requirements[key] = val
            details['requirements'] = requirements

            # Criteria
            criteria = {}
            blocks = page.locator("//div[contains(@ng-repeat, 'criteriaType')]")
            bcount = await blocks.count()
            if bcount == 0:
                crow = page.locator("//div[contains(@class,'criteria-row')]")
                ccount = await crow.count()
                if ccount:
                    texts = []
                    for i in range(ccount):
                        t = await get_text_if_present(crow.nth(i))
                        if t:
                            texts.append(t)
                    if texts:
                        criteria['criteria'] = ' | '.join(texts)
            else:
                for i in range(bcount):
                    block = blocks.nth(i)
                    key = await get_text_if_present(block.locator('strong'))
                    val = await get_text_if_present(block.locator('div.criteria-row'))
                    if key:
                        criteria[key] = val
            details['criteria'] = criteria

            details['url'] = response.url
        except Exception as e:
            self.logger.error(f'Error parsing {response.url}: {e}')
        finally:
            self.logger.debug(f'Closing detail page: {response.url}')
            await page.close()
            yield details

    async def errback(self, failure):
        page = failure.request.meta.get('playwright_page')
        self.logger.error(f"Errorback triggered for {failure.request.url}: {failure.value}")
        if page:
            try:
                await page.close()
            except Exception:
                pass

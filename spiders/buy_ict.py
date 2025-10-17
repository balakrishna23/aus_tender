import scrapy
import re
from typing import List, Optional
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

class BuyIctSpider(scrapy.Spider):
    name = 'buy_ict'
    custom_settings = {
        'LOG_FILE': 'logs/buy_ict.log',
        'PLAYWRIGHT_LAUNCH_OPTIONS': {
            'headless': True,
        },
        'PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT': 60000
    }

    STATUS_LABELS = {
        "live": "#cb-status-live",
        "closing": "#cb-status-closingsoon",
        "closed": "#cb-status-closed",
    }
    RESULTS_REGION_ARIA = "Opportunities"

    async def start(self):
        yield scrapy.Request(
            url='https://www.buyict.gov.au/sp?id=opportunities',
            meta=dict(
                playwright=True,
                playwright_include_page=True,
                playwright_page_goto_kwargs={'wait_until': 'domcontentloaded'},
                errback=self.errback,
                spider_name=self.name,
            ),
            callback=self.parse_listings
        )

    async def parse_listings(self, response):
        page = response.meta['playwright_page']
        try:
            self.logger.info("Applying filters and setting items per page...")
            await self.set_status_filters(page, ["live", "closing", "closed"])
            await self.set_items_per_page(page, "90")

            self.logger.info("Starting pagination and link extraction...")
            page_idx = 0
            prev_sig: Optional[str] = None
            while True:
                page_idx += 1
                self.logger.info(f"Scraping page {page_idx}")
                
                links = await self.extract_current_page_links(page)
                self.logger.info(f"Found {len(links)} links on page {page_idx}")
                for link_url in links:
                    yield response.follow(
                        link_url,
                        callback=self.parse_opportunity,
                        meta=dict(
                            playwright=True,
                            playwright_include_page=True,
                            playwright_page_goto_kwargs={'wait_until': 'domcontentloaded'},
                            errback=self.errback,
                        )
                    )

                prev_sig = await self._get_page_signature(page)
                moved = await self.click_next(page, prev_signature=prev_sig)
                if not moved:
                    self.logger.info("No more pages to paginate.")
                    break

        except Exception as e:
            self.logger.error(f"An error occurred during parsing listings: {e}")
        finally:
            self.logger.info("Finished scraping listings, closing page.")
            await page.close()

    async def wait_for_results_settle(self, page: Page, timeout_ms: int = 15000):
        await page.get_by_role("region", name=self.RESULTS_REGION_ARIA).wait_for(timeout=timeout_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_timeout(500)

    async def open_status_filter(self, page: Page):
        status_btn = page.get_by_role("button", name=re.compile(r"^Status filter$|^Status$", re.I))
        for _ in range(3):
            await status_btn.click()
            try:
                await page.wait_for_selector("#cb-status-live", state="visible", timeout=2000)
                break
            except PlaywrightTimeoutError:
                continue

    async def set_status_filters(self, page: Page, statuses: List[str]):
        await self.open_status_filter(page)
        for key in statuses:
            sel = self.STATUS_LABELS.get(key)
            if not sel:
                continue
            el = page.locator(sel)
            try:
                await el.wait_for(state="visible", timeout=3000)
            except PlaywrightTimeoutError:
                await self.open_status_filter(page)
            if not await el.is_checked():
                await el.check(force=True)
        await self.wait_for_results_settle(page)
        await page.wait_for_timeout(5000)

    async def set_items_per_page(self, page: Page, value: str = "90"):
        try:
            select = page.get_by_label(re.compile(r"Items per page:?", re.I))
            await select.wait_for(state="visible", timeout=4000)
            await select.select_option(value)
        except Exception:
            # Fallback if label-based selection fails
            selects = page.locator("select")
            for i in range(await selects.count()):
                sel = selects.nth(i)
                if await sel.locator(f'option[value="{value}"]').count() > 0:
                    await sel.select_option(value)
                    break
        await self.wait_for_results_settle(page)
        await page.wait_for_timeout(1500)

    async def extract_current_page_links(self, page: Page) -> List[str]:
        links = []
        link_selector = page.locator('a[href*="id=opportunity"], a[href*="id=marketplace_opportunity"]')
        for i in range(await link_selector.count()):
            link = link_selector.nth(i)
            url = await link.get_attribute("href")
            if url:
                links.append(url)
        return links

    async def _get_page_signature(self, page: Page) -> str:
        try:
            cards = page.locator('a[href*="id=opportunity"], a[href*="id=marketplace_opportunity"]')
            count = await cards.count()
            if count == 0:
                return "count:0"
            first_text = await cards.first.locator("xpath=ancestor::*[self::article or self::li or self::div][1]").inner_text()
            last_text = await cards.last.locator("xpath=ancestor::*[self::article or self::li or self::div][1]").inner_text()
            return f"count:{count}|first:{hash(first_text)}|last:{hash(last_text)}"
        except Exception:
            return "unknown"

    async def click_next(self, page: Page, prev_signature: Optional[str] = None) -> bool:
        try:
            baseline = prev_signature or await self._get_page_signature(page)
            await page.wait_for_timeout(2000)

            next_button = page.get_by_role("link", name="›")
            if await next_button.is_visible() and await next_button.is_enabled():
                await next_button.click()
            else:
                return False

            await self.wait_for_results_settle(page)

            for _ in range(24):
                sig = await self._get_page_signature(page)
                if sig != baseline:
                    return True
                await page.wait_for_timeout(250)
            return False
        except Exception:
            return False

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
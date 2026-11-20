import json
import html
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, quote, urlparse
from urllib.parse import urljoin

import scrapy
from database_manager import DatabaseManager


HTML_BREAK = re.compile(r'<\s*(br|/p|/div|/li|/h\d|/tr)\s*/?>', re.I)
HTML_TAG = re.compile(r'<[^>]+>')


def html_to_text(value):
    """Buyers write requirements in a rich-text editor; keep the words and line breaks, not the markup."""
    if not isinstance(value, str) or '<' not in value:
        return value
    text = HTML_BREAK.sub('\n', value)
    text = re.sub(r'<\s*li[^>]*>', '\n• ', text, flags=re.I)
    text = html.unescape(HTML_TAG.sub('', text)).replace('\xa0', ' ')
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.replace('\r', '').split('\n')]
    return '\n'.join(line for line in lines if line)


class BuyIctApiSpider(scrapy.Spider):
    """
    HTTP-only BuyICT spider (no Playwright).
    Uses Service Portal APIs:
      - /api/now/sp/page
      - /api/now/sp/rectangle/{id}
    """

    name = "buy_ict"
    custom_settings = {
        "LOG_FILE": "logs/buy_ict.log",
        "LOG_LEVEL": "INFO",
        # The project-wide 1s DOWNLOAD_DELAY applies per domain, which capped this
        # spider at ~50 requests/min regardless of concurrency. The API copes with 4 at once.
        "CONCURRENT_REQUESTS": 8,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "DOWNLOAD_DELAY": 0.25,
        "AUTOTHROTTLE_ENABLED": False,
    }

    # Fallback IDs observed currently; spider attempts dynamic discovery first.
    FALLBACK_FILTER_RECTANGLE_ID = "f3c3ac0a8738d950f973a8e50cbb35c2"
    FALLBACK_RESULTS_RECTANGLE_ID = "bc93ec0a8738d950f973a8e50cbb3598"
    FALLBACK_DETAIL_RECTANGLE_ID = "698ffe151b4d411064f3a68b274bcbea"

    def __init__(self, max_pages=None, page_size=90, summary_only=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Crawl the entire result set by default. A page limit is useful for smoke runs.
        self.max_pages = max(1, int(max_pages)) if max_pages is not None else None
        self.page_size = max(1, int(page_size))
        self.summary_only = str(summary_only).lower() in ("1", "true", "yes")
        self.base_public_url = "https://www.buyict.gov.au/public"
        self.portal_id = None
        self.seen_detail_urls = set()
        self.pages_seen = 0
        self.reported_total = None
        self.db_manager = DatabaseManager()
        self.stored = {}
        self.stored_rfq_ids = {}
        self.validated_empty = False
        self.expected_pages = None
        self.detail_requests = 0
        self.closed_skipped = 0
        self.title_updates = 0

    def _record_failure(self, message, *args):
        self.logger.error(message, *args)
        self.crawler.stats.inc_value('crawl/extraction_failed')

    def _load_stored(self):
        """sys_id -> (status, captured_while_open) for every saved opportunity."""
        self.db_manager.initialize_database(self.name)
        with self.db_manager.get_connection(self.name) as conn:
            rows = conn.execute('SELECT sys_id, status, scraped_at, rfq_closing_date, rfq_id '
                                'FROM buy_ict_tenders WHERE sys_id IS NOT NULL').fetchall()
        for sys_id, status, scraped_at, closing, rfq_id in rows:
            self.stored_rfq_ids[sys_id] = rfq_id
            closing_day = self._parse_day(closing)
            captured_open = closing_day is None or str(scraped_at or '')[:10] <= closing_day.isoformat()
            self.stored[sys_id] = (status, captured_open)
        self.logger.info('Loaded %s stored BuyICT opportunities', len(self.stored))

    @staticmethod
    def _parse_day(text):
        match = re.search(r'(\d{1,2} [A-Za-z]+ \d{4})', text or '')
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), '%d %B %Y').date()
        except ValueError:
            return None

    def _needs_detail(self, sys_id, closed):
        """Open opportunities can change; a closed one is final once saved after closing."""
        if not closed or sys_id not in self.stored:
            return True
        status, captured_open = self.stored[sys_id]
        if status == 'closed':
            return False
        # Last saved while open (or legacy row saved before its close date): fetch the final copy once.
        return status == 'open' or captured_open

    def closed(self, reason):
        # BuyICT's totalItems also counts opportunities restricted to logged-in sellers, which
        # the public API never returns (~1/3 of the total). Coverage is therefore measured as
        # "every listing page loaded", not "unique items == totalItems".
        if self.expected_pages is not None and self.pages_seen < self.expected_pages:
            self._record_failure('BuyICT listing incomplete: loaded %s of %s pages',
                                 self.pages_seen, self.expected_pages)
        if not self.seen_detail_urls and reason == 'finished' and not self.validated_empty:
            self._record_failure('BuyICT listing returned no opportunities')
        if self.validated_empty and not self.seen_detail_urls and reason == 'finished':
            self.crawler.stats.set_value('crawl/nothing_due', 1)
        self.logger.info(
            "BuyICT listing coverage: pages=%s/%s public_opportunities=%s reported_total=%s "
            "(remainder visible to logged-in sellers only) reason=%s",
            self.pages_seen, self.expected_pages, len(self.seen_detail_urls), self.reported_total, reason,
        )
        self.logger.info(
            "BuyICT detail plan: requested=%s closed_already_saved=%s listing_rows_updated=%s",
            self.detail_requests, self.closed_skipped, self.title_updates,
        )

    def start_requests(self):
        url = f"{self.base_public_url}?id=opportunities"
        yield scrapy.Request(
            url=url,
            callback=self.parse_opportunities_landing,
            meta={"cookiejar": 1, "spider_name": self.name},
        )

    async def start(self):
        # Scrapy 2.12 calls start_requests(); 2.13+ calls start().
        for request in self.start_requests():
            yield request

    def parse_opportunities_landing(self, response):
        token = self._extract_g_ck(response.text)
        self.portal_id = self._extract_portal_id(response.text)
        if not token or not self.portal_id:
            self.logger.error("Failed to extract g_ck token or portal_id from opportunities page.")
            return

        page_api = self._build_sp_page_url(
            page_id="opportunities",
            request_uri="/public?id=opportunities",
            extra_params={},
        )
        yield scrapy.Request(
            url=page_api,
            headers=self._sp_headers(token),
            callback=self.parse_opportunities_sp_page,
            meta={
                "cookiejar": 1,
                "token": token,
                "spider_name": self.name,
            },
        )

    def parse_opportunities_sp_page(self, response):
        sp_page = self._safe_json(response.text).get("result", {})
        results_rectangle_id, filter_rectangle_id = self._discover_opportunities_rectangle_ids(sp_page)
        self.logger.info(
            "Discovered opportunities rectangles: results=%s filter=%s",
            results_rectangle_id,
            filter_rectangle_id,
        )

        token = response.meta["token"]
        filter_url = self._build_rectangle_url(filter_rectangle_id, "opportunities")
        yield scrapy.Request(
            url=filter_url,
            method="POST",
            body="{}",
            headers=self._sp_headers(token, json_post=True),
            callback=self.parse_filter_rectangle,
            meta={
                "cookiejar": 1,
                "token": token,
                "results_rectangle_id": results_rectangle_id,
                "filter_rectangle_id": filter_rectangle_id,
                "spider_name": self.name,
            },
        )

    def parse_filter_rectangle(self, response):
        token = response.meta["token"]
        results_rectangle_id = response.meta.get("results_rectangle_id", self.FALLBACK_RESULTS_RECTANGLE_ID)
        payload = self._safe_json(response.text).get("result", {}).get("data", {})
        if not payload:
            self.logger.error("Filter rectangle returned no data payload.")
            return

        self._load_stored()

        # Match current Playwright spider behavior: include all status groups.
        try:
            for choice in payload["filters"]["opp_status"]["choices"]:
                choice["selected"] = True
        except Exception:
            pass

        yield self._results_page_request(payload, results_rectangle_id, token, 1)

    def _results_page_request(self, payload, rectangle_id, token, page_num):
        start_loc = (page_num - 1) * self.page_size
        page_payload = dict(payload)
        page_payload.update(
            {
                "newFocus": None,
                "recTags": [],
                "page": "opportunities",
                "currentPage": page_num,
                "page_size": str(self.page_size),
                "startLoc": start_loc,
                "endLoc": start_loc + self.page_size - 1,
                "limit": 0,
                "getPageData": True,
                "sessionRotationTrigger": True,
            }
        )
        return scrapy.Request(
            url=self._build_rectangle_url(rectangle_id, "opportunities"),
            method="POST",
            body=json.dumps(page_payload),
            headers=self._sp_headers(token, json_post=True),
            callback=self.parse_results_rectangle,
            priority=10,
            meta={
                "cookiejar": 1,
                "token": token,
                "page_num": page_num,
                "results_payload": payload,
                "results_rectangle_id": rectangle_id,
                "spider_name": self.name,
            },
        )

    def parse_results_rectangle(self, response):
        token = response.meta["token"]
        page_num = response.meta["page_num"]
        data = self._safe_json(response.text).get("result", {}).get("data", {})
        if not isinstance(data, dict) or 'pageItems' not in data:
            self._record_failure('Missing BuyICT listing payload on page %s', page_num)
            return
        items = data['pageItems']
        if not isinstance(items, list) or any(not isinstance(row, dict) for row in items):
            self._record_failure('Invalid BuyICT listing rows on page %s', page_num)
            return
        self.pages_seen += 1
        self.reported_total = data.get("totalItems", self.reported_total)
        self.logger.info(
            "Results rectangle page=%s returned %s items (totalItems=%s)",
            page_num,
            len(items),
            data.get("totalItems"),
        )

        listing_updates = []
        for summary in items:
            rel_url = summary.get("url")
            if not rel_url:
                self._record_failure("BuyICT listing item has no detail URL on page %s", page_num)
                continue
            if rel_url.startswith("?"):
                detail_url = f"{self.base_public_url}{rel_url}"
            else:
                detail_url = urljoin(self.base_public_url, rel_url)
            detail_url = DatabaseManager.buy_ict_url(detail_url) or detail_url
            if detail_url in self.seen_detail_urls:
                continue
            self.seen_detail_urls.add(detail_url)
            if self.summary_only:
                continue
            sys_id = summary.get("sys_id") or (re.search(r"sys_id=([0-9a-f]{32})", detail_url) or [None, None])[1]
            closed = summary.get("closed") is True
            summary["_status"] = "closed" if closed else "open"
            if not self._needs_detail(sys_id, closed):
                listing_updates.append((summary.get("short_description"), "closed", sys_id))
                self.crawler.stats.inc_value("crawl/items_unchanged")
                self.closed_skipped += 1
                continue
            self.detail_requests += 1
            yield self._detail_rectangle_request(
                detail_url, summary, token, self.FALLBACK_DETAIL_RECTANGLE_ID
            )

        self.title_updates += self.db_manager.update_buy_ict_listing(listing_updates)

        raw_total = data.get("totalItems")
        # Do not truncate floats, accept booleans as integers, or trust negative totals.
        total_items = (int(str(raw_total).strip().replace(',', '')) if not isinstance(raw_total, bool) and
                       re.fullmatch(r'\d+|\d{1,3}(?:,\d{3})+', str(raw_total).strip()) else None)
        if page_num == 1 and total_items is not None:
            if total_items == 0 and items:
                self._record_failure('BuyICT zero total contradicts nonempty listing rows')
                return
            self.validated_empty = total_items == 0 and not items
            last_page = (total_items + self.page_size - 1) // self.page_size
            if self.max_pages is not None:
                last_page = min(last_page, self.max_pages)
            self.expected_pages = last_page
            for next_page in range(2, last_page + 1):
                yield self._results_page_request(
                    response.meta["results_payload"], response.meta["results_rectangle_id"],
                    token, next_page)
            return
        if total_items is not None:
            return

        self.crawler.stats.inc_value('crawl/buyict_unknown_totals')
        # Explicit API pagination only: public rows can be sparse because some sellers
        # cannot see restricted opportunities. A short page does not prove completion.
        next_page = data.get('nextPage')
        has_next = data.get('hasNextPage')
        if next_page is None and has_next is True:
            next_page = page_num + 1
        valid_next = (not isinstance(next_page, bool) and
                      re.fullmatch(r'\d+', str(next_page).strip()) and
                      int(next_page) == page_num + 1)
        explicit_end = has_next is False or next_page is False or next_page == 0
        if valid_next:
            if self.max_pages is not None and page_num >= self.max_pages:
                self.logger.error('BuyICT page cap reached with unknown total and more pages')
                self.crawler.stats.inc_value('crawl/buyict_pagination_failed')
                return
            yield self._results_page_request(
                response.meta["results_payload"], response.meta["results_rectangle_id"],
                token, int(next_page))
        elif not explicit_end:
            self.logger.error('BuyICT totalItems=%r unparseable and no validated pagination on page %s',
                              raw_total, page_num)
            self.crawler.stats.inc_value('crawl/buyict_pagination_failed')

    def _detail_rectangle_request(self, detail_url, summary, token, rectangle_id,
                                  fallback_attempted=False):
        qs = parse_qs(urlparse(detail_url).query)
        page_id = (qs.get("id") or [""])[0]
        rectangle_url = self._build_rectangle_url(
            rectangle_id=rectangle_id,
            page_id=page_id,
            extra_params={
                "table": (qs.get("table") or [""])[0],
                "sys_id": (qs.get("sys_id") or [""])[0],
                "entry": (qs.get("entry") or [""])[0],
            },
        )
        return scrapy.Request(
            url=rectangle_url,
            method="POST",
            body="{}",
            headers=self._sp_headers(token, json_post=True),
            callback=self.parse_detail_rectangle,
            dont_filter=fallback_attempted,
            meta={
                "cookiejar": 1,
                "summary": summary,
                "detail_url": detail_url,
                "token": token,
                "fallback_attempted": fallback_attempted,
                "handle_httpstatus_all": True,
                "spider_name": self.name,
            },
        )

    def parse_detail_landing(self, response):
        token = self._extract_g_ck(response.text) or response.meta.get("token")
        if not token:
            self._record_failure("Missing token for detail page: %s", response.url)
            return

        qs = parse_qs(urlparse(response.url).query)
        page_id = (qs.get("id") or [""])[0]
        if not page_id:
            self._record_failure("Detail page missing id param: %s", response.url)
            return

        detail_rectangle_id = self._discover_detail_rectangle_id_from_page_html(response.text)
        if not detail_rectangle_id:
            detail_rectangle_id = self.FALLBACK_DETAIL_RECTANGLE_ID

        yield self._detail_rectangle_request(
            response.meta.get("detail_url", response.url),
            response.meta.get("summary", {}),
            token,
            detail_rectangle_id,
            fallback_attempted=True,
        )

    def parse_detail_rectangle(self, response):
        summary = response.meta.get("summary", {})
        detail_url = response.meta.get("detail_url", response.url)
        result_data = self._safe_json(response.text).get("result", {}).get("data", {})
        opp_widget_data = (result_data.get("opp_widget") or {}).get("data") or result_data
        main_details = opp_widget_data.get('mainDetails') if isinstance(opp_widget_data, dict) else None
        if not isinstance(main_details, list) or not any(
                isinstance(row, dict) and row.get('label') for row in main_details):
            if not response.meta.get("fallback_attempted"):
                self.logger.info("Direct detail API unavailable; loading detail page for %s", detail_url)
                yield scrapy.Request(
                    url=detail_url,
                    callback=self.parse_detail_landing,
                    meta={
                        "cookiejar": 1,
                        "summary": summary,
                        "detail_url": detail_url,
                        "token": response.meta.get("token"),
                        "refresh_existing": True,
                        "spider_name": self.name,
                    },
                )
            else:
                self._record_failure("No opp_widget data for %s", detail_url)
            return

        main_details_map = self._label_value_map(opp_widget_data.get("mainDetails"))
        requirements_map = self._label_value_map(opp_widget_data.get("requirements"))

        ess_criteria = [
            c.get("details")
            for c in (opp_widget_data.get("essCriteria") or [])
            if isinstance(c, dict) and c.get("details")
        ]
        des_criteria = [
            c.get("details")
            for c in (opp_widget_data.get("desCriteria") or [])
            if isinstance(c, dict) and c.get("details")
        ]

        criteria = {
            "essential": ess_criteria,
            "desirable": des_criteria,
            "all_criteria_labels": [
                c.get("label")
                for c in (opp_widget_data.get("allCriteria") or [])
                if isinstance(c, dict) and c.get("label")
            ],
        }

        yield {
            "title": opp_widget_data.get("title") or summary.get("short_description"),
            "rfq_type": main_details_map.get("RFQ type") or main_details_map.get("RFI type"),
            "rfq_id": main_details_map.get("RFQ ID") or main_details_map.get("RFI ID")
            or self.stored_rfq_ids.get(summary.get('sys_id') or
                (parse_qs(urlparse(detail_url).query).get('sys_id') or [None])[0]),
            "rfq_published_date": main_details_map.get("RFQ published date")
            or main_details_map.get("RFI published date"),
            "deadline_for_asking_questions": main_details_map.get("Deadline for asking questions"),
            "rfq_closing_date": main_details_map.get("RFQ closing date")
            or main_details_map.get("RFI closing date"),
            "buyer": main_details_map.get("Buyer") or summary.get("u_buyer_name"),
            "buyer_contact": main_details_map.get("Buyer contact"),
            "estimated_start_date": requirements_map.get("Estimated start date"),
            "initial_contract_duration": requirements_map.get("Initial contract duration"),
            "extension_term": requirements_map.get("Extension term"),
            "number_of_extensions": requirements_map.get("Number of extensions"),
            "location_of_work": requirements_map.get("Location of work") or summary.get("u_delivery_state"),
            "working_arrangements": requirements_map.get("Working arrangements")
            or summary.get("u_delivery_method"),
            "industry_briefing": requirements_map.get("Industry briefing"),
            "requirements": requirements_map,
            "criteria": criteria,
            "url": detail_url,
            "sys_id": summary.get("sys_id")
            or (re.search(r"sys_id=([0-9a-f]{32})", detail_url) or [None, None])[1],
            # Explicit detail status overrides the listing summary (which can lag).
            "status": self._public_status(opp_widget_data, summary),
            # Everything else BuyICT shows, so nothing on the page is lost.
            "description": html_to_text(opp_widget_data.get("description")),
            "key_duties": html_to_text(opp_widget_data.get("key_duties")),
            "response_requirements": opp_widget_data.get("response_requirements"),
            "main_details": main_details_map,
            "all_details": dict(self._other_details(opp_widget_data),
                                public_requirements=opp_widget_data.get('requirements') or []),
        }

    @staticmethod
    def _public_status(data, summary):
        if data.get('is_cancelled') is True:
            return 'cancelled'
        if data.get('rfqOpen') is False:
            return 'closed'
        if data.get('rfqOpen') is True:
            return 'open'
        return summary.get('_status')

    # Page-state flags, labels and the visitor's session: not part of the opportunity.
    UI_KEYS = {"seller", "user", "page", "accordian", "access_message", "recTypeForDetailFilter",
               "contractHeading", "criteriaLabel", "requirementsLabel", "documentsLabel", "badgeLabel",
               "mainDetails", "requirements"}

    def _other_details(self, data):
        """The opportunity API's remaining sections, with HTML turned into text."""
        def text(value):
            if isinstance(value, str):
                return html_to_text(value)
            if isinstance(value, list):
                return [text(v) for v in value]
            if isinstance(value, dict):
                return {k: text(v) for k, v in value.items()}
            return value
        return {k: text(v) for k, v in data.items()
                if k not in self.UI_KEYS and not isinstance(v, bool) and v not in (None, "", [], {})}

    def _extract_g_ck(self, html_text):
        match = re.search(r'''window\.g_ck\s*=\s*(['"])(.*?)\1''', html_text or "")
        return match.group(2) if match else None

    def _extract_portal_id(self, html_text):
        match = re.search(r'''\bportal_id\s*=\s*(['"])(.*?)\1''', html_text or "")
        return match.group(2) if match else None

    def _sp_headers(self, token, json_post=False):
        headers = {
            "accept": "application/json",
            "x-usertoken": token,
            "x-portal": self.portal_id or "",
            "x-use-polaris": "false",
            "x-transaction-source": (
                f"Interface=Web,Interface-Name=PUBLIC,Interface-Type=Service Portal,"
                f"Interface-SysID={self.portal_id or ''}"
            ),
            "referer": f"{self.base_public_url}?id=opportunities",
        }
        if json_post:
            headers["content-type"] = "application/json;charset=UTF-8"
        else:
            headers["x-requested-with"] = "XMLHttpRequest"
        return headers

    def _build_sp_page_url(self, page_id, request_uri, extra_params):
        params = [f"id={page_id}", f"time={int(time.time() * 1000)}"]
        if self.portal_id:
            params.append(f"portal_id={self.portal_id}")
        for key, value in extra_params.items():
            if value:
                params.append(f"{key}={quote(str(value), safe='')}")
        params.append(f"request_uri={quote(request_uri, safe='')}")
        return f"https://www.buyict.gov.au/api/now/sp/page?{'&'.join(params)}"

    def _build_rectangle_url(self, rectangle_id, page_id, extra_params=None):
        query = [f"id={page_id}"]
        if extra_params:
            for key, value in extra_params.items():
                if value:
                    query.append(f"{key}={quote(str(value), safe='')}")
        return f"https://www.buyict.gov.au/api/now/sp/rectangle/{rectangle_id}?{'&'.join(query)}"

    def _find_rectangle_id_for_widget(self, sp_page_result, widget_id):
        for container in sp_page_result.get("containers", []) or []:
            for row in container.get("rows", []) or []:
                for col in row.get("columns", []) or []:
                    for widget_wrapper in col.get("widgets", []) or []:
                        widget = widget_wrapper.get("widget") or {}
                        if widget.get("id") == widget_id:
                            return widget_wrapper.get("instance_id")
        return None

    def _safe_json(self, raw):
        try:
            return json.loads(raw)
        except Exception:
            self.logger.warning("Failed to parse JSON response")
            return {}

    def _label_value_map(self, rows):
        mapping = {}
        if not isinstance(rows, list):
            return mapping
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = row.get("label")
            value = row.get("value")
            if label:
                label = ' '.join(str(label).split()).rstrip(' :')
                # Keep canonical field names and preserve unfamiliar labels as evidence.
                canonical = {name.casefold(): name for name in (
                    'RFQ ID', 'RFI ID', 'RFQ type', 'RFI type', 'RFQ published date',
                    'RFI published date', 'RFQ closing date', 'RFI closing date',
                    'Deadline for asking questions', 'Buyer', 'Buyer contact',
                    'Estimated start date', 'Initial contract duration', 'Extension term',
                    'Number of extensions', 'Location of work', 'Working arrangements',
                    'Industry briefing')}
                mapping[canonical.get(label.casefold(), label)] = html_to_text(value)
        return mapping

    def _discover_opportunities_rectangle_ids(self, sp_page_result):
        """
        Discover results/filter rectangle ids from sp/page JSON.
        - Results ID: widget id == opportunities_v2
        - Filter ID: instance_id present in JSON but not in top-level container widgets
        """
        top_level_ids = set()
        results_id = None
        for container in sp_page_result.get("containers", []) or []:
            for row in container.get("rows", []) or []:
                for col in row.get("columns", []) or []:
                    for widget_wrapper in col.get("widgets", []) or []:
                        iid = widget_wrapper.get("instance_id")
                        wid = (widget_wrapper.get("widget") or {}).get("id")
                        if iid:
                            top_level_ids.add(iid)
                        if wid == "opportunities_v2" and iid:
                            results_id = iid

        all_ids = self._collect_instance_ids(sp_page_result)
        filter_candidates = [iid for iid in all_ids if iid not in top_level_ids]
        filter_id = filter_candidates[0] if filter_candidates else None

        return (
            results_id or self.FALLBACK_RESULTS_RECTANGLE_ID,
            filter_id or self.FALLBACK_FILTER_RECTANGLE_ID,
        )

    def _discover_detail_rectangle_id_from_page_html(self, html_text):
        """
        Detail pages expose the rectangle instance in the bootstrap HTML.
        Prefer dynamic extraction of widget id 'opportunity_rfq_details'.
        """
        if not html_text:
            return None
        # Decode bootstrap objects and bind instance_id to its own widget wrapper.
        # A nearby instance can belong to an entirely different widget.
        decoder = json.JSONDecoder()
        def find(value):
            if isinstance(value, dict):
                widget = value.get('widget')
                if isinstance(widget, dict) and widget.get('id') == 'opportunity_rfq_details':
                    iid = value.get('instance_id')
                    if isinstance(iid, str) and re.fullmatch(r'[a-f0-9]{32}', iid):
                        return iid
                for child in value.values():
                    result = find(child)
                    if result:
                        return result
            elif isinstance(value, list):
                for child in value:
                    result = find(child)
                    if result:
                        return result
            return None
        for match in re.finditer(r'\{', html_text):
            try:
                value, _ = decoder.raw_decode(html_text, match.start())
            except ValueError:
                continue
            result = find(value)
            if result:
                return result
        return None

    def _collect_instance_ids(self, obj):
        ids = []

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "instance_id" and isinstance(v, str):
                        ids.append(v)
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)

        walk(obj)
        # Preserve order while deduping
        seen = set()
        ordered = []
        for iid in ids:
            if iid in seen:
                continue
            seen.add(iid)
            ordered.append(iid)
        return ordered

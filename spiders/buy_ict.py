import json
import re
import time
from urllib.parse import parse_qs, quote, urlparse
from urllib.parse import urljoin

import scrapy


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
    }

    # Fallback IDs observed currently; spider attempts dynamic discovery first.
    FALLBACK_FILTER_RECTANGLE_ID = "f3c3ac0a8738d950f973a8e50cbb35c2"
    FALLBACK_RESULTS_RECTANGLE_ID = "bc93ec0a8738d950f973a8e50cbb3598"
    FALLBACK_DETAIL_RECTANGLE_ID = "698ffe151b4d411064f3a68b274bcbea"

    def __init__(self, max_pages=1, page_size=90, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_pages = max(1, int(max_pages))
        self.page_size = max(1, int(page_size))
        self.base_public_url = "https://www.buyict.gov.au/public"
        self.portal_id = None
        self.seen_detail_urls = set()

    async def start(self):
        url = f"{self.base_public_url}?id=opportunities"
        yield scrapy.Request(
            url=url,
            callback=self.parse_opportunities_landing,
            meta={"cookiejar": 1, "spider_name": self.name},
        )

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

        # Match current Playwright spider behavior: include all status groups.
        try:
            for choice in payload["filters"]["opp_status"]["choices"]:
                choice["selected"] = True
        except Exception:
            pass

        for page_num in range(1, self.max_pages + 1):
            start_loc = (page_num - 1) * self.page_size
            end_loc = start_loc + self.page_size - 1

            page_payload = dict(payload)
            page_payload.update(
                {
                    "newFocus": None,
                    "recTags": [],
                    "page": "opportunities",
                    "currentPage": page_num,
                    "page_size": str(self.page_size),
                    "startLoc": start_loc,
                    "endLoc": end_loc,
                    "limit": 0,
                    "getPageData": True,
                    "sessionRotationTrigger": True,
                }
            )

            results_url = self._build_rectangle_url(results_rectangle_id, "opportunities")
            yield scrapy.Request(
                url=results_url,
                method="POST",
                body=json.dumps(page_payload),
                headers=self._sp_headers(token, json_post=True),
                callback=self.parse_results_rectangle,
                meta={
                    "cookiejar": 1,
                    "token": token,
                    "page_num": page_num,
                    "spider_name": self.name,
                },
            )

    def parse_results_rectangle(self, response):
        token = response.meta["token"]
        page_num = response.meta["page_num"]
        data = self._safe_json(response.text).get("result", {}).get("data", {})
        items = data.get("pageItems", []) or []
        self.logger.info(
            "Results rectangle page=%s returned %s items (totalItems=%s)",
            page_num,
            len(items),
            data.get("totalItems"),
        )

        for summary in items:
            rel_url = summary.get("url")
            if not rel_url:
                continue
            if rel_url.startswith("?"):
                detail_url = f"{self.base_public_url}{rel_url}"
            else:
                detail_url = urljoin(self.base_public_url, rel_url)
            if detail_url in self.seen_detail_urls:
                continue
            self.seen_detail_urls.add(detail_url)
            yield response.follow(
                url=detail_url,
                callback=self.parse_detail_landing,
                meta={
                    "cookiejar": 1,
                    "token": token,
                    "summary": summary,
                    "detail_url": detail_url,
                    "spider_name": self.name,
                },
            )

    def parse_detail_landing(self, response):
        token = self._extract_g_ck(response.text) or response.meta.get("token")
        if not token:
            self.logger.warning("Missing token for detail page: %s", response.url)
            return

        parsed = urlparse(response.url)
        qs = parse_qs(parsed.query)
        page_id = (qs.get("id") or [""])[0]
        if not page_id:
            self.logger.warning("Detail page missing id param: %s", response.url)
            return

        detail_rectangle_id = self._discover_detail_rectangle_id_from_page_html(response.text)
        if not detail_rectangle_id:
            detail_rectangle_id = self.FALLBACK_DETAIL_RECTANGLE_ID

        rectangle_url = self._build_rectangle_url(
            rectangle_id=detail_rectangle_id,
            page_id=page_id,
            extra_params={
                "table": (qs.get("table") or [""])[0],
                "sys_id": (qs.get("sys_id") or [""])[0],
                "entry": (qs.get("entry") or [""])[0],
            },
        )
        yield scrapy.Request(
            url=rectangle_url,
            method="POST",
            body="{}",
            headers=self._sp_headers(token, json_post=True),
            callback=self.parse_detail_rectangle,
            meta={
                "cookiejar": 1,
                "summary": response.meta.get("summary", {}),
                "detail_url": response.meta.get("detail_url", response.url),
                "spider_name": self.name,
            },
        )

    def parse_detail_rectangle(self, response):
        summary = response.meta.get("summary", {})
        detail_url = response.meta.get("detail_url", response.url)
        result_data = self._safe_json(response.text).get("result", {}).get("data", {})
        opp_widget_data = (result_data.get("opp_widget") or {}).get("data", {})
        if not opp_widget_data:
            self.logger.warning("No opp_widget data for %s", detail_url)
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
            "rfq_id": main_details_map.get("RFQ ID") or main_details_map.get("RFI ID"),
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
        }

    def _extract_g_ck(self, html_text):
        match = re.search(r"window\.g_ck\s*=\s*'([^']+)'", html_text or "")
        return match.group(1) if match else None

    def _extract_portal_id(self, html_text):
        match = re.search(r"portal_id\s*=\s*'([^']+)'", html_text or "")
        return match.group(1) if match else None

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
                mapping[label] = value
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
        marker = '"id":"opportunity_rfq_details"'
        idx = html_text.find(marker)
        if idx == -1:
            return None
        window = html_text[max(0, idx - 5000): idx + 5000]
        m = re.search(r'"instance_id":"([a-f0-9]{32})"', window)
        return m.group(1) if m else None

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

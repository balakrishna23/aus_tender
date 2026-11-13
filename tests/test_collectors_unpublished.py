"""aus_contract: notices AusTender's API lists before their page is on the site.

On 13 November 2026 the 06:15 Sydney run failed: all 13 new notices redirected to
/System/NotFound, 12 were dropped as duplicate requests, nothing was saved, and the
runner reported FAIL(no items), which stopped the daily brief.
"""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spiders.aus_contract import AusContractSpider  # noqa: E402
import run_spiders  # noqa: E402

URL = 'https://www.tenders.gov.au/Cn/Show/{}'


def spider_with(stats):
    with patch('spiders.austender_common.DatabaseManager'):
        spider = AusContractSpider()
    spider.crawler = SimpleNamespace(stats=Mock())
    spider.crawler.stats.get_stats.return_value = stats
    return spider


class UnpublishedNoticeTests(unittest.TestCase):
    def closed(self, stats, requested, reason='finished'):
        spider = spider_with(stats)
        spider.requested_notices = requested
        spider.closed(reason)
        return spider.crawler.stats.set_value

    def test_every_new_notice_unpublished_is_a_normal_day(self):
        self.closed({'contract/page_not_found': 13}, 13).assert_called_with('crawl/nothing_due', 1)

    def test_saved_notices_are_an_ordinary_success(self):
        self.closed({'contract/page_not_found': 3, 'item_scraped_count': 10}, 13).assert_not_called()

    def test_many_unpublished_notices_still_fail(self):
        many = AusContractSpider.MAX_UNPUBLISHED_NOTICES + 1
        self.closed({'contract/page_not_found': many}, many).assert_not_called()

    def test_notices_that_never_reached_the_parser_still_fail(self):
        self.closed({'contract/page_not_found': 1}, 13).assert_not_called()

    def test_interrupted_crawl_is_not_marked(self):
        self.closed({'contract/page_not_found': 13}, 13, reason='shutdown').assert_not_called()

    def start(self, notices, stored):
        spider = spider_with({})
        spider.db_manager = Mock()
        spider.db_manager.load_column_map.return_value = {n: n for n in stored}
        spider.db_manager.get_state.return_value = '2026-11-12'
        with patch.object(spider, '_api_notices', return_value=notices):
            return spider, list(spider.start_requests())

    def test_no_new_notices_is_a_normal_day(self):
        spider, requests = self.start([('CN1', URL.format('a'))], {'CN1'})
        self.assertEqual(requests, [])
        spider.crawler.stats.set_value.assert_called_with('crawl/nothing_due', 1)

    def test_each_notice_bypasses_the_duplicate_filter(self):
        spider, requests = self.start([('CN1', URL.format('a')), ('CN2', URL.format('b'))], set())
        self.assertEqual([r.meta['notice_id'] for r in requests], ['CN1', 'CN2'])
        self.assertTrue(all(r.dont_filter for r in requests))
        self.assertEqual(spider.requested_notices, 2)
        spider.crawler.stats.set_value.assert_not_called()


class RunnerTests(unittest.TestCase):
    # The aus_contract stats from the failed 13 November run, with and without the new stat.
    STATS = ("'contract/page_not_found': 1, 'downloader/response_count': 15, "
             "'downloader/response_status_count/302': 13, 'finish_reason': 'finished'")

    def run_with(self, log_text):
        with tempfile.TemporaryDirectory() as logs, \
                patch.object(run_spiders, 'LOG_DIR', Path(logs)), \
                patch.object(sys, 'argv', ['run_spiders.py', '--spiders', 'aus_contract', '--no-sync', '--no-feed']), \
                patch.object(run_spiders, 'load_dotenv'), patch.object(run_spiders, 'DatabaseManager'), \
                patch.object(run_spiders, 'rotate_log'), patch.object(run_spiders, 'collect_db_count', return_value=1), \
                patch.object(run_spiders, 'run_spider', return_value=0), \
                patch.object(run_spiders, 'read_text', return_value=log_text), \
                patch.dict(os.environ), redirect_stdout(io.StringIO()):
            return run_spiders.main()

    def test_the_13_november_run_failed_without_the_stat(self):
        self.assertEqual(self.run_with(self.STATS), 1)

    def test_and_passes_with_it(self):
        self.assertEqual(self.run_with(self.STATS + ", 'crawl/nothing_due': 1"), 0)


if __name__ == '__main__':
    unittest.main()

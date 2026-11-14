"""Cross tests written by Claude for the contract-notice health canary (code by Codex, 14 November 2026).

Risk: the canary is the newest stored notice. AusTender withdraws notices; if that one page goes
away, every quiet morning would read as a failed source and the brief would be skipped, as on
13 November. The site is healthy when any recent stored notice still opens correctly.
"""
import unittest

from test_collectors_pending import contract_html, contract_spider, response, start
from test_collectors_unpublished import URL

NOT_FOUND = 'https://www.tenders.gov.au/System/NotFound'


class CanaryResilience(unittest.TestCase):
    def run_batch(self, stored, healthy, unpublished=13):
        """stored: notice ids oldest first; healthy: ids whose page still opens."""
        spider = contract_spider()
        for notice in stored:
            spider.db_manager.conn.execute('INSERT INTO contracts (cn_id, url) VALUES (?, ?)',
                                           (notice, URL.format(notice)))
        requests = start(spider, [('CN' + str(i), URL.format(str(i))) for i in range(unpublished)])
        canaries = 0
        for request in requests:
            if request.meta.get('contract_canary'):
                canaries += 1
                notice = request.meta['notice_id']
                page = response(request, contract_html(notice)) if notice in healthy else response(request, url=NOT_FOUND)
                self.assertEqual(list(spider.parse_contract(page)), [])
            else:
                self.assertEqual(list(spider.parse_contract(response(request, url=NOT_FOUND))), [])
        spider.closed('finished')
        return spider.crawler.stats.values, canaries

    def test_withdrawn_newest_notice_does_not_fail_a_quiet_day(self):
        stats, _ = self.run_batch(['CN-A', 'CN-B', 'CN-C'], healthy={'CN-A', 'CN-B'})
        self.assertEqual(stats.get('crawl/nothing_due'), 1)

    def test_a_later_failing_canary_cannot_undo_an_earlier_healthy_one(self):
        stats, _ = self.run_batch(['CN-A', 'CN-B', 'CN-C'], healthy={'CN-C'})
        self.assertEqual(stats.get('crawl/nothing_due'), 1)

    def test_all_canaries_missing_still_fails_loudly(self):
        stats, _ = self.run_batch(['CN-A', 'CN-B', 'CN-C'], healthy=set())
        self.assertNotIn('crawl/nothing_due', stats)

    def test_canary_requests_stay_few_and_are_not_counted_as_notices(self):
        stats, canaries = self.run_batch(['CN-%d' % n for n in range(50)], healthy=set())
        self.assertLessEqual(canaries, 3)
        self.assertEqual(stats.get('contract/page_not_found'), 13)

    def test_single_stored_notice_still_works_as_the_canary(self):
        stats, canaries = self.run_batch(['CN-A'], healthy={'CN-A'})
        self.assertEqual((canaries, stats.get('crawl/nothing_due')), (1, 1))


if __name__ == '__main__':
    unittest.main()

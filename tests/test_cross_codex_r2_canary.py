"""Every ordering of successful/failed health canaries stays monotonic."""
import itertools
import unittest
from test_collectors_pending import contract_spider, contract_html, response, start
from test_collectors_unpublished import URL


class CanaryRoundTwo(unittest.TestCase):
    def test_all_orderings_no_canary_items_or_pending_rows(self):
        for order in itertools.permutations([0, 1, 2]):
            for healthy in [set(), {'CN-A'}, {'CN-B'}, {'CN-C'}]:
                with self.subTest(order=order, healthy=healthy):
                    spider = contract_spider()
                    for notice in ['CN-A', 'CN-B', 'CN-C']:
                        spider.db_manager.conn.execute('INSERT INTO contracts(cn_id,url) VALUES (?,?)', (notice, URL.format(notice)))
                    requests = start(spider, [('CN-NEW', URL.format('new'))])
                    canaries = [r for r in requests if r.meta.get('contract_canary')]
                    self.assertEqual(len(canaries), 3)
                    for index in order:
                        req = canaries[index]
                        good = req.meta['notice_id'] in healthy
                        page = response(req, contract_html(req.meta['notice_id']) if good else '',
                                        url=None if good else 'https://www.tenders.gov.au/System/NotFound')
                        self.assertEqual(list(spider.parse_contract(page)), [])
                    for req in requests:
                        if not req.meta.get('contract_canary'):
                            list(spider.parse_contract(response(req, url='https://www.tenders.gov.au/System/NotFound')))
                    rows = spider.db_manager.conn.execute('SELECT notice_id FROM pending_notices').fetchall()
                    self.assertTrue(all(row[0] == 'CN-NEW' for row in rows))
                    spider.closed('finished')
                    self.assertEqual(spider.crawler.stats.values.get('crawl/nothing_due') == 1, bool(healthy))

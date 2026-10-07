"""AusTender contract API: versions are stored raw, contracts roll up exactly (spec 3.4, 3.5, 3.7)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))

import austender_api as api  # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'ocds_CN3695219.json').read_text(encoding='utf-8'))


def page(releases, next_url=None):
    response = Mock(status_code=200)
    response.json.return_value = {'releases': releases, 'links': {'next': next_url} if next_url else {}}
    return response


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': self.dir.name})
        env.start()
        self.addCleanup(env.stop)
        self.conn = api.connect()
        self.addCleanup(self.conn.close)


class RollupTests(ApiTestCase):
    def test_cn3695219_is_one_record_exactly_as_the_spec_says(self):
        new, touched = api.save(self.conn, FIXTURE['releases'], '2026-10-07T10:00:00')
        self.assertEqual(len(new), 6)
        api.rollup(self.conn, touched)
        row = dict(self.conn.execute("SELECT * FROM contracts WHERE cn_id = 'CN3695219'").fetchone())
        self.assertEqual(row['original_value'], 427580.0)
        self.assertEqual(row['current_value'], 2558490.39)
        self.assertEqual((row['start_date'], row['current_end_date']), ('2020-05-18', '2023-05-05'))
        self.assertEqual(row['amendment_count'], 5)
        self.assertEqual(row['exemption'], 'App A: 14. Contracts for labour hire.')
        self.assertEqual(row['is_indigenous_exemption'], 0)
        self.assertEqual(row['buyer'], 'Services Australia')
        self.assertEqual((row['unspsc'], row['category_family']), ('80111600', 'labour hire and recruitment'))
        self.assertEqual(row['first_published'], '2020-06-29')

    def test_versions_are_never_overwritten(self):
        api.save(self.conn, FIXTURE['releases'], 'first')
        changed = json.loads(json.dumps(FIXTURE['releases']))
        changed[0]['contracts'][0]['value']['amount'] = '1.00'
        new, _ = api.save(self.conn, changed, 'second')
        self.assertEqual(new, [])
        value, retrieved = self.conn.execute(
            "SELECT value, retrieved_at FROM contract_versions WHERE amendment_no = 0").fetchone()
        self.assertEqual((value, retrieved), (427580.0, 'first'))

    def test_extensions_are_counted_when_the_end_date_moves_later(self):
        versions = [dict(cn_id='CN1', amendment_no=n, release_date=f'2026-0{n + 1}-01', end_date=end,
                         value=v, published_on=f'2026-0{n + 1}-01')
                    for n, (end, v) in enumerate([('2026-06-30', 100), ('2026-06-30', 150), ('2026-12-31', 200)])]
        for v in versions:
            v.update({k: None for k in api.ROLLUP_FIELDS if k not in v})
        row = api.rollup_one(versions)
        self.assertEqual((row['amendment_count'], row['extension_count']), (2, 1))
        self.assertEqual((row['original_end_date'], row['current_end_date']), ('2026-06-30', '2026-12-31'))


class FieldTests(unittest.TestCase):
    def test_indigenous_exemption_is_item_16_only(self):
        self.assertTrue(api.INDIGENOUS_EXEMPTION.search('App A: 16. Procurement from an SME with at least 50% '
                                                        'Indigenous ownership'))
        self.assertFalse(api.INDIGENOUS_EXEMPTION.search('App A: 14. Contracts for labour hire.'))
        self.assertFalse(api.INDIGENOUS_EXEMPTION.search('App A: 160. Something else'))

    def test_category_families(self):
        self.assertEqual(api.family('80111600'), 'labour hire and recruitment')
        self.assertEqual(api.family('81111500'), 'ICT services')
        self.assertEqual(api.family('43232300'), 'ICT services')
        self.assertIsNone(api.family('72100000'))

    def test_dates_are_canberra_days(self):
        self.assertEqual(api.sydney_day('2023-05-04T14:00:00Z'), '2023-05-05')
        self.assertEqual(api.sydney_day('2026-11-29T13:00:00Z'), '2026-11-30')  # daylight saving

    def test_contact_officer_and_supplier(self):
        row = api.version_row(FIXTURE['releases'][0], 'now')
        self.assertEqual((row['contact_name'], row['contact_phone']), ('PURCHASING HELPDESK', '18 0090 6144'))
        self.assertEqual((row['supplier_abn'], row['buyer_abn']), ('82139631854', '90794605008'))


class FetchTests(ApiTestCase):
    def test_follows_the_page_cursor(self):
        http = Mock()
        http.get.side_effect = [page(FIXTURE['releases'][:3], 'https://api.test/next'), page(FIXTURE['releases'][3:])]
        releases = list(api.walk('contractPublished', *[api.datetime(2020, 6, 1)] * 2, http=http, sleep=lambda s: None))
        self.assertEqual(len(releases), 6)
        self.assertEqual(http.get.call_args_list[1].args[0], 'https://api.test/next')

    def test_history_skips_finished_months_on_a_rerun(self):
        http = Mock()
        http.get.return_value = page([])
        with patch.object(api, 'date') as fake_date:
            fake_date.today.return_value = date(2026, 10, 7)
            fake_date.side_effect = lambda *a: date(*a)
            api.run_history(self.conn, date(2026, 1, 1), http=http, sleep=lambda s: None)
            first = http.get.call_count
            api.run_history(self.conn, date(2026, 1, 1), http=http, sleep=lambda s: None)
        self.assertEqual(first, 10)  # Jan..Oct
        self.assertEqual(http.get.call_count - first, 3)  # only the recent months are re-read

    def test_transient_errors_are_retried(self):
        http = Mock()
        http.get.side_effect = [Mock(status_code=503, text='busy'), page([])]
        with patch.object(api.time, 'sleep'):
            self.assertEqual(api.get_json('https://api.test/x', http)['releases'], [])


if __name__ == '__main__':
    unittest.main()

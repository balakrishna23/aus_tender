from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from market_intelligence.panels import worklist, write_worklist


def source():
    return dict(as_of='2026-11-19', procurement_snapshot_id='example-snapshot',
                procurement_source_sha256='abc', warnings=['Freshness unverified'], buyers=[dict(
                    buyer_key='buyer-a',buyer='Example Agency',rank=1,match_status='exact_name',
                    review_status='unreviewed',register_context_stale=False,known_panel_routes=[dict(
                        son_id='SON123',panel_title='Example Panel',notice_expiry='2027-02-17',
                        linked_contracts=3,distinct_expiry_claims=1,panel_source_url='https://example.invalid/son')])])


class PanelWorklistTests(unittest.TestCase):
    def test_expiry_does_not_imply_intake_or_access(self):
        row=worklist(source())['panels'][0]
        self.assertEqual(row['days_to_recorded_notice_expiry'],90)
        self.assertEqual(row['date_status'],'recorded_notice_date_within_90_days')
        self.assertEqual(row['intake_status'],'unknown')
        self.assertEqual(row['illoura_membership_status'],'unknown')
        self.assertEqual(row['eligibility_status'],'not_assessed')
        self.assertEqual(row['invitation_status'],'unknown')

    def test_conflicting_dates_preserved_without_selecting_latest(self):
        report=source()
        other=deepcopy(report['buyers'][0]);other.update(buyer_key='buyer-b',buyer='Other Agency',rank=2)
        other['known_panel_routes'][0]['notice_expiry']='2028-01-01'
        report['buyers'].append(other)
        row=worklist(report)['panels'][0]
        self.assertIsNone(row['notice_expiry'])
        self.assertEqual(row['date_status'],'conflicting_notice_dates')
        self.assertEqual(row['buyer_count'],2)
        self.assertEqual(row['linked_contracts'],6)
        self.assertEqual(len(row['observed_notice_expiry_dates']),2)

    def test_source_conflict_remains_even_with_no_selected_date(self):
        report=source();route=report['buyers'][0]['known_panel_routes'][0]
        route.update(notice_expiry=None,distinct_expiry_claims=2)
        self.assertEqual(worklist(report)['panels'][0]['date_status'],'conflicting_notice_dates')

    def test_repeated_route_does_not_double_count_and_does_not_mutate_input(self):
        report=source();report['buyers'][0]['known_panel_routes']*=2
        original=deepcopy(report)
        self.assertEqual(worklist(report)['panels'][0]['linked_contracts'],3)
        self.assertEqual(report,original)

    def test_boundary_missing_and_passed_dates(self):
        for value,expected in [(None,'notice_date_unknown'),('2026-11-18','recorded_notice_date_passed'),
                               ('2026-11-19','recorded_notice_date_within_90_days'),
                               ('2027-02-18','recorded_notice_date_later')]:
            report=source();report['buyers'][0]['known_panel_routes'][0]['notice_expiry']=value
            self.assertEqual(worklist(report)['panels'][0]['date_status'],expected)

    def test_empty_and_provenance(self):
        report=source();report['buyers']=[]
        result=worklist(report)
        self.assertEqual(result['panels'],[])
        self.assertEqual(result['procurement_source_sha256'],'abc')
        self.assertEqual(result['warnings'],report['warnings'])
        self.assertEqual(result['as_of'],date(2026,11,19))

    def test_render_escapes_external_values_and_unsafe_urls(self):
        report=source();route=report['buyers'][0]['known_panel_routes'][0]
        route.update(panel_title='<script>alert(1)</script>',panel_source_url='javascript:alert(1)')
        with tempfile.TemporaryDirectory() as folder:
            result=write_worklist(worklist(report),folder)
            page=Path(result['html']).read_text(encoding='utf-8')
            self.assertNotIn('<script>',page)
            self.assertNotIn('href="javascript:',page)
            self.assertIn('&lt;script&gt;',page)
            self.assertEqual(len(json.loads(Path(result['json']).read_text())['panels']),1)


if __name__=='__main__':unittest.main()

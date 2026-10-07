"""Engine views built from small source databases: renewals, BuyICT links, organisations, MMR, hot list."""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_intelligence as bi  # noqa: E402

TODAY = date(2026, 10, 7)
CONTRACTS = [
    # cn_id, buyer, supplier, supplier_abn, description, unspsc, family, value, start, end, page
    ('CN4226826', 'Australian Prudential Regulation Authority', 'DXC TECHNOLOGY AUSTRALIA PTY LIMITED',
     '18008476944', 'Contractor', '80111600', 'labour hire and recruitment', 497887.5, '2025-12-01', '2026-11-30'),
    ('CN4262189', 'Department of Foreign Affairs and Trade', 'DXC Technology Australia Pty Ltd', '18008476944',
     'IT Support', '43200000', 'ICT equipment and general', 32197728.73, '2026-07-01', '2029-06-30'),
    ('CN4259794', 'Department of Employment and Workplace Relations', 'MACROTECH GROUP', '11111111111',
     'ICT Contractor Services - LH-06470 - Senior Business Analyst', '80111600', 'labour hire and recruitment',
     154440.0, '2026-06-30', '2026-12-31'),
    ('CN1', 'Department of Defence', 'Old Builder', '22222222222', 'Building works', '72100000', None,
     9e6, '2020-01-01', '2026-12-01'),
]


def make_db(directory, name, script, rows=()):
    with closing(sqlite3.connect(directory / f'{name}.db')) as conn:
        conn.executescript(script)
        for sql, values in rows:
            conn.executemany(sql, values)
        conn.commit()


class BuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': tmp.name})
        env.start()
        self.addCleanup(env.stop)
        make_db(self.dir, 'contract_api', '''CREATE TABLE contracts (cn_id, buyer, buyer_abn, supplier, supplier_abn,
            description, unspsc, category_family, current_value, start_date, current_end_date, amendment_count,
            contact_name, contact_email, contact_phone, contact_branch, contact_division, is_indigenous_exemption,
            exemption, first_published, page_url, source_url)''',
                [('INSERT INTO contracts VALUES (?,?,NULL,?,?,?,?,?,?,?,?,0,NULL,NULL,NULL,NULL,NULL,0,NULL,'
                  "'2026-01-01','https://www.tenders.gov.au/Cn/Show/x','https://api')", CONTRACTS)])
        make_db(self.dir, 'buy_ict', 'CREATE TABLE buy_ict_tenders (rfq_id, title, buyer, url)',
                [('INSERT INTO buy_ict_tenders VALUES (?,?,?,?)',
                  [('LH-06470', 'Senior Business Analyst', 'DEWR', 'https://buyict/LH-06470')])])
        make_db(self.dir, 'organisations', '''
            CREATE TABLE mmr_list (list_date, organisation, contract_count);
            CREATE TABLE mmr_matches (organisation, supplier_abn, match_method, review);''',
                [('INSERT INTO mmr_list VALUES (?,?,?)',
                  [('2026-10-07', 'DXC TECHNOLOGY AUSTRALIA PTY LIMITED', 5)]),
                 ('INSERT INTO mmr_matches VALUES (?,?,?,?)',
                  [('DXC TECHNOLOGY AUSTRALIA PTY LIMITED', '18008476944', 'name', None)])])

    def test_renewal_windows(self):
        renewals = {r['cn_id']: r for r in bi.build_renewals(TODAY, {'18008476944'})}
        self.assertEqual((renewals['CN4226826']['window'], renewals['CN4226826']['days_left']), ('31-60 days', 54))
        self.assertEqual(renewals['CN4226826']['supplier_on_mmr_list'], 1)
        self.assertNotIn('CN1', renewals)            # not ICT or labour hire
        self.assertNotIn('CN4262189', renewals)      # ends in 2029

    def test_buyict_role_links_with_estimated_rate(self):
        links, stats = bi.build_buyict_links()
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual((link['buyict_id'], link['cn_id'], link['contract_value']), ('LH-06470', 'CN4259794', 154440))
        self.assertEqual(link['business_days'], 130)
        self.assertAlmostEqual(link['estimated_day_rate'], 1188.0)
        self.assertIn('Estimate', link['rate_note'])
        self.assertEqual(stats['ids_found_in_buyict'], 1)

    def test_dxc_names_resolve_to_one_organisation(self):
        organisations, names, review = bi.build_organisations()
        dxc = {n['raw_name']: n['org_id'] for n in names if n['raw_name'].upper().startswith('DXC')}
        self.assertEqual(set(dxc.values()), {'ABN18008476944'})
        self.assertIn('DXC TECHNOLOGY AUSTRALIA PTY LIMITED', dxc)  # the NIAA list's spelling (no ABN)
        old = next(o for o in organisations if o['org_id'] == 'ABN22222222222')
        self.assertEqual(old['name'], 'Old Builder')     # unrelated companies stay separate

    def test_mmr_candidates_are_never_confirmed(self):
        primes, candidates = bi.build_mmr(TODAY, bi.contract_counts(TODAY))
        self.assertEqual([(p['abn'], p['mmr_contracts']) for p in primes], [('18008476944', 5)])
        self.assertEqual([c['cn_id'] for c in candidates], ['CN4262189'])
        self.assertTrue(candidates[0]['status'].startswith('candidate'))

    def test_quarters_and_business_days(self):
        self.assertEqual(bi.fy_quarter_range('Quarter 3 2026/2027'), (date(2027, 1, 1), date(2027, 3, 31)))
        self.assertEqual(bi.fy_quarter_range('Quarter 2 2026/2027'), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(bi.business_days(date(2026, 12, 24), date(2026, 12, 29)), 2)  # Christmas, Boxing Day
        self.assertEqual(bi.name_key('DXC Technology Australia Pty. Ltd.'), bi.name_key('DXC TECHNOLOGY AUSTRALIA LIMITED'))

    def test_hot_list_keeps_ict_plans_due_soon_with_a_contact(self):
        plans = [dict(kind='Planned procurement', is_relevant=r, expected_from=f, contact_email=e, contact_phone=None)
                 for r, f, e in [(1, '2026-10-01', 'a@gov.au'), (1, '2027-04-01', 'b@gov.au'), (0, '2026-10-01', 'c@gov.au'),
                                 (1, '2027-07-01', 'd@gov.au'), (1, '2026-11-01', None)]]
        self.assertEqual([p['contact_email'] for p in bi.hot_list(plans, TODAY)], ['a@gov.au', 'b@gov.au'])

    def test_full_build_writes_every_view(self):
        with patch.object(sys, 'argv', ['build_intelligence.py']):
            self.assertEqual(bi.main([]), 0)
        with closing(sqlite3.connect(self.dir / 'intelligence.db')) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'opportunities', 'renewals', 'buyict_links', 'organisations', 'mmr_candidates',
                             'exemption_use', 'hot_list', 'panel_members'} <= tables)
            info = json.loads(conn.execute('SELECT buyict_link_stats FROM build_info').fetchone()[0])
            self.assertEqual(info['contracts_linked'], 1)


if __name__ == '__main__':
    unittest.main()

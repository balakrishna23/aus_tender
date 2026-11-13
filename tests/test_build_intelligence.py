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

    def test_agency_contacts_have_source_date_confidence_and_safety(self):
        with closing(sqlite3.connect(self.dir / 'contract_api.db')) as conn:
            conn.execute("UPDATE contracts SET contact_name = 'Thi Anna Ly', contact_email = 'buyer@apra.gov.au', "
                         "first_published = '2026-01-01' WHERE cn_id = 'CN4226826'")
            conn.execute("UPDATE contracts SET contact_email = 'old@defence.gov.au', first_published = '2024-01-01' "
                         "WHERE cn_id = 'CN1'")
            conn.commit()
        with patch.object(bi, 'opted_out', return_value={'old@defence.gov.au'}):
            contacts = {c['email']: c for c in bi.build_agency_contacts(TODAY)}
        dva = contacts['procurement@dva.gov.au']   # spec seed contact
        self.assertEqual((dva['confidence'], dva['contact_safety'], dva['stale']), ('High', 'SAFE_GENERAL', 0))
        apra = contacts['buyer@apra.gov.au']
        self.assertEqual((apra['confidence'], apra['verified'], apra['usable_for_outreach']), ('Medium', '2026-01-01', 1))
        old = contacts['old@defence.gov.au']
        self.assertEqual((old['stale'], old['contact_safety'], old['usable_for_outreach']), (1, 'NO_CONTACT', 0))

    def test_agency_contacts_filled_and_extended_from_other_notices(self):
        with closing(sqlite3.connect(self.dir / 'contract_api.db')) as conn:
            conn.execute("UPDATE contracts SET contact_email = 'buyer@apra.gov.au', contact_phone = '00', contact_branch = NULL, "
                         "first_published = '2026-01-01' WHERE cn_id = 'CN4226826'")
            conn.commit()
        opportunities = [
            dict(source='AusTender', buyer='APRA', contact_email='buyer@apra.gov.au', contact_name='A', contact_phone='02 6213 5000',
                 published='01-Sep-2026', contact_safety='FORMAL_CHANNEL_ONLY', url='atm1'),
            dict(source='AusTender', buyer='Department of Finance', contact_email='tenders@finance.gov.au', contact_name='T',
                 contact_phone=None, published='01-Sep-2026', contact_safety='FORMAL_CHANNEL_ONLY', url='atm2'),
            dict(source='AusTender plan', buyer='NEMA', contact_email='plans@nema.gov.au', contact_name='P',
                 contact_phone='02 6111 1111', retrieved_at='2026-10-05T03:00:00', contact_safety='SAFE_GENERAL', url='plan1'),
            dict(source='AusTender', buyer='Old Agency', contact_email='old@agency.gov.au', published='01-Jan-2023',
                 contact_safety='SAFE_GENERAL', url='atm3'),
            dict(source='BuyICT', buyer='DTA', contact_email='rfq@dta.gov.au', published='2026-09-01',
                 contact_safety='FORMAL_CHANNEL_ONLY', url='rfq'),
            dict(source='AusTender', buyer='Department of Defence', contact_email='pm@engineering-adviser.com',
                 published='01-Sep-2026', contact_safety='SAFE_GENERAL', url='atm4'),
            dict(source='AusTender', buyer='Australian Broadcasting Corporation', contact_email='tenders@abc.net.au',
                 published='01-Sep-2026', contact_safety='SAFE_GENERAL', url='atm5'),
            dict(source='AusTender plan', buyer='Airservices Australia', contact_email='p@airservicesaustralia.com',
                 retrieved_at='2026-10-05', contact_safety='SAFE_GENERAL', url='plan2')]
        with patch.object(bi, 'opted_out', return_value=set()):
            contacts = {c['email']: c for c in bi.build_agency_contacts(TODAY, opportunities)}
        apra = contacts['buyer@apra.gov.au']   # existing address also belongs to a live tender
        self.assertEqual((apra['phone'], apra['source'], apra['contact_safety']),
                         ('02 6213 5000', 'AusTender contract notice', 'FORMAL_CHANNEL_ONLY'))
        self.assertEqual(apra['usable_for_outreach'], 0)
        self.assertIn('phone from AusTender tender notice', apra['enriched'])
        tender = contacts['tenders@finance.gov.au']   # open tender: formal channel only, never outreach
        self.assertEqual((tender['source'], tender['verified'], tender['contact_safety'], tender['usable_for_outreach']),
                         ('AusTender tender notice', '2026-09-01', 'FORMAL_CHANNEL_ONLY', 0))
        plan = contacts['plans@nema.gov.au']
        self.assertEqual((plan['source'], plan['verified'], plan['usable_for_outreach']),
                         ('AusTender procurement plan', '2026-10-05', 1))
        self.assertNotIn('old@agency.gov.au', contacts)   # older than the 36-month look-back
        self.assertNotIn('rfq@dta.gov.au', contacts)      # only AusTender notices are added
        adviser = contacts['pm@engineering-adviser.com']  # not an agency domain: kept, labelled, not for outreach
        self.assertEqual((adviser['confidence'], adviser['usable_for_outreach']), ('Low', 0))
        self.assertIn('outside the agency', adviser['source'])
        for own in ('tenders@abc.net.au', 'p@airservicesaustralia.com'):   # the domain names the agency
            self.assertEqual(contacts[own]['confidence'], 'Medium', own)

    def test_contact_freshness_uses_an_actual_amendment_not_its_download_date(self):
        with closing(sqlite3.connect(self.dir / 'contract_api.db')) as conn:
            conn.execute("UPDATE contracts SET contact_email='buyer@apra.gov.au', first_published='2024-01-01' "
                         "WHERE cn_id='CN4226826'")
            conn.execute('''CREATE TABLE contract_versions (buyer,contact_name,contact_email,contact_phone,
                contact_branch,contact_division,published_on,release_date,page_url,retrieved_at)''')
            conn.execute('INSERT INTO contract_versions VALUES (?,?,?,?,?,?,?,?,?,?)',
                ('Australian Prudential Regulation Authority','Current procurement team','buyer@apra.gov.au',
                 '02 1234 5678',None,None,'2026-09-01','2026-09-01T00:00:00Z','amendment-page','2026-10-07'))
            conn.commit()
        with patch.object(bi,'opted_out',return_value=set()):
            row=next(c for c in bi.build_agency_contacts(TODAY) if c['email']=='buyer@apra.gov.au')
        self.assertEqual((row['verified'],row['stale'],row['source_url'],row['name']),
                         ('2026-09-01',0,'amendment-page','Current procurement team'))

    def test_loose_and_superseded_mmr_matches_do_not_drive_recommendations(self):
        with closing(sqlite3.connect(self.dir/'organisations.db')) as conn:
            conn.execute("UPDATE mmr_matches SET match_method='loose name'")
            conn.commit()
        self.assertEqual(bi.build_mmr(TODAY,bi.contract_counts(TODAY)),([],[]))
        with closing(sqlite3.connect(self.dir/'organisations.db')) as conn:
            conn.execute("UPDATE mmr_matches SET review='confirmed'")
            conn.commit()
        self.assertEqual(len(bi.build_mmr(TODAY,bi.contract_counts(TODAY))[0]),1)

    def test_registry_entity_conflict_is_not_used_as_an_mmr_prime(self):
        with patch.object(bi,'registry_entity_names',return_value={'18008476944':'Unrelated Pty Ltd'}):
            self.assertEqual(bi.build_mmr(TODAY,bi.contract_counts(TODAY)),([],[]))
        with patch.object(bi,'registry_entity_names',return_value={'18008476944':'DXC Technology Australia Pty Ltd'}):
            primes,_=bi.build_mmr(TODAY,bi.contract_counts(TODAY))
        self.assertEqual(primes[0]['registry_identity_check'],'official entity name matches')

    def test_expired_panel_history_is_retained_and_explicitly_labelled(self):
        panel=bi.classify.config('panels')['panels'][0]
        source={'son_id':panel['son_id'],'title':panel['name'],'url':'panel-source',
                'current_suppliers':json.dumps([
                    {'Supplier Name':'Expired Pty Ltd','ABN':'18008476944','Deed Start Date':'1-Jul-2024','Deed End Date':'30-Jun-2026'},
                    {'Supplier Name':'Current Pty Ltd','ABN':'18008476944','Deed Start Date':'1-Jul-2026','Deed End Date':'30-Jun-2027'}])}
        with patch.object(bi,'rows',side_effect=lambda name,*args:[source] if name=='standing_offer' else []):
            result=bi.build_panel_members({},TODAY)
        self.assertEqual([(r['member'],r['membership_status']) for r in result],
                         [('Expired Pty Ltd','expired'),('Current Pty Ltd','current')])

    def test_company_groups_add_up_their_mmr_contracts(self):
        with closing(sqlite3.connect(self.dir / 'organisations.db')) as conn:
            conn.execute("INSERT INTO mmr_list VALUES ('2026-10-07', 'DXC ENTERPRISE AUSTRALIA PTY LTD', 1)")
            conn.execute("INSERT INTO mmr_matches VALUES ('DXC ENTERPRISE AUSTRALIA PTY LTD', '16612896527', 'name', NULL)")
            conn.commit()
        primes, _ = bi.build_mmr(TODAY, bi.contract_counts(TODAY))
        self.assertEqual({(p['organisation'], p['group_name'], p['group_mmr_contracts']) for p in primes},
                         {('DXC TECHNOLOGY AUSTRALIA PTY LIMITED', 'DXC Technology (group)', 6),
                          ('DXC ENTERPRISE AUSTRALIA PTY LTD', 'DXC Technology (group)', 6)})

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

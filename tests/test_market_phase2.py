"""Category tags, enriched export, IPP rules, BuyICT buyers, levers, watchlist and profile.

File-level tests run everywhere; the PostgreSQL class needs the disposable test database."""
from contextlib import closing
import csv
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from market_intelligence.categories import tag
from market_intelligence.extract_procurement import extract, CONTRACT_FIELDS, OPTIONAL_CONTRACT_FIELDS, PANEL_FIELDS
from market_intelligence.levers import load_ipp_rules, load_portfolio_results
from market_intelligence.buyict import load_arrangements
from market_intelligence import registers as R
from market_intelligence.supplier_checks import name_match

ABN, SUPPLIER, PRIME = '51824753556', '53900000001', '85900000004'
SECRET = 'Confidential project codename Falcon'


def collector(folder, contracts, links=(), panels=()):
    """Collector-shaped SQLite inputs with descriptions and contact columns, as on the server."""
    inputs = folder/'collector_inputs'
    inputs.mkdir(exist_ok=True)
    cols = CONTRACT_FIELDS + OPTIONAL_CONTRACT_FIELDS + ['description', 'contact_email']
    with closing(sqlite3.connect(inputs/'contract_api.db')) as c, c:
        c.execute('CREATE TABLE contracts(' + ','.join(k + ' TEXT' for k in cols) + ')')
        c.executemany('INSERT INTO contracts VALUES(' + ','.join('?' for _ in cols) + ')',
                      [tuple(r.get(k) for k in cols) for r in contracts])
    with closing(sqlite3.connect(inputs/'aus_contract.db')) as c, c:
        c.execute('CREATE TABLE contracts(cn_id TEXT, son_id TEXT, url TEXT, scraped_at TEXT)')
        c.executemany('INSERT INTO contracts VALUES(?,?,?,?)', [(cn, son, 'https://www.tenders.gov.au/Cn/Show/x', '2026-11-19') for cn, son in links])
    with closing(sqlite3.connect(inputs/'standing_offer.db')) as c, c:
        c.execute('CREATE TABLE standing_offers(' + ','.join(k + ' TEXT' for k in PANEL_FIELDS) + ')')
        c.executemany('INSERT INTO standing_offers VALUES(' + ','.join('?' for _ in PANEL_FIELDS) + ')', list(panels))
    return inputs


def contract(cn, **kw):
    base = dict(cn_id=cn, buyer='Example Federal Agency', buyer_abn=ABN, supplier='Example Supplies Pty Ltd', supplier_abn=SUPPLIER,
                category_family='labour hire and recruitment', unspsc='80111600', original_value='150000', current_value='150000',
                start_date='2026-01-01', current_end_date='2027-06-30', first_published='2026-06-01', page_url='https://www.tenders.gov.au/Cn/Show/x',
                retrieved_at='2026-11-19T00:00:00', procurement_method='Limited tender', is_indigenous_exemption='0',
                description='ICT labour hire - business analyst for ' + SECRET, contact_email='person@example.invalid')
    base.update(kw)
    return base


class CategoryTests(unittest.TestCase):
    def test_rules_in_order(self):
        cases = [(('80111600', 'labour hire and recruitment', 'Registered nurses for remote clinic'), 'nursing_agency'),
                 (('80111600', 'labour hire and recruitment', 'Locum medical officer'), 'healthcare_staffing'),
                 (('80111700', 'labour hire and recruitment', 'Executive search'), 'recruitment'),
                 (('80111600', 'labour hire and recruitment', 'IT contractor - developer'), 'ict_labour'),
                 (('80111600', 'labour hire and recruitment', 'Temporary staff to support it during peak'), 'labour_hire_general'),
                 (('81112200', 'ICT services', 'Specified personnel - cloud engineer'), 'ict_labour'),
                 (('81112200', 'ICT services', 'Software maintenance and support'), 'ict_services'),
                 (('81111500', None, 'Systems work'), 'ict_services'),
                 (('80120000', None, 'Legal advice'), 'other')]
        for args, expected in cases:
            self.assertEqual(tag(*args)[0], expected, args)

    def test_export_tags_without_keeping_descriptions_or_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            inputs = collector(folder, [contract('CN1')])
            out = folder/'export.db'
            extract(inputs, out)
            raw = out.read_bytes()
            self.assertNotIn(b'Falcon', raw)
            self.assertNotIn(b'example.invalid', raw)
            with closing(sqlite3.connect(out)) as c:
                columns = [r[1] for r in c.execute('PRAGMA table_info(contracts)')]
                row = dict(zip(columns, c.execute('SELECT * FROM contracts').fetchone()))
            self.assertNotIn('description', columns)
            self.assertEqual((row['category_tag'], row['procurement_method']), ('ict_labour', 'Limited tender'))


class RulesTests(unittest.TestCase):
    def test_official_rules_load_and_are_sourced(self):
        rules = load_ipp_rules()
        self.assertEqual(rules['mandatory_set_aside']['min_value_gst_inclusive'], 80000)
        self.assertEqual(rules['mandatory_minimum_requirements']['threshold_gst_inclusive'], 7500000)
        self.assertEqual(len(rules['mandatory_minimum_requirements']['segments']), 19)
        self.assertTrue(all(u.startswith('https://') for u in rules['sources'].values()))

    def test_inconsistent_thresholds_are_rejected(self):
        rules = load_ipp_rules()
        rules['mandatory_set_aside']['max_value_gst_inclusive'] = 10
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'ipp_rules.json'
            path.write_text(json.dumps(rules))
            with self.assertRaises(ValueError):
                load_ipp_rules(path)

    def test_portfolio_results_and_arrangements(self):
        rows = load_portfolio_results()
        self.assertEqual(sum(r['year'] == '2024-25' for r in rows), 17)
        self.assertEqual(sum(r['volume_target'] for r in rows if r['year'] == '2024-25'), 1866)
        ids = [a['son_id'] for a in load_arrangements()['arrangements']]
        self.assertIn('SON4102906', ids)

    def test_agor_portfolio_names_reach_niaa_names(self):
        """AGOR (19 Nov 2026) spells portfolios differently from NIAA's 2024-25 results."""
        from market_intelligence.levers import _portfolio_key
        renames = load_ipp_rules()['portfolio_renames']['names']
        niaa = {_portfolio_key(r['portfolio'], renames) for r in load_portfolio_results() if r['year'] == '2024-25'}
        agor = ['Health, Disability and Ageing', 'Infrastructure, Transport, Regional Development, Communications, Sport & Arts',
                'Parliamentary Departments (not a portfolio)', "Veterans' Affairs (part of the Defence Portfolio)", "Attorney-General's",
                'Climate Change, Energy, the Environment and Water', 'Agriculture, Fisheries and Forestry', 'Defence', 'Education',
                'Employment and Workplace Relations', 'Finance', 'Foreign Affairs and Trade', 'Home Affairs',
                'Industry, Science and Resources', 'Prime Minister and Cabinet', 'Social Services', 'Treasury']
        self.assertEqual({_portfolio_key(a, renames) for a in agor}, niaa)


class MatchingAndNamesTests(unittest.TestCase):
    def test_common_name_variants_match(self):
        self.assertEqual(name_match({'AUSTRALIAN MENS SHED ASSOCIATION'}, "Australian Men's Shed Association", []), 'registered_name')
        self.assertEqual(name_match({'MGS HOLDINGS PTY LTD'}, 'M.G.S. HOLDINGS PTY LTD', []), 'registered_name')
        self.assertEqual(name_match({'Seaestar Boat Charters Pty Ltd ATF Seaestar Trust'}, 'The Trustee for Seaestar Trust', []), 'registered_name')
        self.assertEqual(name_match({'CIVIL CONTRACTORS FEDE'}, 'CIVIL CONTRACTORS FEDERATION SOUTH AUSTRALIA LTD', []), 'similar_name')
        self.assertEqual(name_match({'THE TRUSTEE FOR THE N-J DISCRETIONA'}, 'The trustee for the N-J Discretionary Trust', []), 'similar_name')
        self.assertEqual(name_match({'Totally Different Name'}, 'REGISTERED HOLDINGS PTY LTD', []), 'differs')

    def test_business_names_only_for_known_organisations(self):
        import io
        header = ['REGISTER_NAME', 'BN_NAME', 'BN_STATUS', 'BN_REG_DT', 'BN_CANCEL_DT', 'BN_RENEW_DT', 'BN_STATE_NUM', 'BN_STATE_OF_REG', 'BN_ABN']
        out = io.StringIO()
        w = csv.writer(out, delimiter='\t', lineterminator='\n')
        w.writerow(header)
        w.writerow(['BUSINESS NAMES', 'EXAMPLE IT', 'Registered', '01/02/2020', '', '', '', 'NSW', SUPPLIER])
        w.writerow(['BUSINESS NAMES', 'JANE CITIZEN PLUMBING', 'Registered', '01/02/2020', '', '', '', 'VIC', PRIME])
        w.writerow(['BUSINESS NAMES', 'NO HOLDER', 'Registered', '01/02/2020', '', '', '', 'VIC', '0'])
        with tempfile.TemporaryDirectory() as tmp:
            import zipfile
            path = Path(tmp)/'business_names.zip'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('business_names_202611.csv', out.getvalue())
            records, metrics = R.parse_asic_business_names(path, {SUPPLIER})
            self.assertEqual([r.name for r in records], ['EXAMPLE IT'])
            self.assertEqual(metrics['skipped_not_organisation_in_scope'], 2)
            with self.assertRaises(R.SourceError):
                R.parse_asic_business_names(path, set())


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Disposable PostgreSQL test database not configured')
class Phase2DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine, migrate
        cls.engine = engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database != 'illoura_market_test':
            raise RuntimeError('Integration tests require the dedicated illoura_market_test database')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        from sqlalchemy import text
        from market_intelligence.procurement import import_procurement
        from market_intelligence.ingest import import_snapshot
        from test_market_intelligence import manifest
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        with self.engine.begin() as c:
            c.execute(text('TRUNCATE market.watchlist_events,market.panel_watchlist,market.profile_events,'
                           'market.current_register_snapshots,market.register_names,market.register_entities,market.register_scope,'
                           'market.register_snapshots,market.current_procurement_snapshot,market.procurement_panel_links,'
                           'market.procurement_panels,market.procurement_contracts,market.procurement_snapshots,'
                           'market.review_events,market.review_decisions,market.buyer_match_proposals,market.current_buyer_match_run,'
                           'market.buyer_match_runs,market.current_snapshots,market.observations,market.snapshots,market.pipeline_runs CASCADE'))
            c.execute(text("UPDATE market.illoura_profile SET value=NULL, evidence_url=NULL, updated_by=NULL, updated_at=NULL"))
        contracts = [contract('CN1'),
                     contract('CN2', supplier='Other Seller Pty Ltd', supplier_abn=PRIME, original_value='90000', first_published='2025-01-10'),
                     contract('CN3', is_indigenous_exemption='1', original_value='120000', description='Nurses for aged care'),
                     contract('CN4', supplier='Big Prime Ltd', supplier_abn=PRIME, unspsc='81112200', category_family='ICT services',
                              original_value='9000000', current_value='12000000', current_end_date='2028-01-01', description='ICT managed services')]
        panels = [('SON4102906', 'Digital Marketplace Panel 2', 'Digital Transformation Agency', 'ICT', '1-Jan-2024 to 30-Jun-2027',
                   'https://www.tenders.gov.au/Son/Show/x', '2026-11-19')]
        inputs = collector(root, contracts, links=[('CN1', 'SON4102906'), ('CN2', 'SON4102906')], panels=panels)
        export = root/'export.db'
        extract(inputs, export)
        import_procurement(self.engine, export)
        self.export = export
        import_snapshot(self.engine, manifest(root, [dict(zip(['Id', 'Title', 'Portfolio', 'ABN', 'Type of Body'],
                        ['O-000009', 'Unseen Agency', 'Finance', '', 'Non-corporate Commonwealth entity']))]), 'agor')

    def test_buyict_buyers_and_potential_buyers(self):
        from market_intelligence.buyict import build_buyict, write_buyict
        report = build_buyict(self.engine, date(2026, 11, 19))
        buyer = report['buyers'][0]
        self.assertEqual((buyer['buyer'], buyer['relevant_contracts_36m'], buyer['contracts_12m']), ('Example Federal Agency', 2, 1))
        self.assertEqual(buyer['top_sellers'][0]['contracts'], 1)
        self.assertEqual(buyer['invitation_history'], 'unknown')
        self.assertEqual([p['name'] for p in report['potential_buyers_without_relevant_spend']], ['Unseen Agency'])
        with tempfile.TemporaryDirectory() as tmp:
            write_buyict(report, tmp)
            self.assertTrue((Path(tmp)/'buyict_buyers.csv').read_text(encoding='utf-8').startswith('rank,buyer'))

    def test_levers(self):
        from market_intelligence.levers import build_levers
        report = build_levers(self.engine, date(2026, 11, 19))
        self.assertEqual(report['exemption_16_by_buyer'][0]['contracts'], 1)
        band = report['set_aside_band_by_buyer'][0]
        self.assertEqual(band['contracts'], 1)  # CN3 only: CN1 is bought through a BuyICT arrangement
        self.assertEqual([p['supplier'] for p in report['mmr_primes']], ['Big Prime Ltd'])

    def test_agor_fields_survive_newer_directory_rows(self):
        """On the server every AGOR body also appears in the newer Government Directory export, which
        market.organisations prefers. Body type and portfolio exist only in AGOR and must still show."""
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.matching import bridge_buyers
        from market_intelligence.buyict import build_buyict
        from market_intelligence.levers import build_levers
        from test_market_intelligence import manifest, directory_manifest
        root = Path(self.tmp.name)
        nce = 'A. Non-corporate Commonwealth entity'
        bodies = [('O-000001', 'Example Federal Agency', 'Finance', ABN, nce), ('O-000009', 'Unseen Agency', 'Finance', '', nce),
                  ('O-000010', 'Example Company', 'Finance', '', 'C. Commonwealth company')]
        import_snapshot(self.engine, manifest(root, [dict(zip(['Id', 'Title', 'Portfolio', 'ABN', 'Type of Body'], b)) for b in bodies],
                                              effective='2026-10-31'), 'agor')
        xml = '<response>' + ''.join('<item><unique_record_id>' + i + '</unique_record_id><content_id>' + str(n) + '</content_id><title>' + t
                                     + '</title><type>organisation</type>' + ('<abn>' + a + '</abn>' if a else '') + '</item>'
                                     for n, (i, t, _, a, _) in enumerate(bodies, 1)) + '</response>'
        import_snapshot(self.engine, directory_manifest(root, xml), 'government_directory')
        bridge_buyers(self.engine, self.export)
        report = build_buyict(self.engine, date(2026, 11, 19))
        buyer = report['buyers'][0]
        self.assertEqual((buyer['body_type'], buyer['portfolio']), (nce, 'Finance'))
        self.assertEqual(report['summary']['non_corporate_entities'], 2)
        self.assertEqual([p['name'] for p in report['potential_buyers_without_relevant_spend']], ['Unseen Agency'])
        finance = [r for r in build_levers(self.engine, date(2026, 11, 19))['portfolio_results'] if r['portfolio'] == 'Finance'][0]
        self.assertGreater(finance['our_category_value_12m'], 0)

    def test_category_summary_reports_catch_all_share(self):
        from market_intelligence.watchlist import category_summary
        summary = category_summary(self.engine)
        self.assertEqual(summary['catch_all_rules'], ['rule_7', 'rule_8'])
        self.assertEqual(summary['other_share_of_our_family_value'], 0.0)
        self.assertEqual(summary['untagged_contracts'], 0)
        self.assertIsNotNone(summary['catch_all_share_of_our_family_value'])
        self.assertNotIn(SECRET, json.dumps(summary, default=str))

    def test_watchlist_seed_mark_and_audit(self):
        from sqlalchemy import text
        from market_intelligence.watchlist import seed_watchlist, export_watchlist, mark_watchlist
        first = seed_watchlist(self.engine, date(2026, 11, 19))
        self.assertEqual((first['inserted'], first['refreshed']), (1, 0))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'watchlist.csv'
            export_watchlist(self.engine, path)
            rows = list(csv.DictReader(open(path, encoding='utf-8')))
            rows[0].update(marked_relevant='yes', illoura_status='applied', notes='Applied in October')
            with open(path, 'w', newline='', encoding='utf-8') as stream:
                w = csv.DictWriter(stream, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            self.assertEqual(mark_watchlist(self.engine, path, 'Kumar')['changes'], 3)
            self.assertEqual(seed_watchlist(self.engine, date(2026, 11, 19))['refreshed'], 1)
            rows[0]['illoura_status'] = 'invented'
            with open(path, 'w', newline='', encoding='utf-8') as stream:
                w = csv.DictWriter(stream, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            with self.assertRaises(ValueError):
                mark_watchlist(self.engine, path, 'Kumar')
        with self.engine.connect() as c:
            kept = c.execute(text("SELECT illoura_status, marked_relevant, portal FROM market.panel_watchlist WHERE son_id='SON4102906'")).first()
            events = c.execute(text('SELECT count(*) FROM market.watchlist_events')).scalar()
        self.assertEqual(tuple(kept), ('applied', True, 'BuyICT'))
        self.assertEqual(events, 3)

    def test_profile_changes_are_named_and_logged(self):
        from sqlalchemy import text
        from market_intelligence.watchlist import set_profile
        set_profile(self.engine, 'supply_nation_status', 'Certified', 'https://supplynation.org.au/example', 'Sirish')
        with self.assertRaises(ValueError):
            set_profile(self.engine, 'made_up_key', 'x', None, 'Sirish')
        with self.assertRaises(ValueError):
            set_profile(self.engine, 'abn', 'x', None, '')
        with self.engine.connect() as c:
            value = c.execute(text("SELECT value FROM market.illoura_profile WHERE key='supply_nation_status'")).scalar()
            events = c.execute(text("SELECT count(*) FROM market.profile_events WHERE key='supply_nation_status'")).scalar()
        self.assertEqual((value, events), ('Certified', 1))


if __name__ == '__main__':
    unittest.main()

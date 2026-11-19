"""APS agency headcount: workbook parsing everywhere; import, matching and report on the disposable PostgreSQL database."""
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

try:
    import openpyxl
except ImportError:  # the market requirements pin it; the plain collector environment may not have it
    openpyxl = None

ABN = '51824753556'


def workbook(path, total_2025=165):
    """A small workbook shaped like the APSC release tables (titles, two header rows, '.', (NC), Total, Source)."""
    wb = openpyxl.Workbook()
    wb.active.title = 'Table of Contents'
    t2 = wb.create_sheet('Table 2')
    t2.append(['Table 2: All employees: agency by employment category, 30 June 2024 and 30 June 2025', None, None, 'Back to Table of Contents'])
    t2.append([None])
    t2.append(['Agency', 'Ongoing', None, 'Non-ongoing', None, 'Total', None])
    t2.append([None, 2024, 2025, 2024, 2025, 2024, 2025])
    t2.append(['Finance', 90, 100, 10, 20, 100, 120])
    t2.append(['- Example Federal Agency', 40, 45, 5, '.', 45, 45])
    t2.append(['Old Body (NC)', 3, '.', 0, '.', 3, '.'])
    t2.append(['Total', 133, 145, 15, 20, 148, total_2025])
    t2.append([None])
    t2.append(['Source: APSED'])
    t13 = wb.create_sheet('Table 13')
    t13.append(['Table 13: All employees: agency by location, 30 June 2025', None, 'Back to Table of Contents'])
    t13.append([None])
    t13.append(['Agency', 'Location', None, 'Total'])
    t13.append([None, 'ACT', 'NSW', None])
    t13.append(['Finance', 100, 20, 120])
    t13.append(['- Example Federal Agency', 45, '.', 45])
    t13.append(['Total', 145, 20, 165])
    t88 = wb.create_sheet('Table 88')
    t88.append(['Table 88: All employees: agency by job family, 30 June 2025', None, 'Back to Table of Contents'])
    t88.append([None])
    t88.append(['Agency', 'Job Family', None, None, 'Total'])
    t88.append([None, 'ICT and Digital Solutions', 'Policy', 'No Data', None])
    t88.append(['Finance', 30, 50, 40, 120])
    t88.append(['- Example Federal Agency', 10, '.', 35, 45])
    t88.append(['Total', 40, 50, 75, 165])
    wb.save(path)
    return path


def manifest(folder, path, licence='Creative Commons Attribution 4.0 International'):
    data = Path(path).read_bytes()
    m = Path(folder)/'manifest.json'
    m.write_text(json.dumps({'status': 'complete', 'datasets': [{'key': 'aps_employment', 'source_url': 'https://www.apsc.gov.au/example'}],
                             'files': [{'dataset': 'aps_employment', 'status': 'complete', 'path': str(path), 'name': 'APS tables',
                                        'licence': licence, 'licence_url': 'https://creativecommons.org/licenses/by/4.0/',
                                        'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                        'retrieved_at': '2026-11-19T11:44:41+00:00', 'download_url': 'https://www.apsc.gov.au/example.xlsx'}]}))
    return m


@unittest.skipIf(openpyxl is None, 'openpyxl not installed')
class ApsParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_rows_portfolios_and_unpublished_cells(self):
        from market_intelligence.aps import parse_aps
        records, metrics, as_at, prev = parse_aps(workbook(self.root/'aps.xlsx'))
        self.assertEqual((as_at, prev), (date(2025, 6, 30), date(2024, 6, 30)))
        finance, agency, ceased = records
        self.assertEqual((finance['agency'], finance['portfolio'], finance['is_department_row'], finance['total']), ('Finance', 'Finance', True, 120))
        self.assertEqual((agency['agency'], agency['portfolio'], agency['is_department_row']), ('Example Federal Agency', 'Finance', False))
        self.assertIsNone(agency['non_ongoing'])  # '.' is not zero
        self.assertEqual(agency['by_job_family'], {'ICT and Digital Solutions': 10, 'Policy': None, 'No Data': 35})
        self.assertEqual(agency['by_location'], {'ACT': 45, 'NSW': None})
        self.assertEqual((ceased['agency'], ceased['ceased'], ceased['portfolio'], ceased['total']), ('Old Body', True, None, None))
        self.assertEqual((metrics['agencies'], metrics['ceased'], metrics['issues'], metrics['total']), (3, 1, 0, 165))

    def test_tables_must_add_up(self):
        from market_intelligence.aps import parse_aps
        from market_intelligence.parsers import SourceError
        with self.assertRaisesRegex(SourceError, 'does not add up'):
            parse_aps(workbook(self.root/'aps.xlsx', total_2025=166))

    def test_manifest_needs_approved_licence_and_matching_hash(self):
        from market_intelligence.aps import verified_workbook
        from market_intelligence.parsers import SourceError
        path = workbook(self.root/'aps.xlsx')
        self.assertEqual(verified_workbook(manifest(self.root, path))[0], path.resolve())
        with self.assertRaises(SourceError):
            verified_workbook(manifest(self.root, path, licence='to confirm'))
        m = manifest(self.root, path)
        path.write_bytes(path.read_bytes() + b'x')
        with self.assertRaises(SourceError):
            verified_workbook(m)


@unittest.skipIf(openpyxl is None, 'openpyxl not installed')
@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Disposable PostgreSQL test database not configured')
class ApsDatabaseTests(unittest.TestCase):
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
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.procurement import import_procurement
        from market_intelligence.matching import bridge_buyers
        from test_market_intelligence import manifest as agor_manifest, procurement_export
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        with self.engine.begin() as c:
            c.execute(text('TRUNCATE market.current_aps_snapshot,market.aps_agencies,market.aps_snapshots,'
                           'market.watchlist_events,market.panel_watchlist,'
                           'market.current_procurement_snapshot,market.procurement_panel_links,market.procurement_panels,'
                           'market.procurement_contracts,market.procurement_snapshots,'
                           'market.review_events,market.review_decisions,market.buyer_match_proposals,market.current_buyer_match_run,'
                           'market.buyer_match_runs,market.current_snapshots,market.observations,market.snapshots,market.pipeline_runs CASCADE'))
        rows = [('O-000001', 'Example Federal Agency', 'Finance', ABN, 'A. Non-corporate Commonwealth entity'),
                ('O-000002', 'Department of Finance', 'Finance', '', 'A. Non-corporate Commonwealth entity')]
        import_snapshot(self.engine, agor_manifest(self.root, [dict(zip(['Id', 'Title', 'Portfolio', 'ABN', 'Type of Body'], r)) for r in rows]), 'agor')
        export = procurement_export(self.root)
        import_procurement(self.engine, export)
        bridge_buyers(self.engine, export)

    def test_import_match_report_and_unchanged_reimport(self):
        from sqlalchemy import text
        from market_intelligence.aps import import_aps, build_aps_report, write_aps_report
        m = manifest(self.root, workbook(self.root/'aps.xlsx'))
        first = import_aps(self.engine, m)
        self.assertEqual((first['status'], first['as_at'], first['metrics']['matched']), ('complete', '2025-06-30', 2))
        self.assertEqual(import_aps(self.engine, m)['status'], 'unchanged')
        with self.engine.connect() as c:
            matches = dict(c.execute(text('SELECT agency, match_basis FROM market.current_aps_agencies')).all())
        self.assertEqual(matches, {'Finance': 'department_name', 'Example Federal Agency': 'exact_name', 'Old Body': 'unmatched'})
        report = build_aps_report(self.engine, date(2026, 11, 19))
        self.assertEqual([a['agency'] for a in report['agencies']], ['Finance', 'Example Federal Agency'])  # ceased bodies left out
        agency = report['agencies'][1]
        self.assertEqual((agency['ict_digital'], agency['ict_share_of_known'], agency['job_family_coverage']), (10, 1.0, round(10 / 45, 4)))
        self.assertEqual((agency['our_category_value_12m'], agency['our_category_value_per_employee']), (100000.0, round(100000 / 45)))
        self.assertIsNone(report['agencies'][0]['our_category_value_12m'])
        with tempfile.TemporaryDirectory() as out:
            write_aps_report(report, out)
            self.assertTrue((Path(out)/'aps_agencies.csv').read_text(encoding='utf-8').startswith('agency,portfolio'))

    def test_failed_import_writes_nothing(self):
        from sqlalchemy import text
        from market_intelligence.aps import import_aps
        from market_intelligence.parsers import SourceError
        with self.assertRaises(SourceError):
            import_aps(self.engine, manifest(self.root, workbook(self.root/'aps.xlsx', total_2025=166)))
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text('SELECT count(*) FROM market.aps_snapshots')).scalar(), 0)
            self.assertEqual(c.execute(text("SELECT status FROM market.pipeline_runs WHERE source='aps'")).scalar(), 'failed')

    def test_rejected_buyer_proposal_is_not_counted_as_reviewed_spend(self):
        from sqlalchemy import text
        from market_intelligence.aps import import_aps,build_aps_report
        from market_intelligence.review import decide
        import_aps(self.engine,manifest(self.root,workbook(self.root/'aps.xlsx')))
        with self.engine.connect() as conn:
            key=conn.execute(text('SELECT buyer_key FROM market.current_buyer_matches LIMIT 1')).scalar()
        decide(self.engine,key,'rejected',None,'Fixture reviewer','https://example.invalid/evidence','Fixture rejection')
        row=next(r for r in build_aps_report(self.engine,date(2026,11,19))['agencies'] if r['agency']=='Example Federal Agency')
        self.assertIsNone(row['our_category_value_12m'])
        self.assertFalse(row['identity_reviewed'])


if __name__ == '__main__':
    unittest.main()

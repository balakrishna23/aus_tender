"""National registers (every active ABN/ASIC/business name) and Government Directory role holders.

File-level tests run everywhere; the PostgreSQL class needs the disposable test database."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from market_intelligence import directory_roles as D
from market_intelligence import national_registers as N
from market_intelligence.parsers import SourceError, legal_key
from test_market_registers import (ASIC_HEADER, CAN_ABN, IND_ABN, ORG_ABN, OTHER_ABN, SOLE_TRADING, TRADING, abr,
                                   asic_row, manifest, table, transfer, zipped)

ACTIVE_ONLY_ABN = '33051775556'      # active, never in a contract: kept only by the national import
CANCELLED_ELSEWHERE = '66900000005'  # cancelled, never in a contract: never kept
BN_HEADER = ['REGISTER_NAME', 'BN_NAME', 'BN_STATUS', 'BN_REG_DT', 'BN_CANCEL_DT', 'BN_RENEW_DT', 'BN_STATE_NUM',
             'BN_STATE_OF_REG', 'BN_ABN']


def item(kind, uid, content, title, **fields):
    extra = ''.join('<%s>%s</%s>' % (k, v, k) for k, v in fields.items())
    return ('<item><type>%s</type><unique_record_id>%s</unique_record_id><content_id>%s</content_id><title>%s</title>%s</item>'
            % (kind, uid, content, title, extra))


DIRECTORY = ('<response>'
             + item('organisation', 'O-000001', '100', 'Department of Examples')
             + item('directory_sub_structure', 'O-000002', '200', 'ICT Services Branch', parent_organisation='100')
             + item('directory_sub_structure', 'O-000003', '300', 'Policy Branch', parent_organisation='100')
             + item('person', 'P-000001', '9001', 'Jane Example', first_name='Jane', last_name='Example', salutation='Ms',
                    honorific='Dr')
             + item('person', 'P-000002', '9002', 'Sam Sample', first_name='Sam', last_name='Sample')
             + item('person', 'P-000003', '9003', 'Alex Other', first_name='Alex', last_name='Other')
             + item('directory_role', 'R-000001', '7001', 'Chief Information Officer', contact='9001', role_belongs_to='200',
                    is_acting_current='FALSE', importance='1', email='private@example.gov.au', phone_number='02 6123 4567',
                    postal_address='GPO Box 1, Canberra')
             + item('directory_role', 'R-000002', '7002', 'Assistant Secretary', contact='9003', role_belongs_to='300',
                    is_acting_current='FALSE', importance='2')
             + item('directory_role', 'R-000003', '7003', 'Secretary', contact='9002', role_belongs_to='100',
                    is_acting_current='TRUE', importance='1')
             + item('directory_role', 'R-000004', '7004', 'Chief Procurement Officer', contact='', role_belongs_to='100',
                    is_acting_current='FALSE', importance='3')
             + '</response>')


class LegalKeyTests(unittest.TestCase):
    def test_leading_the_and_trailing_legal_forms_only(self):
        self.assertEqual(legal_key('The Example Group Pty. Ltd.'), 'example group')
        self.assertEqual(legal_key('Company of Heroes Limited'), 'company of heroes')
        self.assertEqual(legal_key('The'), '')


class DirectoryRoleTests(unittest.TestCase):
    def test_functions_from_role_or_unit_title(self):
        self.assertEqual(D.functions('Assistant Secretary', 'ICT Services Branch', 'directory_role', 'unit'), ['ict'])
        self.assertEqual(D.functions('Chief Digital Officer', '', 'directory_role', 'unit'), ['digital'])
        self.assertEqual(D.functions('Director', 'People and Culture Branch', 'directory_role', 'unit'), ['workforce'])
        self.assertEqual(D.functions('Chief Procurement Officer', '', 'directory_role', 'body'), ['procurement'])
        self.assertEqual(D.functions('Secretary', 'Department of Examples', 'directory_role', 'body'), ['agency_head'])
        self.assertEqual(D.functions('Chair', 'Board', 'single_executive_role', 'body'), ['agency_head'])
        # Policy work about Indigenous people is not a workforce function.
        self.assertEqual(D.functions('Assistant Secretary', 'First Nations People Policy Branch', 'directory_role', 'unit'), [])

    def test_parse_keeps_only_senior_function_roles_and_no_contact_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'export.xml'
            path.write_text(DIRECTORY, encoding='utf-8')
            roles, metrics = D.parse_directory_roles(path)
        by_id = {r['role_record_id']: r for r in roles}
        self.assertEqual(sorted(by_id), ['R-000001', 'R-000003', 'R-000004'])
        self.assertEqual(by_id['R-000001']['holder_name'], 'Jane Example')   # no salutation or honorific
        self.assertEqual(by_id['R-000001']['belongs_to_publisher_id'], 'O-000002')
        self.assertEqual(by_id['R-000003']['functions'], ['agency_head'])
        self.assertTrue(by_id['R-000003']['is_acting'])
        self.assertIsNone(by_id['R-000004']['holder_name'])
        self.assertEqual((metrics['not_in_scope'], metrics['without_holder']), (1, 1))
        stored = json.dumps(roles)
        for private in ('private@example', '6123', 'GPO Box', 'Ms', 'Dr', 'Alex'):
            self.assertNotIn(private, stored)

    def test_contact_like_names_and_empty_exports_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'export.xml'
            path.write_text(DIRECTORY.replace('<first_name>Jane</first_name>', '<first_name>jane@x.au</first_name>'), encoding='utf-8')
            with self.assertRaises(SourceError):
                D.parse_directory_roles(path)
            path.write_text('<response>' + item('organisation', 'O-000001', '100', 'Department') + '</response>', encoding='utf-8')
            with self.assertRaises(SourceError):
                D.parse_directory_roles(path)


class NationalProducerTests(unittest.TestCase):
    def test_abn_keeps_active_and_procurement_abns_and_never_individual_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = zipped(Path(tmp), 'public_split_1_10.zip', {'a.xml': transfer(
                abr(ORG_ABN, extra=TRADING), abr(ACTIVE_ONLY_ABN, name='ONLY ACTIVE PTY LTD'),
                abr(CAN_ABN, status='CAN', name='CLOSED CO PTY LTD'),
                abr(CANCELLED_ELSEWHERE, status='CAN', name='GONE PTY LTD'), abr(IND_ABN, 'IND', extra=SOLE_TRADING))})
            metrics = dict(records_read=0, active_read=0, invalid_abn=0, out_of_scope=0, individuals=0,
                           inactive_procurement_kept=0, members=0)
            rows = [dict(zip(N.STAGE_COLUMNS, r)) for r in N.abn_rows([data], {CAN_ABN}, metrics, [])]
        entities = {r['record_id']: r for r in rows if r['kind'] == 'e'}
        self.assertEqual(sorted(entities), sorted([ORG_ABN, ACTIVE_ONLY_ABN, CAN_ABN, IND_ABN]))
        self.assertIsNone(entities[IND_ABN]['name'])
        self.assertEqual(entities[ACTIVE_ONLY_ABN]['legal_key'], 'only active')
        self.assertEqual([r['name'] for r in rows if r['kind'] == 'n'], ['EXAMPLE IT'])
        self.assertNotIn('JANE', json.dumps(rows, default=str))
        self.assertEqual((metrics['records_read'], metrics['out_of_scope'], metrics['inactive_procurement_kept'],
                          metrics['individuals']), (5, 1, 1, 1))

    def test_table_encoding_is_decided_before_rows_are_staged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'t.txt'
            path.write_bytes('Café Pty Ltd\n'.encode('utf-8'))
            self.assertEqual(N.table_encoding(lambda: open(path, 'rb')), 'utf-8-sig')
            path.write_bytes('Café Pty Ltd\n'.encode('cp1252'))
            self.assertEqual(N.table_encoding(lambda: open(path, 'rb')), 'cp1252')


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Disposable PostgreSQL test database not configured')
class NationalDatabaseTests(unittest.TestCase):
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
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        with self.engine.begin() as c:
            c.execute(text('TRUNCATE market.current_register_snapshots,market.register_names,market.register_entities,'
                           'market.register_scope,market.register_snapshots,market.current_procurement_snapshot,'
                           'market.procurement_panel_links,market.procurement_panels,market.procurement_contracts,'
                           'market.procurement_snapshots,market.pipeline_runs,market.directory_roles,'
                           'market.directory_role_imports,market.current_snapshots,market.observations,market.snapshots CASCADE'))
        from test_market_registers import RegisterDatabaseTests
        RegisterDatabaseTests.load_contracts(self)

    def scalar(self, sql, **params):
        from sqlalchemy import text
        with self.engine.connect() as c:
            return c.execute(text(sql), params).scalar()

    def import_abn(self, folder=None):
        folder = folder or self.root
        data = zipped(folder, 'public_split_1_10.zip', {'a.xml': transfer(
            abr(ORG_ABN, extra=TRADING), abr(ACTIVE_ONLY_ABN, name='ONLY ACTIVE PTY LTD'),
            abr(CAN_ABN, status='CAN', since='20260401', name='CLOSED CO PTY LTD'),
            abr(CANCELLED_ELSEWHERE, status='CAN', name='GONE PTY LTD'), abr(IND_ABN, 'IND', extra=SOLE_TRADING),
            abr(OTHER_ABN, name='REGISTERED HOLDINGS PTY LTD'))})
        return N.import_national(self.engine, manifest(folder, 'abn', [data]), 'abn', min_rows=1)

    def test_national_abn_keeps_active_plus_procurement_and_is_idempotent(self):
        first = self.import_abn()
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(self.import_abn()['status'], 'unchanged')
        current = "FROM market.current_register_entities WHERE source='abn'"
        self.assertEqual(self.scalar('SELECT count(*) ' + current), 5)  # GONE PTY LTD is cancelled and not a supplier
        self.assertEqual(self.scalar('SELECT scope_kind ' + current + ' LIMIT 1'), 'national_active')
        self.assertEqual(self.scalar('SELECT legal_key ' + current + ' AND abn=:a', a=ACTIVE_ONLY_ABN), 'only active')
        self.assertEqual(self.scalar("SELECT count(*) FROM market.register_names WHERE name LIKE '%JANE%'"), 0)
        self.assertIsNone(self.scalar('SELECT name ' + current + ' AND abn=:a', a=IND_ABN))
        self.assertEqual(self.scalar('SELECT count(*) FROM market.register_scope'), 5)
        metrics = self.scalar("SELECT metrics FROM market.register_snapshots WHERE source='abn'")
        self.assertEqual(metrics['active_abns_loaded_share'], 1.0)

    def test_minimum_row_guard_refuses_promotion_and_keeps_current(self):
        self.import_abn()
        broken = self.root/'small'
        broken.mkdir()
        data = zipped(broken, 'public_split_1_10.zip', {'a.xml': transfer(abr(ORG_ABN), extract='2026-12-01T00:00:00')})
        with self.assertRaises(SourceError):
            N.import_national(self.engine, manifest(broken, 'abn', [data]), 'abn', min_rows=3)
        self.assertEqual(self.scalar("SELECT count(*) FROM market.current_register_entities WHERE source='abn'"), 5)
        self.assertEqual(self.scalar("SELECT count(*) FROM market.pipeline_runs WHERE status='failed'"), 1)

    def test_asic_and_business_names_national(self):
        self.import_abn()
        rows = [asic_row('ONLY ACTIVE PTY LTD', '900000002', ACTIVE_ONLY_ABN),
                asic_row('OLD NAME PTY LTD', '900000002', ACTIVE_ONLY_ABN, current=''),
                dict(asic_row('GONE PTY LTD', '900000003', CANCELLED_ELSEWHERE), Status='DRGD'),
                dict(asic_row('CLOSED CO PTY LTD', '900000004', CAN_ABN), Status='DRGD')]
        data = zipped(self.root, 'company_202611.zip', {'COMPANY_202611.csv': table(ASIC_HEADER, rows, delimiter='\t')})
        N.import_national(self.engine, manifest(self.root, 'asic_companies', [data]), 'asic_companies', min_rows=1)
        asic = "FROM market.current_register_entities WHERE source='asic_companies'"
        self.assertEqual(self.scalar('SELECT string_agg(record_id, \',\' ORDER BY record_id) ' + asic), '900000002,900000004')
        self.assertEqual(self.scalar("SELECT count(*) FROM market.register_names WHERE name_type='asic_former'"), 1)
        names = [dict(BN_NAME='EXAMPLE IT', BN_STATUS='Registered', BN_REG_DT='01/01/2020', BN_ABN=ORG_ABN),
                 dict(BN_NAME='JANE CITIZEN CONSULTING', BN_STATUS='Registered', BN_REG_DT='01/01/2020', BN_ABN=IND_ABN),
                 dict(BN_NAME='OLD TRADING', BN_STATUS='Deregistered', BN_REG_DT='01/01/2010', BN_ABN=ACTIVE_ONLY_ABN)]
        data = zipped(self.root, 'business_names_202611.zip', {'BN.csv': table(BN_HEADER, names, delimiter='\t')})
        N.import_national(self.engine, manifest(self.root, 'asic_business_names', [data]), 'asic_business_names', min_rows=1)
        bn = "FROM market.current_register_entities WHERE source='asic_business_names'"
        self.assertEqual(self.scalar('SELECT string_agg(name, \',\') ' + bn), 'EXAMPLE IT')

    def test_name_steps_do_not_depend_on_stale_planner_statistics(self):
        # 27 Nov: the national ASIC import spent 4 hours in the other-names insert because the stage was analysed
        # before the former names were added. The import must re-analyse and join indexed, analysed sets.
        from sqlalchemy import event
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(' '.join(statement.split()))

        self.import_abn()
        event.listen(self.engine, 'before_cursor_execute', record)
        self.addCleanup(event.remove, self.engine, 'before_cursor_execute', record)
        rows = [asic_row('ONLY ACTIVE PTY LTD', '900000002', ACTIVE_ONLY_ABN),
                asic_row('OLD NAME PTY LTD', '900000002', ACTIVE_ONLY_ABN, current='')]
        data = zipped(self.root, 'company_202611.zip', {'COMPANY_202611.csv': table(ASIC_HEADER, rows, delimiter='\t')})
        N.import_national(self.engine, manifest(self.root, 'asic_companies', [data]), 'asic_companies', min_rows=1)
        former = next(i for i, s in enumerate(statements) if "'asic_former'" in s and s.startswith('INSERT INTO register_stage'))
        names = next(i for i, s in enumerate(statements) if s.startswith('INSERT INTO market.register_names'))
        self.assertIn('ANALYZE register_stage', statements[former + 1:names])
        self.assertIn('ANALYZE register_kept', statements[former + 1:names])
        self.assertIn('JOIN register_kept', statements[names])
        self.assertNotIn('EXISTS', statements[names])
        self.assertEqual(self.scalar("SELECT count(*) FROM market.register_names WHERE name_type='asic_former'"), 1)

    def test_business_names_need_the_national_abn_register(self):
        names = [dict(BN_NAME='EXAMPLE IT', BN_STATUS='Registered', BN_REG_DT='01/01/2020', BN_ABN=ORG_ABN)]
        data = zipped(self.root, 'business_names_202611.zip', {'BN.csv': table(BN_HEADER, names, delimiter='\t')})
        with self.assertRaises(SourceError):
            N.import_national(self.engine, manifest(self.root, 'asic_business_names', [data]), 'asic_business_names', min_rows=1)

    def test_rap_and_supplier_views_read_only_what_they_need(self):
        from sqlalchemy import text
        self.import_abn()
        with self.engine.connect() as c:
            profile = {r['supplier_abn']: r for r in c.execute(text('SELECT * FROM market.v_supplier_profile')).mappings()}
        self.assertEqual(profile[ORG_ABN]['organisation'], 'EXAMPLE SUPPLIES PTY LTD')
        self.assertNotIn(ACTIVE_ONLY_ABN, profile)
        from market_intelligence.rap_review import load_context
        context = load_context(self.engine, ['Only Active Pty Ltd'])
        abns = {e['abn'] for e in context['entities'] if e.get('abn')}
        self.assertIn(ACTIVE_ONLY_ABN, abns)
        self.assertNotIn(OTHER_ABN, abns)

    def test_directory_roles_attach_to_the_imported_snapshot_and_map_to_agencies(self):
        from market_intelligence.ingest import import_snapshot
        from test_market_intelligence import directory_manifest
        man = directory_manifest(self.root, DIRECTORY)
        with self.assertRaises(SourceError):
            D.import_directory_roles(self.engine, man)   # Directory file not imported yet
        import_snapshot(self.engine, man, 'government_directory')
        result = D.import_directory_roles(self.engine, man)
        self.assertEqual((result['status'], result['roles']), ('complete', 3))
        self.assertEqual(D.import_directory_roles(self.engine, man)['status'], 'unchanged')
        from sqlalchemy import text
        with self.engine.connect() as c:
            rows = [dict(r) for r in c.execute(text('SELECT * FROM market.v_agency_org_map ORDER BY role_record_id')).mappings()]
        cio = rows[0]
        self.assertEqual((cio['agency'], cio['unit'], cio['function'], cio['holder_name']),
                         ('Department of Examples', 'ICT Services Branch', 'ict', 'Jane Example'))
        self.assertEqual(rows[1]['function'], 'agency_head')
        self.assertNotIn('6123', json.dumps(rows, default=str))


if __name__ == '__main__':
    unittest.main()

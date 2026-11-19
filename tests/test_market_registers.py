"""National register parsers, privacy filters and supplier identity checks.

File-level tests run everywhere; the PostgreSQL class needs the disposable test database."""
import csv
import hashlib
import io
import json
import os
from datetime import date
from pathlib import Path
import tempfile
import unittest
import zipfile

from market_intelligence import registers as R
from market_intelligence.parsers import SourceError
from market_intelligence.supplier_checks import legal_key, name_match, write_checks

ORG_ABN, CAN_ABN, IND_ABN, MISSING_ABN, OTHER_ABN = '51824753556', '53900000001', '34900000002', '15900000003', '85900000004'


def abr(abn, kind='PRV', status='ACT', since='20000101', name='EXAMPLE SUPPLIES PTY LTD', updated='20240101', extra=''):
    if kind == 'IND':
        holder = ('<LegalEntity><IndividualName type="LGL"><GivenName>JANE</GivenName><FamilyName>CITIZEN</FamilyName>'
                  '</IndividualName><BusinessAddress><AddressDetails><State>VIC</State><Postcode>3000</Postcode>'
                  '</AddressDetails></BusinessAddress></LegalEntity>')
    else:
        holder = ('<MainEntity><NonIndividualName type="MN"><NonIndividualNameText>'+name+'</NonIndividualNameText>'
                  '</NonIndividualName><BusinessAddress><AddressDetails><State>NSW</State><Postcode>2000</Postcode>'
                  '</AddressDetails></BusinessAddress></MainEntity>')
    return ('<ABR recordLastUpdatedDate="'+updated+'" replaced="N"><ABN status="'+status+'" ABNStatusFromDate="'+since+'">'+abn
            + '</ABN><EntityType><EntityTypeInd>'+kind+'</EntityTypeInd><EntityTypeText>Type '+kind+'</EntityTypeText></EntityType>'
            + holder+'<ASICNumber ASICNumberType="undetermined">900000001</ASICNumber><GST status="ACT" GSTStatusFromDate="20000701" />'
            + extra+'</ABR>')


def transfer(*records, extract='2026-11-18T12:22:32'):
    return ('<?xml version="1.0"?><Transfer><TransferInfo><FileSequenceNumber>1</FileSequenceNumber><RecordCount>'
            + str(len(records))+'</RecordCount><ExtractTime>'+extract+'</ExtractTime></TransferInfo>'+''.join(records)+'</Transfer>').encode()


TRADING = '<OtherEntity><NonIndividualName type="TRD"><NonIndividualNameText>EXAMPLE IT</NonIndividualNameText></NonIndividualName></OtherEntity>'
SOLE_TRADING = '<OtherEntity><NonIndividualName type="TRD"><NonIndividualNameText>JANE CITIZEN CONSULTING</NonIndividualNameText></NonIndividualName></OtherEntity>'
ASIC_HEADER = ['Company Name', 'ACN', 'Type', 'Class', 'Sub Class', 'Status', 'Date of Registration', 'Date of Deregistration',
               'Previous State of Registration', 'State Registration number', 'Modified since last report',
               'Current Name Indicator', 'ABN', 'Current Name', 'Current Name Start Date']
ACNC_HEADER = R.ACNC_COLUMNS + ['Address_Line_1', 'Town_City', 'Operates_in_NSW', 'Operates_in_VIC']
ORIC_HEADER = R.ORIC_COLUMNS + ['2024 Total Income', '2025 Total Income', '2025 Number of Employees', 'URL']


def table(header, rows, delimiter=',', encoding='utf-8'):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=header, delimiter=delimiter, lineterminator='\n')
    writer.writeheader()
    writer.writerows([{k: r.get(k, '') for k in header} for r in rows])
    return out.getvalue().encode(encoding)


def asic_row(name, acn, abn, current='Y', registered='01/07/2024'):
    return {'Company Name': name, 'ACN': acn, 'Type': 'APTY', 'Class': 'LMSH', 'Status': 'REGD',
            'Date of Registration': registered, 'Current Name Indicator': current, 'ABN': abn}


def acnc_row(abn, name='Example Health Charity', website='www.example.org'):
    return {'ABN': abn, 'Charity_Legal_Name': name, 'Other_Organisation_Names': 'Example Care, Inc', 'State': 'NSW',
            'Postcode': '2000', 'Charity_Website': website, 'Registration_Date': '03/12/2012', 'Charity_Size': 'Large',
            'PBI': 'Y', 'Aboriginal_or_TSI': 'Y', 'Address_Line_1': '1 Private Street', 'Town_City': 'Sydney',
            'Operates_in_NSW': 'Y'}


def oric_row(icn, abn, name='Example Aboriginal Corporation', status='Registered'):
    return {'ICN': icn, 'Corporation Name': name, 'Status Reason': status, 'Registered On': '1/02/1979', 'ABN': abn,
            'Corporation Size': 'Large', 'State/Territory (Main place of business) (Address)': 'NT',
            'Postcode (Main place of business) (Address)': '0871', 'Industry Sector(s)': 'Health care',
            'Corporation registered with ACNC?': 'Yes', '2024 Total Income': '>=$5m', '2025 Total Income': '',
            '2025 Number of Employees': '>24', 'URL': 'https://register.example/oric'}


def manifest(folder, source, files, name=None, licence='Creative Commons Attribution 3.0 Australia'):
    entries = []
    for path in files:
        entries.append({'dataset': source, 'status': 'complete', 'path': str(path), 'name': name or path.name,
                        'licence': licence, 'licence_url': 'https://creativecommons.org/licenses/by/3.0/au/',
                        'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'retrieved_at': '2026-11-19T07:00:00+00:00'})
    out = folder/(source+'-manifest.json')
    out.write_text(json.dumps({'status': 'complete', 'datasets': [{'key': source, 'source_url': 'https://data.gov.au/example'}],
                               'files': entries}))
    return out


def zipped(folder, name, members):
    path = folder/name
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for member, data in members.items():
            archive.writestr(member, data)
    return path


def fresh_metrics():
    return dict(records_read=0, invalid_abn=0, out_of_scope=0, individuals=0, duplicates=0, members=0)


class AbnExtractTests(unittest.TestCase):
    def parse(self, xml, scope):
        found, metrics, times = {}, fresh_metrics(), []
        R._parse_abn_stream(io.BytesIO(xml), scope, found, metrics, times)
        return found, metrics, times

    def test_individual_names_and_trading_names_are_never_kept(self):
        found, metrics, _ = self.parse(transfer(abr(IND_ABN, 'IND', extra=SOLE_TRADING)), {IND_ABN})
        person = found[IND_ABN]
        self.assertTrue(person.is_individual)
        self.assertIsNone(person.name)
        self.assertEqual(person.other_names, set())
        self.assertNotIn('JANE', json.dumps(person.attributes))
        self.assertEqual((person.state, person.postcode, metrics['individuals']), ('VIC', '3000', 1))

    def test_organisation_keeps_registered_and_trading_names_within_scope_only(self):
        found, metrics, times = self.parse(transfer(abr(ORG_ABN, extra=TRADING), abr(OTHER_ABN)), {ORG_ABN})
        self.assertEqual(list(found), [ORG_ABN])
        org = found[ORG_ABN]
        self.assertEqual((org.name, org.acn, org.status), ('EXAMPLE SUPPLIES PTY LTD', '900000001', 'ACT'))
        self.assertEqual(org.other_names, {('abr_trd', 'EXAMPLE IT')})
        self.assertEqual((metrics['out_of_scope'], times), (1, [date(2026, 11, 18)]))

    def test_bad_date_is_an_issue_and_duplicate_keeps_latest_record(self):
        xml = transfer(abr(ORG_ABN, since='2024130A', updated='20200101', name='OLD NAME PTY LTD'),
                       abr(ORG_ABN, updated='20250101', name='NEW NAME PTY LTD'))
        found, metrics, _ = self.parse(xml, {ORG_ABN})
        self.assertEqual((found[ORG_ABN].name, metrics['duplicates']), ('NEW NAME PTY LTD', 1))
        found, _, _ = self.parse(transfer(abr(ORG_ABN, since='2024130A')), {ORG_ABN})
        self.assertIn('invalid_date', found[ORG_ABN].issues)

    def test_dtd_and_wrong_root_are_rejected(self):
        with self.assertRaises(Exception):
            self.parse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Transfer>&a;</Transfer>', {ORG_ABN})
        with self.assertRaises(SourceError):
            self.parse(b'<Other></Other>', {ORG_ABN})

    def test_zip_members_streamed_and_scope_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            one = zipped(folder, 'public_split_1_10.zip', {'a.xml': transfer(abr(ORG_ABN), extract='2026-11-18T01:00:00')})
            two = zipped(folder, 'public_split_11_20.zip', {'b.xml': transfer(abr(CAN_ABN, status='CAN'), extract='2026-11-17T23:00:00')})
            records, metrics, extract = R.parse_abn([one, two], {ORG_ABN, CAN_ABN})
            self.assertEqual(({r.abn for r in records}, metrics['members'], extract), ({ORG_ABN, CAN_ABN}, 2, date(2026, 11, 17)))
            with self.assertRaises(SourceError):
                R.parse_abn([one], set())


class TableRegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_asic_current_and_former_names_and_zero_abn(self):
        rows = [asic_row('EXAMPLE SUPPLIES PTY LTD', '900000001', ORG_ABN),
                asic_row('OLD EXAMPLE PTY LTD', '900000001', ORG_ABN, current=''),
                asic_row('NO ABN PTY LTD', '900000019', '0')]
        path = zipped(self.root, 'company.zip', {'company_202611.csv': table(ASIC_HEADER, rows, '\t')})
        records, metrics = R.parse_asic_companies(path, {ORG_ABN})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].other_names, {('asic_former', 'OLD EXAMPLE PTY LTD')})
        self.assertEqual((records[0].registered_on, metrics['invalid_abn'], metrics['out_of_scope']), (date(2024, 7, 1), 0, 1))

    def test_asic_two_current_names_fail(self):
        rows = [asic_row('A PTY LTD', '900000001', ORG_ABN), asic_row('B PTY LTD', '900000001', ORG_ABN)]
        path = zipped(self.root, 'company.zip', {'company.csv': table(ASIC_HEADER, rows, '\t')})
        with self.assertRaises(SourceError):
            R.parse_asic_companies(path, {ORG_ABN})

    def test_acnc_drops_addresses_keeps_names_with_commas_and_filters_websites(self):
        path = self.root/'acnc.csv'
        path.write_bytes(table(ACNC_HEADER, [acnc_row(ORG_ABN), acnc_row(CAN_ABN, website='info@example.org'), acnc_row('')]))
        records, metrics = R.parse_acnc(path)
        by_abn = {r.abn: r for r in records}
        self.assertEqual((metrics['missing_abn'], len(records)), (1, 2))
        self.assertEqual(by_abn[ORG_ABN].attributes['website'], 'http://www.example.org')
        self.assertIsNone(by_abn[CAN_ABN].attributes['website'])
        self.assertIn('website_contact_data_omitted', by_abn[CAN_ABN].issues)
        self.assertEqual(by_abn[ORG_ABN].other_names, {('acnc_other', 'Example Care, Inc')})
        self.assertEqual(by_abn[ORG_ABN].attributes['operates_in'], ['NSW'])
        self.assertNotIn('Private Street', json.dumps([r.attributes for r in records]))
        self.assertNotIn('Sydney', json.dumps([r.attributes for r in records]))

    def test_oric_windows_1252_and_latest_nonempty_band(self):
        path = self.root/'oric.csv'
        path.write_bytes(table(ORIC_HEADER, [oric_row('2', ORG_ABN, name='Café Aboriginal Corporation'),
                                             oric_row('3', '', status='Deregistered')], encoding='cp1252'))
        records, metrics = R.parse_oric(path)
        first = next(r for r in records if r.icn == '2')
        self.assertEqual((metrics['encoding'], first.name), ('cp1252', 'Café Aboriginal Corporation'))
        self.assertEqual(first.attributes['income'], {'year': 2024, 'band': '>=$5m'})
        self.assertEqual(first.attributes['employees'], {'year': 2025, 'band': '>24'})
        self.assertEqual(metrics['missing_abn'], 1)

    def test_missing_columns_and_empty_tables_fail(self):
        path = self.root/'bad.csv'
        path.write_bytes(b'ICN,Name\n1,x\n')
        with self.assertRaises(SourceError):
            R.parse_oric(path)
        path.write_bytes(table(ORIC_HEADER, []))
        with self.assertRaises(SourceError):
            R.parse_oric(path)

    def test_manifest_selects_data_files_and_checks_hashes(self):
        data = zipped(self.root, 'public_split_1_10.zip', {'a.xml': transfer(abr(ORG_ABN))})
        schema = self.root/'bulkextract.xsd'
        schema.write_text('<xs:schema/>')
        files, meta = R.verified_resources(manifest(self.root, 'abn', [data, schema]), 'abn')
        self.assertEqual([f['path'].name for f in files], ['public_split_1_10.zip'])
        data.write_bytes(data.read_bytes() + b'x')
        with self.assertRaises(SourceError):
            R.verified_resources(self.root/'abn-manifest.json', 'abn')
        one, two = self.root/'one.csv', self.root/'two.csv'
        one.write_text('x')
        two.write_text('y')
        with self.assertRaises(SourceError):
            R.verified_resources(manifest(self.root, 'oric', [one, two]), 'oric')

    def test_oric_effective_date_comes_from_resource_name(self):
        files = [dict(name='8 November 2026 dataset', retrieved_at=None)]
        self.assertEqual(R._effective('oric', files, None), (date(2026, 11, 8), 'publisher_snapshot_date'))


class NameMatchTests(unittest.TestCase):
    def test_legal_forms_and_trustee_prefix(self):
        self.assertEqual(legal_key('Example Supplies Proprietary Limited'), legal_key('EXAMPLE SUPPLIES PTY. LTD.'))
        self.assertEqual(name_match({'Example Pty Ltd'}, 'THE TRUSTEE FOR EXAMPLE PTY LTD', set()), 'registered_name')

    def test_match_levels(self):
        self.assertEqual(name_match({'Example IT'}, 'EXAMPLE SUPPLIES PTY LTD', {'EXAMPLE IT'}), 'other_registered_name')
        self.assertEqual(name_match({'Example Supplies'}, 'EXAMPLE SUPPLIES PTY LTD', set()), 'same_core_name')
        self.assertEqual(name_match({'Different Co'}, 'EXAMPLE SUPPLIES PTY LTD', set()), 'differs')
        self.assertEqual(name_match({None}, 'EXAMPLE SUPPLIES PTY LTD', set()), 'no_contract_name')

    def test_report_escapes_html_and_hides_sole_trader_names(self):
        row = dict(abn=IND_ABN, status='abn_cancelled', explanation='x', name_match='not_compared_sole_trader',
                   flags=['sole_trader'], contract_supplier_names=[], registered_name=None, other_registered_names=[],
                   abn_status='CAN', abn_status_from=date(2025, 1, 1), entity_type='Individual', asic_registered_on=None,
                   oric=[], contracts=1, awards_last_365_days=1, latest_award=date(2026, 1, 1), buyers=1,
                   original_value_total=1.0, sample_contracts=['CN1'])
        evil = dict(row, abn=ORG_ABN, flags=['indigenous_corporation_oric'], contract_supplier_names=['<script>x</script>'])
        report = dict(as_of=date(2026, 11, 19), suppliers_checked=2, summary={'abn_cancelled': 2}, register_sources={},
                      contracts_without_supplier_abn={'contracts': 0}, limitations=['l'], suppliers=[row, evil])
        with tempfile.TemporaryDirectory() as tmp:
            out = write_checks(report, tmp)
            page = Path(out['html']).read_text(encoding='utf-8')
        self.assertNotIn('<script>x', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('Sole trader (name not shown)', page)


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Disposable PostgreSQL test database not configured')
class RegisterDatabaseTests(unittest.TestCase):
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
                           'market.procurement_snapshots,market.pipeline_runs CASCADE'))

    def load_contracts(self):
        from test_market_intelligence import procurement_export
        from market_intelligence.procurement import import_procurement
        base = dict(buyer='Example Federal Agency', buyer_abn=ORG_ABN, category_family='ICT services', original_value=100000,
                    current_value=100000, start_date='2026-01-01', current_end_date='2027-02-01',
                    page_url='https://www.tenders.gov.au/Cn/Show/example', retrieved_at='2026-11-19T00:00:00Z')
        contracts = [dict(base, cn_id='CN1', supplier='Example Supplies Pty Ltd', supplier_abn=ORG_ABN, first_published='2026-03-01'),
                     dict(base, cn_id='CN2', supplier='Closed Co Pty Ltd', supplier_abn=CAN_ABN, first_published='2026-06-01'),
                     dict(base, cn_id='CN3', supplier='Jane Citizen', supplier_abn=IND_ABN, first_published='2026-06-01'),
                     dict(base, cn_id='CN4', supplier='Ghost Pty Ltd', supplier_abn=MISSING_ABN, first_published='2026-06-01'),
                     dict(base, cn_id='CN5', supplier='Totally Different Name', supplier_abn=OTHER_ABN, first_published='2026-06-01'),
                     dict(base, cn_id='CN6', supplier='Bad Abn Pty Ltd', supplier_abn='12345678901', first_published='2026-06-01'),
                     dict(base, cn_id='CN7', supplier='No Abn Supplier', supplier_abn=None, first_published='2026-06-01')]
        import_procurement(self.engine, procurement_export(self.root, contracts))

    def import_abn(self):
        data = zipped(self.root, 'public_split_1_10.zip', {'a.xml': transfer(
            abr(ORG_ABN, extra=TRADING), abr(CAN_ABN, status='CAN', since='20260401', name='CLOSED CO PTY LTD'),
            abr(IND_ABN, 'IND', extra=SOLE_TRADING), abr(OTHER_ABN, name='REGISTERED HOLDINGS PTY LTD'))})
        return R.import_register(self.engine, manifest(self.root, 'abn', [data]), 'abn')

    def test_scoped_import_checks_and_privacy(self):
        from sqlalchemy import text
        from market_intelligence.supplier_checks import build_checks
        self.load_contracts()
        first = self.import_abn()
        self.assertEqual(first['status'], 'complete')
        self.assertEqual(self.import_abn()['status'], 'unchanged')
        oric = self.root/'oric.csv'
        oric.write_bytes(table(ORIC_HEADER, [oric_row('2', ORG_ABN)], encoding='cp1252'))
        R.import_register(self.engine, manifest(self.root, 'oric', [oric], name='8 November 2026 dataset'), 'oric')
        with self.engine.connect() as c:
            stored = json.dumps([list(r) for r in c.execute(text('SELECT * FROM market.register_entities')).all()], default=str)
            names = c.execute(text('SELECT count(*) FROM market.register_names WHERE name LIKE :n'), {'n': '%JANE%'}).scalar()
            scope = c.execute(text('SELECT count(*) FROM market.register_scope')).scalar()
        self.assertNotIn('JANE', stored)
        self.assertEqual(names, 0)
        self.assertEqual(scope, 5)  # five valid ABNs across suppliers and the buyer; the invalid one is excluded
        report = build_checks(self.engine, date(2026, 11, 19))
        by_abn = {r['abn']: r for r in report['suppliers']}
        self.assertEqual(by_abn[ORG_ABN]['status'], 'consistent')
        self.assertIn('indigenous_corporation_oric', by_abn[ORG_ABN]['flags'])
        self.assertEqual(by_abn[CAN_ABN]['status'], 'abn_cancelled')
        self.assertIn('cancelled_before_latest_award', by_abn[CAN_ABN]['flags'])
        self.assertEqual(by_abn[MISSING_ABN]['status'], 'abn_not_found')
        self.assertEqual(by_abn[OTHER_ABN]['status'], 'name_differs')
        self.assertEqual(by_abn['12345678901']['status'], 'invalid_contract_abn')
        person = by_abn[IND_ABN]
        self.assertEqual((person['status'], person['contract_supplier_names'], person['registered_name']), ('consistent', [], None))
        self.assertEqual(report['contracts_without_supplier_abn']['contracts'], 1)

    def test_older_register_never_replaces_current(self):
        from sqlalchemy import text
        self.load_contracts()
        new = self.root/'new'
        new.mkdir()
        old = self.root/'old'
        old.mkdir()
        (new/'oric.csv').write_bytes(table(ORIC_HEADER, [oric_row('2', ORG_ABN, name='New Name Aboriginal Corporation')]))
        (old/'oric.csv').write_bytes(table(ORIC_HEADER, [oric_row('2', ORG_ABN, name='Old Name Aboriginal Corporation')]))
        R.import_register(self.engine, manifest(new, 'oric', [new/'oric.csv'], name='8 November 2026 dataset'), 'oric')
        R.import_register(self.engine, manifest(old, 'oric', [old/'oric.csv'], name='13 October 2026 dataset'), 'oric')
        with self.engine.connect() as c:
            name = c.execute(text("SELECT name FROM market.current_register_entities WHERE source='oric'")).scalar()
        self.assertEqual(name, 'New Name Aboriginal Corporation')

    def test_failed_import_leaves_current_snapshot_and_logs_failure(self):
        from sqlalchemy import text
        self.load_contracts()
        self.import_abn()
        broken = self.root/'broken'
        broken.mkdir()
        bad = zipped(broken, 'public_split_1_10.zip', {'a.xml': b'<Transfer><ABR>'})
        with self.assertRaises(Exception):
            R.import_register(self.engine, manifest(broken, 'abn', [bad]), 'abn')
        with self.engine.connect() as c:
            current = c.execute(text("SELECT count(*) FROM market.current_register_entities WHERE source='abn'")).scalar()
            failed = c.execute(text("SELECT count(*) FROM market.pipeline_runs WHERE status='failed'")).scalar()
        self.assertEqual((current, failed), (4, 1))


if __name__ == '__main__':
    unittest.main()

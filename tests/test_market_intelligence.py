"""Parser/matching invariants plus opt-in tests against a disposable PostgreSQL database."""
import csv
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from market_intelligence.parsers import parse_agor, parse_directory, valid_abn, SourceError, name_key
from market_intelligence.matching import propose
from market_intelligence.extract_buyers import extract
from market_intelligence.extract_procurement import extract as extract_procurement,CONTRACT_FIELDS
from market_intelligence.procurement import panel_period,amount
from market_intelligence.analytics import rank

ABN = '51824753556'


def agor_file(path, rows):
    with path.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['Id','Title','Portfolio','ABN','Type of Body'])
        writer.writeheader()
        writer.writerows(rows)
    return path


def row(identifier='O-000001',name='Example Federal Agency',abn=ABN):
    return dict(zip(['Id','Title','Portfolio','ABN','Type of Body'],[identifier,name,'Example Portfolio',abn,'Non-corporate Commonwealth entity']))


def manifest(folder, rows, effective='2026-09-30'):
    path=agor_file(folder/('agor-'+effective+'.csv'),rows)
    m=folder/('manifest-'+effective+'.json')
    m.write_text(json.dumps({'status':'complete','datasets':[{'key':'agor','source_url':'https://data.gov.au/example'}],
        'files':[{'dataset':'agor','status':'complete','path':str(path),'name':'AGOR '+effective,
                  'licence':'Creative Commons Attribution 3.0 Australia','licence_url':'https://creativecommons.org/licenses/by/3.0/au/',
                  'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                  'retrieved_at':'2026-11-19T07:00:00+00:00'}]}))
    return m


def procurement_export(folder,contracts=None):
    import sqlite3
    inputs=folder/'collector_inputs';inputs.mkdir(exist_ok=True)
    source=inputs/'contract_api.db'
    with closing(sqlite3.connect(source)) as c,c:
        c.execute('CREATE TABLE contracts('+','.join(k+' TEXT' for k in CONTRACT_FIELDS)+',contact_email TEXT)')
        contracts=contracts or [dict(cn_id='CN1',buyer='Example Federal Agency',buyer_abn=ABN,
             supplier='Example Supplier',supplier_abn=ABN,category_family='ICT services',original_value=100000,
             current_value=120000,start_date='2026-01-01',current_end_date='2027-02-01',first_published='2026-01-01',
             page_url='https://www.tenders.gov.au/Cn/Show/example',retrieved_at='2026-11-19T00:00:00Z')]
        c.executemany('INSERT INTO contracts VALUES('+','.join('?' for _ in range(len(CONTRACT_FIELDS)+1))+')',
                      [tuple(r.get(k) for k in CONTRACT_FIELDS)+('private@example.invalid',) for r in contracts])
    destination=folder/'procurement.db'
    extract_procurement(inputs,destination)
    return destination


def directory_manifest(folder,xml):
    path=folder/'directory.xml';path.write_text(xml,encoding='utf-8')
    output=folder/'directory-manifest.json'
    output.write_text(json.dumps({'status':'complete','datasets':[{'key':'government_directory','source_url':'https://data.gov.au/directory'}],
        'files':[{'dataset':'government_directory','status':'complete','path':str(path),'name':'Directory export',
            'licence':'Creative Commons Attribution 4.0 International','licence_url':'https://creativecommons.org/licenses/by/4.0/',
            'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'retrieved_at':'2026-11-19T07:00:00+00:00'}]}))
    return output


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_abn_validation(self):
        self.assertEqual(valid_abn('51 824 753 556'),ABN)
        self.assertIsNone(valid_abn(''))
        for invalid in ['11111111111','00000000000','51-824-753-556','５１８２４７５３５５６']:
            with self.assertRaises(SourceError):valid_abn(invalid)

    def test_agor_preserves_publisher_identity_without_abn(self):
        records,counts=parse_agor(agor_file(self.root/'a.csv',[row(abn='')]))
        self.assertEqual(records[0].publisher_id,'O-000001')
        self.assertIsNone(records[0].abn)
        self.assertEqual(counts['source_rows'],1)

    def test_invalid_abn_is_explicit_issue(self):
        records,_=parse_agor(agor_file(self.root/'a.csv',[row(abn='bad')]))
        self.assertEqual(records[0].issues,['invalid_abn'])

    def test_agor_windows_punctuation_is_decoded_without_replacement(self):
        p=agor_file(self.root/'a.csv',[row(name='Agency \u2013 Research')])
        p.write_bytes(p.read_text(encoding='utf-8').encode('cp1252'))
        records,metrics=parse_agor(p)
        self.assertEqual(records[0].name,'Agency \u2013 Research')
        self.assertEqual(metrics['encoding'],'cp1252')

    def test_shared_abn_does_not_merge_bodies(self):
        records,_=parse_agor(agor_file(self.root/'a.csv',[row(),row('O-000002','Second Body')]))
        self.assertEqual(len(records),2)

    def test_duplicate_publisher_id_fails(self):
        with self.assertRaises(SourceError):parse_agor(agor_file(self.root/'a.csv',[row(),row()]))

    def test_required_columns_and_empty_source_fail(self):
        p=self.root/'a.csv';p.write_text('Title\nOnly a name\n')
        with self.assertRaises(SourceError):parse_agor(p)
        with self.assertRaises(SourceError):parse_agor(agor_file(p,[]))

    def test_directory_contacts_filtered_before_payload(self):
        p=self.root/'d.xml'
        p.write_text('''<response><item><type>organisation</type><unique_record_id>O-000001</unique_record_id>
        <title>Example Agency</title><content_id>100</content_id><email>private@example.invalid</email>
        <phone_number>0412345678</phone_number><contact><first_name>Private</first_name></contact></item>
        <item><type>person</type><title>Private Person</title><email>person@example.invalid</email></item>
        <item><type>role</type><title>Director Private Person</title></item></response>''')
        records,counts=parse_directory(p)
        self.assertEqual(len(records),1);self.assertEqual(counts['excluded_rows'],2)
        payload=json.dumps(records[0].payload)
        for private in ['private@example','0412345678','Private Person','first_name','email']:
            self.assertNotIn(private,payload)

    def test_directory_dtd_entity_rejected(self):
        p=self.root/'bad.xml';p.write_text('''<!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><response><item>&a;</item></response>''')
        with self.assertRaises(Exception):parse_directory(p)

    def test_directory_placeholder_flagged(self):
        p=self.root/'d.xml';p.write_text('<response><item><type>directory_sub_structure</type><unique_record_id>O-000001</unique_record_id><title>Dummy Organisation DO NOT DELETE</title></item></response>')
        self.assertEqual(parse_directory(p)[0][0].issues,['publisher_placeholder'])

    def test_directory_wrong_root_or_nested_items_rejected(self):
        p=self.root/'d.xml'
        for xml in ['<unexpected/>','<response><item><item/></item></response>']:
            p.write_text(xml)
            with self.assertRaises(SourceError):parse_directory(p)

    def test_contact_strings_in_website_fields_are_excluded(self):
        from market_intelligence.parsers import website
        for value in ['mailto:canary@example.invalid','https://example.invalid/?email=canary@example.invalid','https://user:password@example.invalid']:
            issues=[];self.assertEqual(website(value,issues),'');self.assertTrue(issues)
        self.assertEqual(website('https://www.example.gov.au',[]),'https://www.example.gov.au')


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.orgs=[dict(organisation_id='gov:O-1',name_key=name_key('Example Agency'),abn=ABN)]

    def test_exact_abn_and_name_proposals(self):
        self.assertEqual(propose('Example Agency',ABN,self.orgs)['match_status'],'exact_abn')
        self.assertEqual(propose('EXAMPLE, Agency',None,self.orgs)['match_status'],'exact_name')

    def test_shared_abn_with_unknown_name_requires_review(self):
        self.orgs.append(dict(organisation_id='gov:O-2',name_key='other body',abn=ABN))
        result=propose('Unmapped Agency',ABN,self.orgs)
        self.assertEqual(result['match_status'],'ambiguous');self.assertIsNone(result['organisation_id'])

    def test_exact_abn_and_official_name_disambiguate_without_merging(self):
        self.orgs.append(dict(organisation_id='gov:O-2',name_key='other body',abn=ABN))
        result=propose('Example Agency',ABN,self.orgs)
        self.assertEqual(result['match_status'],'exact_abn_name')
        self.assertEqual(result['organisation_id'],'gov:O-1')
        self.assertEqual(len(result['candidate_ids']),2)

    def test_abn_name_conflict_and_invalid_abn(self):
        self.orgs.append(dict(organisation_id='gov:O-2',name_key='other body',abn=None))
        self.assertEqual(propose('Other Body',ABN,self.orgs)['match_status'],'conflict')
        self.assertEqual(propose('Example Agency','invalid',self.orgs)['match_status'],'conflict')

    def test_fuzzy_is_review_only(self):
        result=propose('Example Agenc',None,self.orgs)
        self.assertEqual(result['match_status'],'ambiguous');self.assertIsNone(result['organisation_id'])

    def test_absent_register_abn_with_name_candidate_is_not_false_conflict(self):
        self.orgs[0]['abn']=None
        result=propose('Example Agency',ABN,self.orgs)
        self.assertEqual(result['match_status'],'ambiguous')
        self.assertIn('not_in_register',result['evidence'])

    def test_conflicting_register_identity_does_not_become_unique_match(self):
        self.orgs[0]['register_conflict']=True
        result=propose('Example Agency',ABN,self.orgs)
        self.assertEqual(result['match_status'],'conflict')
        self.assertIsNone(result['organisation_id'])

    def test_absent_match_is_not_invented(self):
        self.assertEqual(propose('Unrelated Department',None,self.orgs)['match_status'],'unmatched')


class ExtractionTests(unittest.TestCase):
    def test_private_atomic_subset_leaves_source_unchanged(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.db';destination=Path(folder)/'buyers.db'
            with closing(sqlite3.connect(source)) as c, c:
                c.execute('CREATE TABLE contracts(buyer,buyer_abn,contact_email)')
                c.executemany('INSERT INTO contracts VALUES(?,?,?)',[('Agency',ABN,'private@example.invalid'),('Agency',ABN,'other@example.invalid')])
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            result=extract(source,destination)
            self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(result['buyer_pairs'],1)
            with self.assertRaises(ValueError):extract(source,destination)
            with closing(sqlite3.connect(destination)) as c:
                self.assertEqual([r[1] for r in c.execute('PRAGMA table_info(contracts)')],['buyer','buyer_abn'])
            with self.assertRaises(ValueError):extract(source,source)

    def test_procurement_export_whitelists_fields_and_records_missing_sources(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path=procurement_export(Path(folder))
            with closing(sqlite3.connect(path)) as c:
                self.assertNotIn('contact_email',[r[1] for r in c.execute('PRAGMA table_info(contracts)')])
                metadata=json.loads(c.execute('SELECT payload FROM export_metadata').fetchone()[0])
                self.assertEqual(metadata['sources']['standing_offer']['status'],'missing')
                self.assertEqual(metadata['sources']['contract_api']['rows'],1)
            with self.assertRaises(ValueError):extract_procurement(Path(folder)/'collector_inputs',path)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.config={'method':'relative_percentiles','weights':{'awarded_value_365d':.45,'recent_awards_count':.25,'expiry_count_365d':.30}}

    def test_unknown_signal_is_not_renormalized_to_full_score(self):
        rows=[{'buyer':'Known','buyer_key':'a','awarded_value_365d':100,'recent_awards_count':2,'expiry_count_365d':2},
              {'buyer':'Unknown value','buyer_key':'b','awarded_value_365d':None,'recent_awards_count':2,'expiry_count_365d':2}]
        result={r['buyer_key']:r for r in rank(rows,self.config)}
        self.assertLess(result['b']['priority_score'],result['a']['priority_score'])
        self.assertEqual(result['b']['score_coverage_pct'],55)
        self.assertIsNone(result['b']['score_components']['awarded_value_365d'])

    def test_no_signal_scores_zero_and_ties_are_stable(self):
        rows=[dict(buyer=b,buyer_key=b,awarded_value_365d=0,recent_awards_count=0,expiry_count_365d=0) for b in ['B','A']]
        result=rank(rows,self.config)
        self.assertEqual([r['buyer'] for r in result],['A','B'])
        self.assertEqual(result[0]['priority_score'],0)

    def test_invalid_weights_rejected(self):
        for value in [-1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):rank([],{'method':'relative_percentiles','weights':dict(self.config['weights'],awarded_value_365d=value)})

    def test_unknown_or_inverted_panel_dates_are_not_guessed(self):
        self.assertEqual(panel_period('Always open'),(None,None))
        self.assertEqual(panel_period('1-Jul-2027 to 30-Jun-2024'),(None,None))
        self.assertEqual(str(panel_period('1-Jul-2024 to 30-Jun-2027')[1]),'2027-06-30')

    def test_bad_contract_values_remain_unknown(self):
        for value in ['not supplied','NaN','Infinity','-100']:
            issues=[];self.assertIsNone(amount(value,issues,'value'));self.assertEqual(issues,['invalid_value'])


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL test database not configured')
class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine,migrate
        cls.engine=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database!='illoura_market_test':
            raise RuntimeError('Integration tests require the dedicated illoura_market_test database')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):cls.engine.dispose()

    def setUp(self):
        from sqlalchemy import text
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        with self.engine.begin() as c:
            c.execute(text('TRUNCATE market.current_procurement_snapshot,market.procurement_panel_links,market.procurement_panels,market.procurement_contracts,market.procurement_snapshots,market.review_events,market.review_decisions,market.buyer_match_proposals,market.current_buyer_match_run,market.buyer_match_runs,market.current_snapshots,market.observations,market.snapshots,market.pipeline_runs CASCADE'))

    def scalar(self,sql):
        from sqlalchemy import text
        with self.engine.connect() as c:return c.execute(text(sql)).scalar()

    def test_idempotent_snapshot_and_migration(self):
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.db import migrate
        p=manifest(self.root,[row()]);first=import_snapshot(self.engine,p,'agor')
        second=import_snapshot(self.engine,p,'agor');migrate(self.engine)
        self.assertEqual(first['snapshot_id'],second['snapshot_id']);self.assertEqual(second['status'],'unchanged')
        self.assertEqual(self.scalar('SELECT count(*) FROM market.observations'),1)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.pipeline_runs'),2)

    def test_corrupt_input_preserves_current_and_logs_failure(self):
        from market_intelligence.ingest import import_snapshot
        p=manifest(self.root,[row()]);first=import_snapshot(self.engine,p,'agor')
        data=json.loads(p.read_text());Path(data['files'][0]['path']).write_text('corrupt')
        with self.assertRaises(SourceError):import_snapshot(self.engine,p,'agor')
        self.assertEqual(str(self.scalar('SELECT snapshot_id FROM market.current_snapshots')),first['snapshot_id'])
        self.assertEqual(self.scalar("SELECT count(*) FROM market.pipeline_runs WHERE status='failed'"),1)

    def test_transaction_rollback_after_snapshot_insert(self):
        from sqlalchemy import event
        from market_intelligence.ingest import import_snapshot
        def fail(conn,cursor,statement,params,context,executemany):
            if statement.startswith('INSERT INTO market.observations'):raise RuntimeError('Injected storage failure')
        event.listen(self.engine,'before_cursor_execute',fail)
        try:
            with self.assertRaises(RuntimeError):import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        finally:event.remove(self.engine,'before_cursor_execute',fail)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.snapshots'),0)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.observations'),0)
        self.assertEqual(self.scalar("SELECT count(*) FROM market.pipeline_runs WHERE status='failed'"),1)

    def test_old_snapshot_never_replaces_current(self):
        from market_intelligence.ingest import import_snapshot
        first=import_snapshot(self.engine,manifest(self.root,[row(name='New Name')],'2026-09-30'),'agor')
        import_snapshot(self.engine,manifest(self.root,[row(name='Old Name')],'2026-06-30'),'agor')
        self.assertEqual(str(self.scalar('SELECT snapshot_id FROM market.current_snapshots')),first['snapshot_id'])
        self.assertEqual(self.scalar('SELECT count(*) FROM market.snapshots'),2)
        self.assertEqual(self.scalar('SELECT name FROM market.organisations'),'New Name')

    def test_publisher_id_unique_constraint(self):
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        from market_intelligence.ingest import import_snapshot
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as c:c.execute(text('INSERT INTO market.observations SELECT * FROM market.observations'))

    def test_shared_abn_retains_two_organisations(self):
        from market_intelligence.ingest import import_snapshot
        import_snapshot(self.engine,manifest(self.root,[row(),row('O-000002','Second Body')]),'agor')
        self.assertEqual(self.scalar('SELECT count(*) FROM market.organisations'),2)

    def test_bridge_read_only_preserves_human_decision(self):
        import sqlite3
        from sqlalchemy import text
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.matching import bridge_buyers
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        source=self.root/'buyers.db'
        with closing(sqlite3.connect(source)) as c, c:
            c.execute('CREATE TABLE contracts(buyer,buyer_abn)')
            c.execute('INSERT INTO contracts VALUES(?,?)',('Example Federal Agency',ABN))
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        first=bridge_buyers(self.engine,source)
        self.assertEqual(first['matches']['exact_abn'],1)
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO market.review_decisions(buyer_key,organisation_id,reviewer,evidence_url) SELECT buyer_key,'gov:O-000001','reviewer','https://example.invalid/evidence' FROM market.buyer_match_proposals"))
        bridge_buyers(self.engine,source)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.review_decisions'),1)
        self.assertEqual(digest,hashlib.sha256(source.read_bytes()).hexdigest())

    def test_bridge_generations_exclude_departed_buyers_and_reject_empty(self):
        import sqlite3
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.matching import bridge_buyers
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        source=self.root/'buyers.db'
        with closing(sqlite3.connect(source)) as c, c:
            c.execute('CREATE TABLE contracts(buyer,buyer_abn)')
            c.executemany('INSERT INTO contracts VALUES(?,?)',[('Example Federal Agency',ABN),('Historical Buyer',None)])
        bridge_buyers(self.engine,source)
        with closing(sqlite3.connect(source)) as c, c:c.execute("DELETE FROM contracts WHERE buyer='Historical Buyer'")
        bridge_buyers(self.engine,source)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.current_buyer_matches'),1)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.buyer_match_proposals'),3)
        with closing(sqlite3.connect(source)) as c, c:c.execute('DELETE FROM contracts')
        with self.assertRaises(ValueError):bridge_buyers(self.engine,source)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.current_buyer_matches'),1)

    def test_human_review_audit_rejection_and_identity_change(self):
        import sqlite3
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.matching import bridge_buyers
        from market_intelligence.review import decide
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        source=self.root/'buyers.db'
        with closing(sqlite3.connect(source)) as c, c:
            c.execute('CREATE TABLE contracts(buyer,buyer_abn)')
            c.execute('INSERT INTO contracts VALUES(?,?)',('Example Federal Agency',ABN))
        bridge_buyers(self.engine,source)
        key=self.scalar('SELECT buyer_key FROM market.current_buyer_matches')
        decide(self.engine,key,'approved','gov:O-000001','reviewer','https://example.invalid/evidence')
        self.assertEqual(self.scalar('SELECT review_status FROM market.buyer_resolution'),'approved')
        import_snapshot(self.engine,manifest(self.root,[row(name='Renamed Federal Body')],'2026-10-01'),'agor')
        self.assertEqual(self.scalar('SELECT review_status FROM market.buyer_resolution'),'needs_recheck')
        self.assertTrue(self.scalar('SELECT register_context_stale FROM market.buyer_resolution'))
        decide(self.engine,key,'rejected',None,'reviewer','https://example.invalid/evidence','Unsupported identity')
        self.assertEqual(self.scalar('SELECT review_status FROM market.buyer_resolution'),'rejected')
        self.assertEqual(self.scalar('SELECT count(*) FROM market.review_events'),2)
        bridge_buyers(self.engine,source)
        self.assertEqual(self.scalar('SELECT review_status FROM market.buyer_resolution'),'rejected')

    def test_parser_revision_retains_history_and_updates_current(self):
        from unittest.mock import patch
        from market_intelligence.ingest import import_snapshot
        p=manifest(self.root,[row()])
        first=import_snapshot(self.engine,p,'agor')
        with patch('market_intelligence.ingest.PARSER_REVISION','next-test'):
            second=import_snapshot(self.engine,p,'agor')
        self.assertNotEqual(first['snapshot_id'],second['snapshot_id'])
        self.assertEqual(self.scalar('SELECT count(*) FROM market.snapshots'),2)
        self.assertEqual(str(self.scalar('SELECT snapshot_id FROM market.current_snapshots')),second['snapshot_id'])

    def test_identity_review_packet_tracks_generation_and_preserves_decisions(self):
        import sqlite3
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.matching import bridge_buyers
        from market_intelligence.review_report import read_queue
        from market_intelligence.review import decide
        first=import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        source=self.root/'buyers.db'
        with closing(sqlite3.connect(source)) as c,c:
            c.execute('CREATE TABLE contracts(buyer,buyer_abn)')
            c.execute('INSERT INTO contracts VALUES(?,?)',('Example Federal Agency',ABN))
        generation=bridge_buyers(self.engine,source)
        packet=read_queue(self.engine)
        self.assertEqual(str(packet['proposal_generation']['id']),generation['run_id'])
        self.assertEqual(str(packet['register_sources'][0]['id']),first['snapshot_id'])
        self.assertEqual(packet['rows'][0]['buyer_abn'],ABN)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.review_events'),0)
        key=packet['rows'][0]['buyer_key']
        decide(self.engine,key,'approved','gov:O-000001','reviewer','https://example.invalid/evidence')
        self.assertEqual(read_queue(self.engine)['rows'],[])
        import_snapshot(self.engine,manifest(self.root,[row(name='Updated Agency')],'2026-12-31'),'agor')
        packet=read_queue(self.engine)
        self.assertEqual(packet['rows'][0]['review_status'],'needs_recheck')
        self.assertTrue(packet['rows'][0]['register_context_stale'])
        self.assertEqual(self.scalar('SELECT count(*) FROM market.review_events'),1)

    def test_procurement_copy_idempotency_and_evidence_report(self):
        from datetime import date
        from market_intelligence.procurement import import_procurement
        from market_intelligence.analytics import build_report
        path=procurement_export(self.root)
        first=import_procurement(self.engine,path)
        second=import_procurement(self.engine,path)
        self.assertEqual(first['snapshot_id'],second['snapshot_id'])
        self.assertEqual(second['status'],'unchanged')
        self.assertEqual(self.scalar('SELECT count(*) FROM market.current_contracts'),1)
        result=build_report(self.engine,date(2026,11,19))
        self.assertEqual(len(result['buyers']),1)
        buyer=result['buyers'][0]
        self.assertEqual(buyer['awarded_value_365d'],100000)
        self.assertEqual(buyer['full_contract_value_ending_365d'],120000)
        self.assertEqual(buyer['invitation_history'],'unknown; public awards do not establish invitations')
        self.assertIn('not payments',result['definitions']['awarded_value_365d'])

    def test_procurement_copy_failure_preserves_previous_snapshot(self):
        import sqlite3
        from market_intelligence.procurement import import_procurement
        good=procurement_export(self.root)
        first=import_procurement(self.engine,good)
        bad=self.root/'bad.db';bad.write_bytes(good.read_bytes())
        with closing(sqlite3.connect(bad)) as c,c:c.execute("UPDATE contracts SET cn_id='not-a-contract'")
        with self.assertRaises(ValueError):import_procurement(self.engine,bad)
        self.assertEqual(str(self.scalar('SELECT snapshot_id FROM market.current_procurement_snapshot')),first['snapshot_id'])
        self.assertEqual(self.scalar('SELECT count(*) FROM market.procurement_snapshots'),1)
        self.assertEqual(self.scalar("SELECT count(*) FROM market.pipeline_runs WHERE source='procurement' AND status='failed'"),1)

    def test_legacy_amendments_retained_but_not_counted_as_awards(self):
        import sqlite3
        from datetime import date
        from market_intelligence.procurement import import_procurement
        from market_intelligence.analytics import build_report
        path=procurement_export(self.root)
        with closing(sqlite3.connect(path)) as c,c:
            c.execute("INSERT INTO contracts SELECT 'CN1-A1',"+','.join(CONTRACT_FIELDS[1:])+" FROM contracts")
            meta=json.loads(c.execute('SELECT payload FROM export_metadata').fetchone()[0]);meta['sources']['contract_api']['rows']=2
            c.execute('UPDATE export_metadata SET payload=?',(json.dumps(meta),))
        import_procurement(self.engine,path)
        result=build_report(self.engine,date(2026,11,19))
        self.assertEqual(self.scalar('SELECT count(*) FROM market.current_contracts'),2)
        self.assertEqual(result['excluded_legacy_amendment_rows'],1)
        self.assertEqual(result['buyers'][0]['recent_awards_count'],1)

    def test_privacy_audit_detects_test_canary(self):
        from sqlalchemy import text
        from market_intelligence.ingest import import_snapshot
        from market_intelligence.quality import report
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        with self.engine.begin() as c:
            c.execute(text("UPDATE market.observations SET payload=payload||'{\"email\":\"canary@example.invalid\"}'::jsonb"))
        result=report(self.engine)
        self.assertEqual(result['privacy']['email_patterns_in_register_payloads'],1)
        self.assertEqual(result['privacy']['contact_fields_in_register_payloads'],1)

    def test_expiry_only_buyer_retains_panel_and_supplier_evidence(self):
        import sqlite3
        from datetime import date
        from market_intelligence.procurement import import_procurement
        from market_intelligence.analytics import build_report
        path=procurement_export(self.root)
        with closing(sqlite3.connect(path)) as c,c:
            c.execute("UPDATE contracts SET first_published='2020-01-01'")
            c.execute("INSERT INTO contract_panel_links VALUES('CN1','SON1','https://example.invalid/cn','2026-11-19')")
            c.execute("INSERT INTO panels VALUES('SON1','Example Panel','Example Agency','ICT','1-Jul-2024 to 30-Jun-2027','https://example.invalid/son','2026-11-19')")
        import_procurement(self.engine,path)
        buyer=build_report(self.engine,date(2026,11,19))['buyers'][0]
        self.assertEqual(buyer['recent_awards_count'],0)
        self.assertEqual(buyer['awarded_value_365d'],0)
        self.assertEqual(buyer['expiry_count_365d'],1)
        self.assertEqual(len(buyer['known_panel_routes']),1)
        self.assertEqual(len(buyer['top_suppliers_ending_contracts']),1)
        self.assertEqual(len(buyer['ending_contract_evidence']),1)

    def test_directory_unit_hierarchy_is_cycle_safe(self):
        from market_intelligence.ingest import import_snapshot
        def item(pid,content,kind,parent_tag=None,parent=None):
            return '<item><unique_record_id>'+pid+'</unique_record_id><content_id>'+content+'</content_id><title>'+pid+'</title><type>'+kind+'</type>'+('' if parent is None else '<'+parent_tag+'>'+parent+'</'+parent_tag+'>')+'</item>'
        xml='<response>'+item('O-000001','100','organisation')+item('O-000002','200','directory_sub_structure','parent_organisation','100')+item('O-000003','300','directory_sub_structure','parent_directory_structure','200')+item('O-000004','400','directory_sub_structure','parent_directory_structure','500')+item('O-000005','500','directory_sub_structure','parent_directory_structure','400')+'</response>'
        import_snapshot(self.engine,directory_manifest(self.root,xml),'government_directory')
        self.assertEqual(self.scalar('SELECT count(*) FROM market.agency_unit_map'),2)
        self.assertEqual(self.scalar('SELECT max(depth) FROM market.agency_unit_map'),2)

    def test_disagreement_between_register_sources_requires_review(self):
        from sqlalchemy import text
        from market_intelligence.ingest import import_snapshot
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        xml='<response><item><unique_record_id>O-000001</unique_record_id><content_id>1</content_id><title>A Different Published Name</title><type>organisation</type><abn>'+ABN+'</abn></item></response>'
        import_snapshot(self.engine,directory_manifest(self.root,xml),'government_directory')
        self.assertEqual(self.scalar('SELECT count(*) FROM market.register_identity_conflicts'),1)
        with self.engine.connect() as c:
            organisations=[dict(r) for r in c.execute(text('SELECT o.*,true AS register_conflict FROM market.organisations o')).mappings()]
        self.assertEqual(propose('A Different Published Name',ABN,organisations)['match_status'],'conflict')

    def test_migration_refuses_to_discard_match_history(self):
        from alembic import command
        from alembic.config import Config
        from market_intelligence.db import migrate
        cfg=Config();cfg.set_main_option('script_location',str(Path(__file__).parents[1]/'market_intelligence/migrations'))
        from market_intelligence.ingest import import_snapshot
        import_snapshot(self.engine,manifest(self.root,[row()]),'agor')
        with self.assertRaises(RuntimeError):
            with self.engine.begin() as c:
                cfg.attributes['connection']=c;command.downgrade(cfg,'base')
        migrate(self.engine)
        self.assertEqual(self.scalar('SELECT count(*) FROM market.organisations'),1)


if __name__=='__main__':unittest.main()

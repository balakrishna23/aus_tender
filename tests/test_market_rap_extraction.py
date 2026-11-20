from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock,MagicMock

from market_intelligence.rap_extract import FIELDS,VERSION,validate_fields,configuration,redact,extract,review,run_prepared
from market_intelligence.rap_extract import prepare_library,validate_package,privacy_payload,digest,privacy_check,request_bound,public_url
from market_intelligence.ingest import file_hash
from market_intelligence.parsers import SourceError


def fields():
    output={k:dict(value=None,quote=None,page=None,confidence=0) for k in FIELDS}
    output['has_procurement_target']=dict(value=True,quote='We will increase Indigenous supplier spend',page=3,confidence=.9)
    return output


REVIEWER='00000000-0000-4000-8000-000000000001'
BODY='We will increase Indigenous supplier spend'


def make_package(root,body=BODY):
    from reportlab.pdfgen import canvas
    root=Path(root);pdf=root/'retained.pdf';c=canvas.Canvas(str(pdf))
    c.drawString(30,700,'Fixture Person, CEO. 12 Example Street.');c.showPage()
    c.drawString(30,700,'Fixture signature');c.showPage()
    c.drawString(30,700,body);c.save()
    package=dict(organisation='Fictional Company',document_sha256=file_hash(pdf),source_url='https://example.invalid/rap.pdf',
        licence='Original copyright retained; internal research only',pages={'3':body},prompt_version=VERSION,
        retained_pdf=str(pdf),page_count=3,truncation_possible=False,prepared_at='2026-11-20T00:00:00+00:00')
    package['privacy_review']=dict(reviewer_id=REVIEWER,contains_personal_data=False,payload_sha256=digest(privacy_payload(package)))
    path=root/'package.json';path.write_text(json.dumps(package),encoding='utf-8')
    return path,package


def config():
    return dict(endpoint='https://example.invalid',model='fixture',api_key='fixture',INPUT_USD_PER_MILLION=Decimal('1'),
                OUTPUT_USD_PER_MILLION=Decimal('2'),MAX_DOCUMENT_USD=Decimal('1'),MAX_BATCH_USD=Decimal('2'))


class MemoryEngine:
    """Transaction stub to inspect parameters and exercise provider flow without PostgreSQL or network."""
    def __init__(self):self.runs=[];self.writes=[]
    def begin(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params):
        sql=str(sql);result=Mock()
        if sql.startswith('SELECT id,status'):
            old=next((r for r in self.runs if all(r[k]==params[k] for k in ['sha','pages_sha','provider','model','config'])),None)
            result.mappings.return_value.first.return_value=old
        elif sql.startswith('INSERT INTO market.rap_extraction_runs'):
            self.runs.append(dict(params,status='running'));self.writes.append(dict(params))
        elif sql.startswith('UPDATE market.rap_extraction_runs'):
            self.runs[-1].update(params);self.writes.append(dict(params))
        return result


class RapExtractionTests(unittest.TestCase):
    def setUp(self):
        self.network=patch('urllib.request.urlopen',side_effect=AssertionError('Network forbidden in RAP tests'))
        self.network.start();self.addCleanup(self.network.stop)
        accounts=patch.dict(os.environ,{'ILLOURA_RAP_REVIEWER_IDS':REVIEWER});accounts.start();self.addCleanup(accounts.stop)

    def test_quote_must_occur_on_the_cited_page(self):
        pages={'3':'Our plan: We will increase Indigenous supplier spend by 2028.'}
        self.assertTrue(validate_fields(fields(),pages)['has_procurement_target']['value'])
        bad=fields();bad['has_procurement_target']['page']=4
        with self.assertRaises(SourceError):validate_fields(bad,pages)
        bad=fields();bad['has_procurement_target']['quote']='A target invented by the model'
        with self.assertRaises(SourceError):validate_fields(bad,pages)

    def test_unknowns_confidence_and_types_are_strict(self):
        pages={'3':'We will increase Indigenous supplier spend'}
        bad=fields();bad['target_value']['confidence']=.7
        with self.assertRaises(SourceError):validate_fields(bad,pages)
        bad=fields();bad['has_procurement_target']['value']='yes'
        with self.assertRaises(SourceError):validate_fields(bad,pages)
        bad=fields();bad['has_procurement_target']['confidence']=float('nan')
        with self.assertRaises(SourceError):validate_fields(bad,pages)

    def test_configuration_never_assumes_model_prices_or_credentials(self):
        with self.assertRaises(ValueError):configuration({})
        data={'ILLOURA_RAP_LLM_'+k:v for k,v in dict(ENDPOINT='https://example.invalid/v1/chat/completions',MODEL='fixture',API_KEY='fixture-only',
            INPUT_USD_PER_MILLION='1',OUTPUT_USD_PER_MILLION='2',MAX_DOCUMENT_USD='.25',MAX_BATCH_USD='1').items()}
        self.assertEqual(configuration(data)['MAX_DOCUMENT_USD'],Decimal('.25'))
        data['ILLOURA_RAP_LLM_ENDPOINT']='https://example.invalid/?key=credential'
        with self.assertRaises(ValueError):configuration(data)

    def test_contact_text_is_not_sent(self):
        value=redact('Procurement office person@example.invalid phone 0412 345 678')
        self.assertNotIn('person@example.invalid',value);self.assertNotIn('0412',value)

    def test_personal_data_and_token_urls_are_rejected(self):
        for value in ['12 Example Street','PO Box 42','ABN 12 345 678 901','12 345 678 901',
                      'https://example.invalid/?token=fixture','person@example.invalid','0412 345 678']:
            with self.subTest(value=value),self.assertRaises(SourceError):privacy_check(value)
        for url in ['https://example.invalid/?token=fixture','https://example.invalid/#secret']:
            with self.assertRaises(ValueError):public_url(url)

    def test_old_packages_and_unreviewed_names_never_reach_provider(self):
        provider=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path,package=make_package(tmp,'Fixture Person is our procurement champion')
            package.pop('privacy_review');path.write_text(json.dumps(package))
            with self.assertRaises(SourceError):extract(None,path,config(),provider)
            package['prompt_version']='rap-procurement-1';path.write_text(json.dumps(package))
            with self.assertRaises(SourceError):extract(None,path,config(),provider)
        provider.assert_not_called()

    def test_clearance_binds_text_organisation_url_and_copyright(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,package=make_package(tmp)
            self.assertEqual(validate_package(package),{'3':BODY})
            for key,value in [('pages',{'3':'Fixture Person is the CEO'}),('organisation','Changed Company'),
                              ('source_url','https://example.invalid/different.pdf'),('licence','Changed copyright')]:
                changed=dict(package,**{key:value})
                with self.subTest(key=key),self.assertRaises(SourceError):validate_package(changed)

    def test_extra_package_or_clearance_text_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            _,package=make_package(tmp)
            with self.assertRaises(SourceError):validate_package(dict(package,raw_text='Fixture Person'))
            package['privacy_review']['notes']='Fixture Person'
            with self.assertRaises(SourceError):validate_package(package)

    def test_retained_pdf_change_and_fabricated_page_are_refused(self):
        provider=Mock()
        with tempfile.TemporaryDirectory() as tmp:
            path,package=make_package(tmp)
            package['pages']={'3':'A sentence that does not occur on the page'}
            package['privacy_review']['payload_sha256']=digest(privacy_payload(package))
            with self.assertRaises(SourceError):validate_package(package)
            Path(package['retained_pdf']).write_bytes(b'changed PDF')
            with self.assertRaises(SourceError):extract(None,path,config(),provider)
        provider.assert_not_called()

    def test_prepare_does_not_write_unreviewed_pages_or_extra_text(self):
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);_,package=make_package(root)
            for filename,columns,row in [
                ('downloads.csv',['url','file','sha256','status'],[package['source_url'],'retained.pdf',package['document_sha256'],'downloaded']),
                ('directory.csv',['rap_url','organisation'],[package['source_url'],package['organisation']])]:
                with (root/filename).open('w',newline='') as stream:
                    writer=csv.writer(stream);writer.writerow(columns);writer.writerow(row)
            with self.assertRaises(SourceError):prepare_library(root,root/'no-review')
            self.assertFalse((root/'no-review').exists())
            selection={k:package[k] for k in ['pages','licence','privacy_review']}
            privacy=root/'privacy.json';privacy.write_text(json.dumps({package['document_sha256']:selection}))
            result=prepare_library(root,root/'prepared',privacy_review_path=privacy)
            self.assertEqual(result['documents'],1)
            saved=(root/'prepared'/result['packages'][0]['file']).read_text()
            for forbidden in ['Fixture Person','Fixture signature','12 Example Street']:self.assertNotIn(forbidden,saved)
            selection['raw_response']='Fixture Person';privacy.write_text(json.dumps({package['document_sha256']:selection}))
            result=prepare_library(root,root/'refused',privacy_review_path=privacy)
            self.assertEqual(result['documents'],0)

    def test_failed_and_uncertain_requests_are_not_repeated(self):
        for response in [TimeoutError('Fixture Person secret error'),({},'Fixture Person'),
                         ({'usage':{'prompt_tokens':True,'completion_tokens':1}},'fixture')]:
            with self.subTest(response_type=type(response).__name__),tempfile.TemporaryDirectory() as tmp:
                path,_=make_package(tmp);engine=MemoryEngine()
                provider=Mock(side_effect=response) if isinstance(response,Exception) else Mock(return_value=response)
                first=extract(engine,path,config(),provider)
                again=extract(engine,path,dict(config(),MAX_DOCUMENT_USD=Decimal('2')),provider)
                self.assertEqual(first['status'],'failed');self.assertEqual(first['cost_status'],'unknown')
                self.assertIsNone(first['cost_usd']);self.assertFalse(again['new_request']);provider.assert_called_once()
                self.assertNotIn('Fixture Person',json.dumps(engine.writes,default=str))
                self.assertNotIn('secret error',json.dumps(engine.writes,default=str))

    def test_success_and_invalid_response_keep_only_validated_fields_and_usage(self):
        for content,expected in [(json.dumps(fields()),'needs_review'),('Fixture Person invalid JSON','failed')]:
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as tmp:
                path,package=make_package(tmp);engine=MemoryEngine()
                provider=Mock(return_value=({'usage':{'prompt_tokens':100,'completion_tokens':50},
                    'choices':[{'message':{'content':content}}]},'Fixture Person header'))
                result=extract(engine,path,config(),provider)
                self.assertEqual(result['status'],expected);self.assertEqual(result['cost_usd'],Decimal('.0002'))
                self.assertIsNone(engine.writes[-1]['request_id'])
                self.assertNotIn('Fixture Person',json.dumps(engine.writes,default=str))
                provenance=json.loads(engine.writes[0]['provenance'])
                self.assertEqual(provenance['package_sha256'],file_hash(path))
                self.assertEqual(provenance['selected_pages_sha256'],digest(package['pages']))
                self.assertEqual(provider.call_args.args[1],{'3':BODY})

    def test_request_bound_applies_before_mocked_provider_and_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,_=make_package(tmp);provider=Mock();cfg=dict(config(),MAX_DOCUMENT_USD=Decimal('.000001'))
            with self.assertRaises(ValueError):extract(None,path,cfg,provider)
            provider.assert_not_called()

    def test_unknown_usage_stops_batch_and_reserves_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,package=make_package(tmp)
            item=dict(file=path.name,document_sha256=package['document_sha256'],package_sha256=file_hash(path),pages=1)
            (root/'manifest.json').write_text(json.dumps(dict(status='prepared',packages=[item,item])))
            with patch('market_intelligence.rap_extract.invoke',side_effect=TimeoutError('private')) as provider:
                result=run_prepared(MemoryEngine(),root,config())
            provider.assert_called_once();self.assertEqual(result['unknown_new_calls'],1)
            self.assertGreater(result['reserved_new_cost_usd'],0);self.assertEqual(result['status'],'failed')

    def test_batch_preflights_all_manifest_document_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,package=make_package(tmp)
            item=dict(file=path.name,document_sha256=package['document_sha256'],package_sha256=file_hash(path),pages=1)
            (root/'manifest.json').write_text(json.dumps(dict(status='prepared',packages=[item,dict(item,document_sha256='0'*64)])))
            with patch('market_intelligence.rap_extract.invoke') as provider,self.assertRaises(SourceError):run_prepared(None,root,config())
            provider.assert_not_called()

    def test_review_never_accepts_a_person_name_or_free_text_notes(self):
        with self.assertRaises(SourceError):review(None,'fixture','approved','Fixture Person','a'*64)
        with self.assertRaises(SourceError):review(None,'fixture','approved',REVIEWER,'a'*64,'Fixture Person signed')
        with self.assertRaises(SourceError):review(None,'fixture','approved','00000000-0000-4000-8000-000000000099','a'*64)

    def test_review_exact_document_and_failed_status_are_enforced(self):
        engine=MagicMock();conn=engine.begin.return_value.__enter__.return_value
        for status,sha in [('failed','a'*64),('needs_review','b'*64)]:
            conn.execute.return_value.mappings.return_value.one.return_value=dict(
                id=REVIEWER,status=status,document_sha256=sha,prompt_version=VERSION)
            with self.assertRaises(ValueError):review(engine,REVIEWER,'approved',REVIEWER,'a'*64)
            self.assertEqual(conn.execute.call_count,1);conn.reset_mock()

    def test_document_change_during_call_retains_cost_and_blocks_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path,package=make_package(tmp);engine=MemoryEngine()
            def provider(cfg,pages):
                Path(package['retained_pdf']).write_bytes(b'changed')
                return {'usage':{'prompt_tokens':100,'completion_tokens':50},
                        'choices':[{'message':{'content':json.dumps(fields())}}]},'fixture'
            result=extract(engine,path,config(),provider)
            self.assertEqual(result['status'],'failed');self.assertEqual(result['cost_status'],'known')
            self.assertEqual(result['cost_usd'],Decimal('.0002'));self.assertIsNone(engine.writes[-1]['fields'])

    def test_batch_stops_at_previous_running_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,package=make_package(tmp)
            item=dict(file=path.name,document_sha256=package['document_sha256'],package_sha256=file_hash(path),pages=1)
            (root/'manifest.json').write_text(json.dumps(dict(status='prepared',packages=[item,item])))
            with patch('market_intelligence.rap_extract.extract',return_value=dict(status='running',new_request=False)) as call:
                result=run_prepared(None,root,config())
            call.assert_called_once();self.assertEqual(result['status'],'failed')

    def test_batch_reserves_bound_instead_of_lower_reported_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,package=make_package(tmp)
            item=dict(file=path.name,document_sha256=package['document_sha256'],package_sha256=file_hash(path),pages=1)
            (root/'manifest.json').write_text(json.dumps(dict(status='prepared',packages=[item,item])))
            result=dict(status='needs_review',new_request=True,cost_status='known',cost_usd=Decimal('.01'),conservative_cost_usd=Decimal('.3'))
            with patch('market_intelligence.rap_extract.extract',side_effect=[result,dict(result,conservative_cost_usd=Decimal('.2'))]) as call:
                report=run_prepared(None,root,dict(config(),MAX_BATCH_USD=Decimal('.5')))
            self.assertEqual(call.call_args_list[1].args[2]['MAX_DOCUMENT_USD'],Decimal('.2'))
            self.assertEqual(report['known_new_cost_usd'],Decimal('.02'));self.assertEqual(report['reserved_new_cost_usd'],Decimal('.5'))

    def test_legacy_rap_research_requires_privacy_clearance_before_database(self):
        from market_intelligence.research import import_research
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'research.json'
            row=dict(record_key='one',organisation='Fixture Person',rap_type='Reflect',period_as_published='2026-2028',
                document_url='https://example.invalid/rap.pdf',source_url='https://example.invalid/rap.pdf',
                document_sha256='a'*64,collection_date='2026-11-20',licence='Original copyright')
            path.write_text(json.dumps(dict(source='rap_directory',contains_contact_data=False,records=[row],
                observed_at='2026-11-19T00:00:00+00:00',provenance={})))
            with self.assertRaises(SourceError):import_research(None,path)

    def test_full_dates_and_membership_are_distinct(self):
        body='We will join Supply Nation as a member by 2028-12-31'
        data={k:dict(value=None,quote=None,page=None,confidence=0) for k in FIELDS}
        data['supply_nation_member_commitment']=dict(value='yes',quote=body,page=1,confidence=.9)
        self.assertIsNone(validate_fields(data,{'1':body})['requires_supply_nation_certified']['value'])
        data['target_deadline']=dict(value='20281231',quote=body,page=1,confidence=.9)
        with self.assertRaises(SourceError):validate_fields(data,{'1':body})

    def test_batch_caps_remaining_budget_and_stops_on_unknown_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);items=[]
            for i in range(3):
                sub=folder/str(i);sub.mkdir();path,package=make_package(sub);items.append(dict(file=str(path.relative_to(folder)),package_sha256=file_hash(path),document_sha256=package['document_sha256'],pages=1))
            (folder/'manifest.json').write_text(json.dumps(dict(status='prepared',packages=items)))
            results=[dict(status='needs_review',new_request=True,cost_status='known',cost_usd=Decimal('.4'),conservative_cost_usd=Decimal('.4')),
                     dict(status='failed',new_request=True,cost_status='unknown',cost_usd=None,conservative_cost_usd=Decimal('.1'))]
            with patch('market_intelligence.rap_extract.extract',side_effect=results) as call:
                result=run_prepared(None,folder,dict(MAX_BATCH_USD=Decimal('.5'),MAX_DOCUMENT_USD=Decimal('.4')))
            self.assertEqual(call.call_count,2);self.assertEqual(call.call_args_list[1].args[2]['MAX_DOCUMENT_USD'],Decimal('.1'))
            self.assertEqual(result['known_new_cost_usd'],Decimal('.4'));self.assertEqual(result['status'],'failed')


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL database required')
class RapExtractionDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=patch('urllib.request.urlopen',side_effect=AssertionError('Network forbidden in RAP tests'));cls.network.start()
        cls.addClassCleanup(cls.network.stop)
        accounts=patch.dict(os.environ,{'ILLOURA_RAP_REVIEWER_IDS':REVIEWER});accounts.start();cls.addClassCleanup(accounts.stop)
        from market_intelligence.db import engine,migrate
        cls.engine=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database!='illoura_market_test':raise RuntimeError('Dedicated test database required')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):cls.engine.dispose()

    def test_usage_idempotence_and_review(self):
        from sqlalchemy import text
        with self.engine.begin() as c:c.execute(text('TRUNCATE market.rap_extraction_reviews,market.rap_extraction_runs'))
        calls=[]
        def provider(config,pages):
            calls.append(True)
            return {'usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[{'message':{'content':json.dumps(fields())}}]},'fixture-request'
        cfg=dict(endpoint='https://example.invalid',model='fixture',api_key='not-real',INPUT_USD_PER_MILLION=Decimal('1'),OUTPUT_USD_PER_MILLION=Decimal('2'),MAX_DOCUMENT_USD=Decimal('1'))
        with tempfile.TemporaryDirectory() as tmp:
            path,package=make_package(tmp)
            first=extract(self.engine,path,cfg,provider)
            again=extract(self.engine,path,dict(cfg,MAX_DOCUMENT_USD=Decimal('2'),INPUT_USD_PER_MILLION=Decimal('3')),provider)
        self.assertEqual(first['status'],'needs_review');self.assertEqual(first['cost_usd'],Decimal('.0002'))
        self.assertFalse(again['new_request']);self.assertEqual(len(calls),1)
        # A status flag alone is insufficient: the view requires matching review history.
        with self.engine.begin() as c:
            c.execute(text("UPDATE market.rap_extraction_runs SET status='approved' WHERE id=:id"),{'id':first['run_id']})
            self.assertEqual(c.execute(text('SELECT count(*) FROM market.current_approved_rap_extractions')).scalar(),0)
        review(self.engine,first['run_id'],'approved',REVIEWER,package['document_sha256'])
        with self.engine.connect() as c:self.assertEqual(c.execute(text('SELECT count(*) FROM market.current_approved_rap_extractions')).scalar(),1)
        with self.assertRaises(ValueError):review(self.engine,first['run_id'],'approved',REVIEWER,'b'*64)
        review(self.engine,first['run_id'],'rejected',REVIEWER,package['document_sha256'])
        with self.engine.connect() as c:self.assertEqual(c.execute(text('SELECT count(*) FROM market.current_approved_rap_extractions')).scalar(),0)

    def test_invalid_response_still_records_usage_and_never_retries(self):
        from sqlalchemy import text
        with self.engine.begin() as c:c.execute(text('TRUNCATE market.rap_extraction_reviews,market.rap_extraction_runs'))
        calls=[]
        def provider(config,pages):
            calls.append(True)
            return {'usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[{'message':{'content':'invalid JSON'}}]},'fixture'
        cfg=dict(endpoint='https://example.invalid',model='fixture',api_key='fixture',INPUT_USD_PER_MILLION=Decimal('1'),OUTPUT_USD_PER_MILLION=Decimal('2'),MAX_DOCUMENT_USD=Decimal('1'))
        with tempfile.TemporaryDirectory() as tmp:
            path,package=make_package(tmp)
            result=extract(self.engine,path,cfg,provider);again=extract(self.engine,path,cfg,provider)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['cost_status'],'known');self.assertEqual(result['cost_usd'],Decimal('.0002'))
        self.assertFalse(again['new_request']);self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()

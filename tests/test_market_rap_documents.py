"""Offline RAP document rules. Source excerpts are deliberately only a few lines."""
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from market_intelligence import rap_documents as R

AECOM_SPEND = ('Allocate at least 2% of AECOM Australia spend to be with First Nations businesses '
               '(in line with our Procurement Strategy). September 2026, 2027, 2028 Regional Procurement Operations Lead, ANZ')
AECOM_BUSINESSES = ('Maintain commercial relationships with 40 Aboriginal and/or Torres Strait Islander businesses. '
                    'September 2026, 2027, 2028 Regional Procurement Operations Lead, ANZ')
AECOM_EMPLOYMENT = ('Work towards a target of 1% of total Australian staff base to be First Nations people. '
                    'December 2026, 2027, 2028')
DELOITTE_EMPLOYMENT = ('\u2022 Increase First Nations peoples FTE employment from 0.03% (3) in baseline year 2020, '
                       'to a more representative level of at least 0.3% (30).\nMay 2027 Director, Talent')
NIAA = 'Develop internal protocols for handling\ncommunity data requests.\n1st Quarter\n2027 - ongoing\nGrants & Finance Group'


def directory(**kwargs):
    return dict(dict(record_key='fixture-rap',organisation='AECOM Australia',rap_type='Stretch',
                     period_as_published='March 2026 to March 2029'),**kwargs)


def page(body):
    return 'AECOM Australia\nStretch Reconciliation Action Plan\nMarch 2026 - March 2029\nAction Deliverable Timeline Responsibility\n'+body


class Rules(unittest.TestCase):
    def analyse(self, body):
        return R.analyse([page(body)],directory())

    def test_aecom_targets_timelines_responsibilities(self):
        findings = self.analyse('\n'.join([AECOM_SPEND,AECOM_BUSINESSES,AECOM_EMPLOYMENT]))
        self.assertEqual(findings['match_status'],'matched')
        spend, suppliers, employment = findings['commitments']
        self.assertEqual((spend['category'],spend['target_kind'],spend['target_value']),('procurement','percent',Decimal(2)))
        self.assertEqual(spend['timeline'],'September 2026, 2027, 2028')
        self.assertEqual(spend['responsibility'],'Regional Procurement Operations Lead, ANZ')
        self.assertEqual((suppliers['category'],suppliers['target_kind'],suppliers['target_value']),('supplier_relationships','count',Decimal(40)))
        self.assertEqual((employment['category'],employment['target_value']),('employment',Decimal(1)))
        self.assertTrue(all(c['quote']==c['deliverable'] and c['page']==1 for c in findings['commitments']))

    def test_wrapped_bullets_future_target_and_count(self):
        rows=self.analyse(DELOITTE_EMPLOYMENT)['commitments']
        self.assertEqual([(r['target_kind'],r['target_value']) for r in rows],[('percent',Decimal('0.3')),('count',Decimal(30))])
        self.assertTrue(all(r['category']=='employment' and r['responsibility']=='Director, Talent' for r in rows))
        wrapped=AECOM_SPEND.replace('businesses (','businesses\n(')
        self.assertEqual(self.analyse(wrapped)['commitments'][0]['deliverable'],self.analyse(AECOM_SPEND)['commitments'][0]['deliverable'])

    def test_membership_is_not_certification_and_cultural_training_not_employment(self):
        rows=self.analyse('Investigate Supply Nation membership. October 2026 Regional Procurement Lead\n'
                          'Ensure 95% of all staff complete cultural learning. December 2026 Director, Talent')['commitments']
        self.assertEqual(rows[0]['category'],'supply_nation')
        self.assertEqual(rows[1]['category'],'other')

    def test_money_and_counts(self):
        for money,expected in [('$2 million',2000000),('$1.5m',1500000),('$500,000',500000),('$2bn',2000000000),('$50k',50000)]:
            self.assertEqual(R.targets('Allocate '+money+' procurement spend.')[0],('money',Decimal(expected),'AUD'))
        self.assertEqual(R.targets('Recruit 20 First Nations employees.')[0],('count',Decimal(20),'employees'))

    def test_numbered_items_role_before_abbreviated_timeline(self):
        rows=self.analyse('1. Establish and\nmaintain relationships.\n1.1. Develop and implement an engagement guide for external partners.\n'
                          'Manager, Office of the\nExecutive Director\nOct 2026\n'
                          '1.2. Aboriginal partners to guide one formal partnership each year.\nExecutive Director Apr 2027,\n2028, 2029')['commitments']
        self.assertEqual([(r['timeline'],r['responsibility']) for r in rows],
                         [('Oct 2026','Manager, Office of the Executive Director'),('Apr 2027, 2028, 2029','Executive Director')])
        self.assertTrue(rows[0]['deliverable'].startswith('Develop and implement'))
        self.assertIsNone(R.TIMELINE.search('Market 2026 and Decision 2027'))

    def test_numbered_deliverable_cadence_and_stacked_dates(self):
        rows=self.analyse('1. Our community team to grow Indigenous supplier spend 10% year-on-year\nMonitor quarterly\nDecember 2025\n'
                          'March, June, September, December 2026\nMarch, June 2027\nGeneral Manager, Procurement')['commitments']
        r=rows[0]
        self.assertEqual(r['deliverable'],'Our community team to grow Indigenous supplier spend 10% year-on-year')
        self.assertEqual(r['timeline'],'Monitor quarterly December 2025 March, June, September, December 2026 March, June 2027')
        self.assertEqual((r['responsibility'],r['category'],r['target_value']),('General Manager, Procurement','procurement',Decimal('10')))

    def test_column_separated_table_keeps_undated_deliverables(self):
        rows=R.commitments(['Action 1: Establish relationships with communities\nMay\n2023, 2024, 2025\nJuly\n2023, 2024, 2025\n'
                            'Engage with First Nations organisations to co-design programs.\nShare the engagement plan with all staff each year.'])
        self.assertEqual([(r['deliverable'][:6],r['timeline']) for r in rows],[('Engage',None),('Share ',None)])

    def test_type_beyond_cover_period_inner_page_states_and_bad_page(self):
        pages=['Commonwealth Example','Message from our CEO','Our Stretch Reconciliation Action Plan sets out','Our Stretch RAP',
               'This plan runs from July 2024 to June 2027']
        self.assertEqual(R.detect_type(pages),'Stretch')
        self.assertEqual(R.detect_period(pages),'July 2024 to June 2027')
        self.assertTrue(R.organisation_found('SA Department for Education','Government of South Australia Department for Education'))
        class Page:
            def __init__(self,text):self.text=text
            def extract_text(self):
                if self.text is None:raise RuntimeError('Reached maximum number of tokens per stream')
                return self.text
        with patch('pypdf.PdfReader',return_value=MagicMock(pages=[Page('One'),Page(None),Page('Three')])):
            self.assertEqual(R.page_texts('ignored.pdf'),['One','','Three'])

    def test_organisation_abbreviations_and_employment_wording(self):
        self.assertTrue(R.organisation_found('Brisbane South Primary Health Network (BSPHN)','Brisbane South PHN Reconciliation Action Plan'))
        self.assertTrue(R.organisation_found('Australian Federal Police (AFP)','AFP Stretch Reconciliation Action Plan'))
        self.assertFalse(R.organisation_found('Example Holdings','Another Company Stretch Reconciliation Action Plan'))
        self.assertEqual(R.category('Circulate NRW resources and reconciliation materials to all staff.'),'other')
        self.assertEqual(R.category('Provide internal communications on reconciliation.'),'other')
        self.assertEqual(R.category('Increase First Nations staff to 3% of our workforce.'),'employment')

    def test_minimum_plus_increment_thresholds_and_training(self):
        both=R.targets('Maintain a 7% minimum procurement spend with First Nations businesses, with an increase of 0.5% each year.')
        self.assertEqual([v for k,v,u in both if k=='percent'],[Decimal('7'),Decimal('0.5')])
        goal=R.targets('Increase First Nations staff in our workforce from 2.5% to 3.0% over the plan.')
        self.assertEqual([v for k,v,u in goal if k=='percent'],[Decimal('3.0')])
        for threshold in ('Include cultural requirements in contracts valued at $250,000 or more.',
                          'Double the number of Indigenous suppliers with whom we spend over $100,000 per annum.'):
            self.assertFalse([k for k,v,u in R.targets(threshold) if k=='money'])
        self.assertEqual(R.targets('Commit to a minimum spend of $750,000 each year.')[0][1],Decimal('750000'))
        self.assertEqual(R.category('Ensure 100% attendance by employees at the mandatory training workshop.'),'other')
        self.assertEqual(R.category('Increase employment through a traineeship training program.'),'employment')

    def test_unlabelled_contact_block_and_bracketed_phone(self):
        rows=self.analyse('Contact details\nRobin Example\nDirector for First Nations\nPartnerships\n(07) 0000 0000\nrap@example.invalid')['people']
        contact=[r for r in rows if r['context']=='contact'][0]
        self.assertEqual((contact['name'],contact['title'],contact['phone'],contact['email']),
                         ('Robin Example','Director for First Nations Partnerships','(07) 0000 0000','rap@example.invalid'))

    def test_contact_blocks_both_layouts_are_personal(self):
        for name,title in [('Robin Example','First Nations Engagement Lead'),('Chris Example','RAP Chair and Deloitte Partner')]:
            # Contact details are synthetic; no real mailboxes are committed.
            body='Contact details\nName: '+name+'\nPosition: '+title+'\nPhone: +61 2 0000 0000\nEmail: contact@example.invalid'
            for value in (body,body.replace(' ','\t')):
                rows=self.analyse(value)['people'];self.assertEqual(len(rows),1)
                r=rows[0]
                self.assertEqual((r['name'],r['title'],r['phone'],r['email']),(name,title,'+61 2 0000 0000','contact@example.invalid'))
                self.assertIs(r['is_personal'],True)

    def test_people_signatory_roles_and_group(self):
        body=('Jordan Example\nChief Executive O\ufb03  cer\nDeloitte Australia\n'
              'RAP Executive Sponsors Jordan Example (Chief Executive Officer)\n'
              'RAP Ambassador Casey Example (Chair, Deloitte Australia)\n'
              'RAP Working Group\nJane Example Director, Procurement\n'
              'RAP Champions\nAlex Example & Sam Example')
        rows=self.analyse(body)['people']
        self.assertEqual({r['context'] for r in rows},{'ceo','sponsor','ambassador','working_group','champion'})
        self.assertTrue(any(r['name']=='Jordan Example' and r['context']=='sponsor' for r in rows))
        self.assertTrue(all(r['is_personal'] for r in rows))
        ra=self.analyse('Taylor Example\nChief Executive Officer\nReconciliation Australia')['people']
        self.assertFalse(any(r['context']=='ceo' for r in ra))

    def test_generic_contact_and_niaa_quarter(self):
        rows=self.analyse('Enquiries: RAP-Team@example.invalid or call 02 0000 0000')['people']
        self.assertEqual((rows[0]['context'],rows[0]['phone']),('generic_contact','02 0000 0000'))
        row=self.analyse(NIAA)['commitments'][0]
        self.assertEqual((row['timeline'],row['responsibility']),('1st Quarter 2027 - ongoing','Grants & Finance Group'))

    def test_normalisation(self):
        self.assertEqual(R.normalise('Lead:\tFirst\tNations\ncertifi ed O\ufb03  cer workfl ow staff base'),
                         'Lead: First Nations\ncertified Officer workflow staff base')
        # A whole word ending in a ligature keeps its space; "off" joins only real continuations.
        self.assertEqual(R.normalise('staﬀ awareness and staﬀ  meetings, time off work, off ered'),
                         'staff awareness and staff meetings, time off work, offered')

    def test_type_period_and_match_status(self):
        result=self.analyse(AECOM_SPEND)
        self.assertEqual((result['rap_type_detected'],result['period_detected']),('Stretch','March 2026 - March 2029'))
        for changes,expected in [({'rap_type':'Reflect'},'type_mismatch'),({'organisation':'Unrelated Entity'},'organisation_not_found'),
                                  ({'period_as_published':'2020-2023'},'period_mismatch'),({'period_as_published':'2029-2031'},'matched')]:
            row=directory();row.update(changes)
            self.assertEqual(R.analyse([page(AECOM_SPEND)],row)['match_status'],expected)
        self.assertEqual(R.analyse([''],directory())['match_status'],'unreadable')
        self.assertEqual(R.analyse(['AECOM Australia Stretch RAP 2025-2028'],dict(directory(),period_as_published='2025-2028'))['match_status'],'matched')
        self.assertEqual(R.years('2026-29'),{2026,2027,2028,2029})


class IntegrationOffline(unittest.TestCase):
    def test_build_rap_fallback_and_legacy_precedence(self):
        from market_intelligence.research import build_rap
        fields={'has_procurement_target':{'value':True},'employment_target':{'value':True},
                'requires_supply_nation_certified':{'value':'unknown'}}
        record=dict(directory(),document_sha256='legacy-sha')
        finding=dict(record_key=record['record_key'],fields=fields,document_sha256='document-sha',source_url='https://example.invalid/rap.pdf')
        legacy=dict(id='approved-run',document_sha256='legacy-sha',fields={'employment_target':{'value':False}},
                    source_url='https://example.invalid/legacy.pdf',licence='Retained copyright',pages=[1],provenance={'method':'legacy'})
        for approved in ([],[legacy]):
            db=MagicMock();conn=db.connect.return_value.execution_options.return_value.__enter__.return_value
            def execute(statement,params=None):
                result=MagicMock();sql=str(statement)
                if 'current_research WHERE' in sql:
                    result.mappings.return_value=[dict(payload=record,snapshot_id='snapshot',provenance={})]
                elif 'current_approved_rap_extractions' in sql:
                    result.mappings.return_value=approved
                elif 'current_rap_document_findings' in sql:
                    result.mappings.return_value=[finding]
                else:
                    result.mappings.return_value=[];result.__iter__.return_value=iter([]);result.scalar.return_value=None
                return result
            conn.execute.side_effect=execute
            row=build_rap(db,date(2026,11,28))['targets'][0]
            self.assertEqual(row['approved_extraction'],legacy['fields'] if approved else fields)
            self.assertEqual(row['approved_extraction_provenance']['document_sha256'],'legacy-sha' if approved else 'document-sha')
            if not approved:
                self.assertEqual(row['approved_extraction_provenance']['source'],'rule_based_document_extraction')
                self.assertEqual(row['score_components']['approved_procurement_target'],10)

    def test_export_allowlist_and_report_files(self):
        import ast
        from deploy.illoura_research_export import TABLES
        tree=ast.parse(Path('api/app.py').read_text(encoding='utf-8'))
        allowed=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ALLOWED' for t in n.targets))
        for name in ('rap_document_findings','rap_commitments','rap_people'):
            self.assertIn(name,allowed['research']);self.assertIn('ORDER BY',TABLES[name]);self.assertIn('LIMIT',TABLES[name])
        db=MagicMock();conn=db.connect.return_value.__enter__.return_value
        conn.execute.return_value.mappings.return_value=[dict(record_key='one',organisation='Example',match_status='matched',commitments=2,people=1)]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(R.report(db,tmp)['documents'],1)
            self.assertEqual(json.loads((Path(tmp)/'rap_documents.json').read_text())[0]['people'],1)
            self.assertIn('commitments,people',(Path(tmp)/'rap_documents.csv').read_text())


class Response:
    def __init__(self, payload=b'', status=200, headers=None):
        self.payload=payload;self.status_code=status;self.headers=headers or {};self.closed=False

    def iter_content(self, size):
        for start in range(0,len(self.payload),size):
            yield self.payload[start:start+size]

    def close(self):
        self.closed=True


class Client:
    def __init__(self,routes):
        self.routes=routes;self.calls=[];self.now=0;self.delays=[]

    def get(self,url,**kwargs):
        self.calls.append((url,self.now,kwargs))
        value=self.routes[url]
        if isinstance(value,Exception):raise value
        payload,status,headers=value
        return Response(payload,status,headers)

    def sleep(self,seconds):
        self.delays.append(seconds);self.now+=seconds


class Browser(Client):
    """Stands in for the stealth BrowserClient: may land elsewhere (redirects are followed)."""
    def __init__(self,routes,landed=None):
        super().__init__(routes);self.landed=landed or {};self.closed=False

    def get(self,url,**kwargs):
        response=super().get(url,**kwargs);response.url=self.landed.get(url,url)
        return response

    def close(self):
        self.closed=True


class Fetch(unittest.TestCase):
    def run_fetch(self,urls,routes,root):
        client=Client(routes)
        result=R.fetch([dict(directory(),url=u,found_via='fixture') for u in urls],root,client,
                       clock=lambda:client.now,sleeper=client.sleep)
        return result,client

    def test_blocked_host_before_any_request(self):
        with tempfile.TemporaryDirectory() as root:
            result,client=self.run_fetch(['http://reconciliation.my.salesforce.com./a.pdf'],{},root)
        self.assertEqual(result['counts'],{'blocked_host':1});self.assertEqual(client.calls,[])

    def test_robots_rules_rfc9309_and_bot_walls(self):
        wall=b'<!doctype html><html><body>Performing security verification</body></html>'
        for robots,expected in [((b'User-agent: *\nDisallow: /',200,{}),'robots_disallowed'),
                                ((b'',503,{}),'robots_disallowed'),((b'',403,{}),'robots_disallowed'),
                                # No rules in the body (an error page served for /robots.txt): no restriction.
                                ((b'Error: unavailable',200,{}),'downloaded'),
                                ((b'<html>System unavailable</html>',200,{'Content-Type':'text/html'}),'downloaded'),
                                ((wall,200,{'Content-Type':'text/html'}),'robots_disallowed'),
                                # A real robots.txt mentioning "captcha" is rules, not a bot wall.
                                ((b'User-agent: *\nDisallow: /captcha/',200,{}),'downloaded'),
                                ((b'',404,{}),'downloaded'),((b'',410,{}),'downloaded'),
                                (OSError('offline'),'robots_disallowed')]:
            with tempfile.TemporaryDirectory() as root:
                result,client=self.run_fetch(['https://example.invalid/rap.pdf'],{
                    'https://example.invalid/robots.txt':robots,'https://example.invalid/rap.pdf':(b'%PDF fixture',200,{})},root)
            self.assertEqual(result['attempts'][0]['status'],expected)
            if expected=='robots_disallowed':self.assertEqual(len(client.calls),1)

    def test_stealth_browser_retries_only_refused_candidates(self):
        plain={'https://example.invalid/robots.txt':(b'',404,{}),
               'https://example.invalid/rap.pdf':(b'',403,{}),
               'https://example.invalid/gone.pdf':(b'',404,{}),
               'https://example.invalid/old.pdf':(b'',410,{}),
               'https://ruled.invalid/robots.txt':(b'User-agent: *\nDisallow: /',200,{}),
               'https://www.agency.wa.gov.au/robots.txt':(b'',404,{}),
               'https://www.agency.wa.gov.au/rap.pdf':(b'',403,{}),
               'https://walled.invalid/robots.txt':(b'',403,{})}
        browser=Browser({'https://example.invalid/robots.txt':(b'',404,{}),
                         'https://example.invalid/rap.pdf':(b'%PDF browser',200,{}),
                         'https://example.invalid/gone.pdf':(b'%PDF only for browsers',200,{}),
                         'https://walled.invalid/robots.txt':(b'User-agent: *\nDisallow: /private/',200,{}),
                         'https://walled.invalid/rap.pdf':(b'%PDF walled',200,{})})
        urls=['https://example.invalid/rap.pdf','https://example.invalid/gone.pdf','https://example.invalid/old.pdf',
              'https://ruled.invalid/rap.pdf','https://www.agency.wa.gov.au/rap.pdf','https://walled.invalid/rap.pdf','']
        with tempfile.TemporaryDirectory() as root:
            client=Client(plain)
            result=R.fetch([dict(directory(),url=u) for u in urls],root,client,
                           clock=lambda:client.now,sleeper=client.sleep,browser=browser)
        # 410, a robots.txt rule, the WA government host and the empty row are never retried; a 404 to bots is.
        self.assertEqual(result['browser_retries'],3);self.assertTrue(browser.closed)
        self.assertNotIn('https://www.agency.wa.gov.au/rap.pdf',[c[0] for c in browser.calls])
        retried=[r for r in result['attempts'] if r['found_via']=='stealth_browser']
        self.assertEqual([(r['url'],r['status']) for r in retried],
                         [('https://example.invalid/rap.pdf','downloaded'),('https://example.invalid/gone.pdf','downloaded'),
                          ('https://walled.invalid/rap.pdf','downloaded')])
        walled=[r for r in result['attempts'] if r['url']=='https://walled.invalid/rap.pdf'][0]
        self.assertEqual((walled['status'],walled['detail']),('robots_disallowed','robots_unavailable'))
        self.assertFalse(R.stealth_allowed('https://data.gov.au/x.pdf'))
        self.assertFalse(R.stealth_allowed('https://www.publications.qld.gov.au/x.pdf'))

    def test_browser_landing_place_gets_the_same_checks(self):
        routes={'https://example.invalid/robots.txt':(b'User-agent: *\nDisallow: /private/',200,{}),
                'https://example.invalid/rap':(b'%PDF x',200,{}),'https://example.invalid/plan':(b'%PDF y',200,{})}
        browser=Browser(routes,{'https://example.invalid/rap':'https://elsewhere.invalid/rap.pdf',
                                'https://example.invalid/plan':'https://example.invalid/private/plan.pdf'})
        with tempfile.TemporaryDirectory() as root:
            result=R.fetch([dict(directory(),url='https://example.invalid/rap'),dict(directory(),url='https://example.invalid/plan')],
                           root,browser,clock=lambda:browser.now,sleeper=browser.sleep)
        self.assertEqual([(r['status'],r['detail']) for r in result['attempts']],
                         [('error','cross_site_redirect'),('robots_disallowed','robots_rule')])

    def test_migration_sql_has_no_accidental_bind_parameters(self):
        # op.execute() goes through text(): ':name' (e.g. a regex '(?:19') becomes a bind parameter.
        import importlib.util
        from sqlalchemy import text
        path=Path(R.__file__).parent/'migrations'/'versions'/'026_rap_documents.py'
        spec=importlib.util.spec_from_file_location('m026',path);module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module);captured=[]
        with patch.object(module.op,'execute',side_effect=captured.append):
            module.upgrade()
        self.assertTrue(captured)
        for sql in captured:
            self.assertEqual(text(sql).compile().params,{})

    @unittest.skipIf(os.name=='nt','POSIX permissions')
    def test_shared_raw_folder_keeps_its_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            raw=Path(root)/'raw';raw.mkdir();os.chmod(raw,0o755)
            R.fetch([],root,Client({}))
            self.assertEqual(raw.stat().st_mode&0o777,0o755)
            self.assertEqual((raw/'rap_documents').stat().st_mode&0o777,0o700)

    def test_robots_redirect_is_followed(self):
        with tempfile.TemporaryDirectory() as root:
            result,client=self.run_fetch(['https://example.invalid/rap.pdf'],{
                'https://example.invalid/robots.txt':(b'',301,{'Location':'https://www.example.invalid/robots.txt'}),
                'https://www.example.invalid/robots.txt':(b'User-agent: *\nAllow: /',200,{}),
                'https://example.invalid/rap.pdf':(b'%PDF fixture',200,{})},root)
        self.assertEqual(result['counts'],{'downloaded':1})
        with tempfile.TemporaryDirectory() as root:
            result,client=self.run_fetch(['https://example.invalid/rap.pdf'],{
                'https://example.invalid/robots.txt':(b'',302,{'Location':'https://www.example.invalid/robots.txt'}),
                'https://www.example.invalid/robots.txt':(b'User-agent: *\nDisallow: /',200,{}),
                'https://example.invalid/rap.pdf':(b'%PDF fixture',200,{})},root)
        self.assertEqual(result['counts'],{'robots_disallowed':1});self.assertEqual(len(client.calls),2)

    def test_html_discovery_robots_per_pdf_and_newest_order(self):
        html=b'<html><a href="/rap-2025.pdf">RAP</a><a href="/rap-2027.pdf">RAP</a><a href="https://other.invalid/rap.pdf">RAP</a></html>'
        with tempfile.TemporaryDirectory() as root:
            result,client=self.run_fetch(['http://example.invalid/plan'],{
                'https://example.invalid/robots.txt':(b'User-agent: *\nDisallow: /rap-2025.pdf',200,{}),
                'https://example.invalid/plan':(html,200,{'Content-Type':'text/html'}),
                'https://example.invalid/rap-2027.pdf':(b'%PDF fixture',200,{})},root)
            self.assertEqual(result['counts'],{'not_pdf':1,'downloaded':1,'robots_disallowed':1})
            self.assertEqual(result['attempts'][1]['url'],'https://example.invalid/rap-2027.pdf')
            self.assertTrue(all(b[1]-a[1]>=2 for a,b in zip(client.calls,client.calls[1:])))
            self.assertTrue(all(call[2]['allow_redirects'] is False and call[2]['timeout']==120 for call in client.calls))
            self.assertTrue(list((Path(root)/'raw/rap_documents/fetches').glob('*.json')))

    def test_not_pdf_cap_and_duplicate(self):
        with tempfile.TemporaryDirectory() as root:
            routes={'https://example.invalid/robots.txt':(b'',404,{}),
                    'https://example.invalid/not.pdf':(b'not a PDF',200,{}),
                    'https://example.invalid/big.pdf':(b'',200,{'Content-Length':str(R.MAX_BYTES+1)}),
                    'https://example.invalid/rap.pdf':(b'%PDF fixture',200,{})}
            result,client=self.run_fetch(['https://example.invalid/'+p for p in ['not.pdf','big.pdf','rap.pdf','rap.pdf']],routes,root)
            self.assertEqual([r['status'] for r in result['attempts']],['not_pdf','too_large','downloaded','duplicate'])
            paths=list((Path(root)/'raw/rap_documents').glob('*.pdf'));self.assertEqual(len(paths),1)
            self.assertEqual(paths[0].stem,hashlib.sha256(b'%PDF fixture').hexdigest())
            # Exercise the streaming cap without allocating 40 MB for the unit test.
            with patch.object(R,'MAX_BYTES',8):
                result,client=self.run_fetch(['https://example.invalid/rap.pdf'],routes,root)
            self.assertEqual(result['counts'],{'too_large':1})

    def test_redirect_blocked_before_request_and_new_host_robots(self):
        with tempfile.TemporaryDirectory() as root:
            result,client=self.run_fetch(['https://example.invalid/rap'],{
                'https://example.invalid/robots.txt':(b'',404,{}),
                'https://example.invalid/rap':(b'',302,{'Location':'https://reconciliation.my.salesforce.com/file.pdf'})},root)
        self.assertEqual(result['counts'],{'blocked_host':1});self.assertEqual(len(client.calls),2)
        with tempfile.TemporaryDirectory() as root:
            result,client=self.run_fetch(['https://example.invalid/rap'],{
                'https://example.invalid/robots.txt':(b'',404,{}),
                'https://example.invalid/rap':(b'',302,{'Location':'https://cdn.example.invalid/rap.pdf'}),
                'https://cdn.example.invalid/robots.txt':(b'User-agent: *\nDisallow: /',200,{})},root)
        self.assertEqual(result['counts'],{'robots_disallowed':1});self.assertEqual(len(client.calls),3)

    def test_rap_page_download_links_and_manual_add(self):
        html=('<html><head><title>Our Reconciliation Action Plan</title></head>'
              '<a href="/files/plan-2025.pdf">Download</a><a href="/media/28716/download">Stretch RAP</a>'
              '<a href="/media/1/download">Annual report</a><a href="https://other.example/x.pdf">Download</a></html>')
        links=R.discover_links(html,'https://www.example.com.au/about/reconciliation')
        self.assertEqual(set(links),{'https://www.example.com.au/files/plan-2025.pdf','https://www.example.com.au/media/28716/download'})
        self.assertFalse(R.discover_links(html.replace('Reconciliation Action Plan','News'),'https://www.example.com.au/news')[1:])
        with tempfile.TemporaryDirectory() as root:
            good=Path(root)/'good.pdf';good.write_bytes(b'%PDF manual');bad=Path(root)/'bad.pdf';bad.write_bytes(b'<html>')
            rows=[dict(record_key='k1',organisation='Example',file=str(good),source_url='https://www.example.com.au/rap.pdf'),
                  dict(record_key='k2',organisation='Example',file=str(bad),source_url=''),
                  dict(record_key='k1',organisation='Example',file=str(good),source_url='')]
            result=R.add_manual(rows,root)
            self.assertEqual([r['status'] for r in result['attempts']],['downloaded','not_pdf','duplicate'])
            self.assertTrue(all(r['found_via']=='manual_download' for r in result['attempts']))
            self.assertTrue((Path(root)/'raw/rap_documents'/(hashlib.sha256(b'%PDF manual').hexdigest()+'.pdf')).exists())

    def test_link_scope_limit_and_order(self):
        html=''.join('<a href="https://docs.example.com.au/rap-'+str(year)+'.pdf">Plan</a>' for year in range(2020,2027))
        html+='<a href="https://other.com.au/rap-2030.pdf">RAP</a><a href="https://example.com.au/financial.pdf">Report</a>'
        links=R.discover_links(html,'https://www.example.com.au/plans')
        self.assertEqual(len(links),3);self.assertIn('2026',links[0]);self.assertTrue(all('docs.example.com.au' in u for u in links))


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL test database not configured')
class Database(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine,migrate
        cls.db=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.db.url.database!='illoura_market_test':raise RuntimeError('Dedicated test database required')
        migrate(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.dispose()

    def test_extract_view_shape_idempotence_and_build_rap(self):
        from uuid import uuid4
        from sqlalchemy import text
        from reportlab.pdfgen.canvas import Canvas
        from market_intelligence.research import build_rap
        snapshot=str(uuid4());record=directory()
        # A current directory fixture uses existing clearance keys; document people have no redaction.
        provenance={'rap_privacy_review':{'contains_personal_data':False}}
        with self.db.begin() as conn:
            conn.execute(text("INSERT INTO market.research_snapshots(id,source,observed_at,provenance,row_count,sha256) VALUES(:id,'rap_directory',now(),CAST(:p AS jsonb),1,:sha)"),
                         dict(id=snapshot,p=json.dumps(provenance),sha=hashlib.sha256(snapshot.encode()).hexdigest()))
            conn.execute(text("INSERT INTO market.research_records(snapshot_id,record_key,payload) VALUES(:id,:key,CAST(:p AS jsonb))"),
                         dict(id=snapshot,key=record['record_key'],p=json.dumps(dict(record,document_sha256='0'*64))))
            conn.execute(text("INSERT INTO market.current_research_snapshots(source,snapshot_id) VALUES('rap_directory',:id) ON CONFLICT(source) DO UPDATE SET snapshot_id=excluded.snapshot_id"),dict(id=snapshot))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);pdf=root/'fixture.pdf';canvas=Canvas(str(pdf));y=800
            for line in page(AECOM_SPEND+'\n'+AECOM_EMPLOYMENT+'\nContact details\nName: Jane Example\nPosition: RAP Lead\nEmail: rap@example.invalid').splitlines():
                canvas.drawString(10,y,line);y-=15
            canvas.save();payload=pdf.read_bytes();sha=hashlib.sha256(payload).hexdigest()
            R.fetch([dict(record,url='https://example.invalid/rap.pdf')],root,
                    Client({'https://example.invalid/robots.txt':(b'',404,{}),'https://example.invalid/rap.pdf':(payload,200,{})}),
                    clock=lambda:0,sleeper=lambda _:None,engine=self.db)
            first=R.extract(self.db,root);self.assertEqual(first['counts'],{'matched':1})
            self.assertEqual(R.extract(self.db,root)['documents'],0)
            with self.db.connect() as conn:
                finding=conn.execute(text('SELECT * FROM market.current_rap_document_findings WHERE record_key=:key'),dict(key=record['record_key'])).mappings().one()
                self.assertEqual(finding['procurement_spend_percent'],2)
                self.assertEqual(finding['fields'],{'has_procurement_target':{'value':True},'employment_target':{'value':True},'requires_supply_nation_certified':{'value':'unknown'}})
                self.assertTrue(conn.execute(text('SELECT bool_and(is_personal) FROM market.rap_people WHERE document_sha256=:sha'),dict(sha=sha)).scalar())
            result=build_rap(self.db,date(2026,11,28))
            target=next(r for r in result['targets'] if r['record_key']==record['record_key'])
            self.assertTrue(target['approved_extraction']['has_procurement_target']['value'])
            self.assertEqual(target['approved_extraction_provenance']['source'],'rule_based_document_extraction')
            self.assertEqual(target['approved_extraction_provenance']['document_sha256'],sha)


if __name__=='__main__':unittest.main()

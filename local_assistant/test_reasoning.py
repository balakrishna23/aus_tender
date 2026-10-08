import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3
from planning import rule_plan,validated_model_plan
from reasoning import analyse,date_value
import assistant


class PlanTests(unittest.TestCase):
    def test_role_and_org_are_separate(self):
        p=rule_plan('Which cyber contracts held by DXC end within 90 days?')
        self.assertEqual(['cyber'],p['roles'])
        self.assertEqual(['DXC'],p['entities'])
        self.assertIn('renewals',p['tasks'])
        self.assertEqual(90,p['days'])

    def test_complex_question_keeps_filters(self):
        p=rule_plan('Which cyber contracts end within 90 days? Compare suppliers and choose 3 for ICT labour hire rather than a one-off assessment or equipment purchase.')
        self.assertEqual(['cyber'],p['roles'])
        self.assertTrue(p['staffing_only'])
        self.assertIn('equipment',p['exclude'])
        self.assertIn('assessments',p['exclude'])
        self.assertIn('renewals',p['tasks'])

    def test_comparison_has_multiple_tasks_and_real_alias(self):
        p=rule_plan('Compare DXC and EY using renewals, panels and MMR evidence.')
        self.assertEqual(['DXC','ERNST & YOUNG'],p['entities'])
        self.assertTrue(set(['company','renewals','panels','mmr','comparison'])<=set(p['tasks']))

    def test_candidates_do_not_erase_opportunity_search(self):
        p=rule_plan('Show cyber opportunities our NV1 candidates could fill.')
        self.assertIn('opportunities',p['tasks'])
        self.assertTrue(any('Candidate' in x for x in p['missing']))

    def test_live_verification_never_claimed(self):
        p=rule_plan('Show cyber renewals and verify the sources live.')
        self.assertTrue(any('Live website' in x for x in p['missing']))

    def test_no_invented_entity_from_fallback(self):
        import json
        p=rule_plan('Tell me about Macrotech')
        chat=lambda *args:(json.dumps({'tasks':['company'],'entities':['IBM']}),{})
        with self.assertRaises(ValueError):
            validated_model_plan('Tell me about Macrotech',chat,p)

    def test_filter_cannot_be_discarded_by_model(self):
        import json
        p=rule_plan('Find cyber contracts ending within 31 days for Macrotech')
        chat=lambda *args:(json.dumps({'tasks':['panels'],'entities':['Macrotech']}),{})
        result=validated_model_plan('Find cyber contracts ending within 31 days for Macrotech',chat,p)
        self.assertIn('renewals',result['tasks'])
        self.assertEqual(['cyber'],result['roles'])
        self.assertEqual(31,result['days'])

    def test_named_contract(self):
        p=rule_plan('Does CN4281051 prove AUCYBER urgently needs staff?')
        self.assertEqual(['CN4281051'],p['contract_ids'])
        self.assertIn('fact_check',p['tasks'])
        self.assertTrue(any('demand' in x for x in p['missing']))

    def test_dates(self):
        self.assertEqual(date(2025,9,30),date_value('30-Sep-2025'))
        self.assertIsNone(date_value('not recorded'))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)
        self.original=assistant.DB_DIR
        assistant.DB_DIR=self.path
        with closing(sqlite3.connect(self.path/'intelligence.db')) as c:
            c.executescript('''CREATE TABLE renewals(cn_id,buyer,supplier,supplier_abn,description,
                category_family,current_value,current_end_date,contact_safety,page_url,api_url);
                CREATE TABLE panel_members(son_id,panel,member,abn,deed_start,deed_end,panel_url);
                CREATE TABLE opportunities(source,source_id,title,buyer,role_families,category_family,
                closes_at,expected_from,expected_to,status_effective,contact_safety,url,retrieved_at,is_relevant);
                CREATE TABLE agency_contacts(organisation,name,email,phone,source_url,verified,
                confidence,stale,opted_out,contact_safety,usable_for_outreach);
                CREATE TABLE mmr_primes(organisation,group_name,abn,mmr_contracts,
                group_mmr_contracts,list_date,match_review);''')
            for cn,supplier,description,family in [
                ('CN1','Cyber Supplier','ICT Equipment','ICT equipment and general'),
                ('CN2','DXC','Senior Cyber Security Analyst','labour hire and recruitment'),
                ('CN3','DXC','Cyber Assurance assessment','labour hire and recruitment'),
                ('CN4','DXC','Software Licences','ICT services'),
                ('CN5','Ordinary','Senior Business Analyst','labour hire and recruitment')]:
                c.execute('INSERT INTO renewals VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (cn,'Agency',supplier,'1',description,family,100,'2026-10-10','SAFE_GENERAL',
                     'https://example.org/'+cn,'https://example.org/api/'+cn))
            c.execute('INSERT INTO panel_members VALUES (?,?,?,?,?,?,?)',
                ('SON1','Panel','DXC','1','01-Jan-2020','30-Sep-2025','https://example.org/SON1'))
            c.execute('INSERT INTO panel_members VALUES (?,?,?,?,?,?,?)',
                ('SON2','Panel','DXC','1','01-Jan-2026','30-Sep-2028','https://example.org/SON2'))
            c.commit()

    def tearDown(self):
        assistant.DB_DIR=self.original
        self.temp.cleanup()

    def run_plan(self,question):
        return analyse(rule_plan(question),assistant.read_rows,assistant.like,date(2026,10,8))

    def test_contract_value_boundary_is_inclusive_only_when_requested(self):
        for wording,expected in [('above $100',0),('at least $100',5),
                                 ('below $100',0),('at most $100',5)]:
            with self.subTest(wording=wording):
                result=self.run_plan('Show contracts ending within 90 days '+wording)
                self.assertEqual(expected,len(result['facts']))

    def test_mmr_threshold_uses_requested_count_and_deduplicates_organisation(self):
        with closing(sqlite3.connect(self.path/'intelligence.db')) as c:
            c.executemany('INSERT INTO mmr_primes VALUES (?,?,?,?,?,?,?)',[
                ('Example A','Group A','1',2,8,'2026-02-25','unreviewed'),
                ('Example A','Group A','2',2,8,'2026-02-25','unreviewed'),
                ('Example B','Group B','3',3,3,'2026-02-25','unreviewed')])
            c.commit()
        inclusive=self.run_plan('Show companies with at least 2 active MMR contracts')
        self.assertEqual(3,len(inclusive['facts']))
        self.assertTrue(any('2 distinct listed organisations' in s for s in inclusive['sections']))
        strict=self.run_plan('Show companies with over 2 active MMR contracts')
        self.assertEqual(['Example B'],[r['organisation'] for r in strict['facts']])
        group=self.run_plan('Show companies with over 3 active MMR contracts at group level')
        self.assertEqual({'Example A'},{r['organisation'] for r in group['facts']})

    def test_cyber_applies_to_work_not_supplier_name(self):
        result=self.run_plan('Which cyber contracts end within 90 days?')
        self.assertEqual({'CN2','CN3'},{r['cn_id'] for r in result['facts']})

    def test_labour_hire_excludes_equipment_and_licences(self):
        result=self.run_plan('Which ICT labour hire contracts end within 90 days?')
        ids={r['cn_id'] for r in result['facts']}
        self.assertNotIn('CN1',ids)
        self.assertNotIn('CN4',ids)

    def test_assessment_excluded_when_requested(self):
        result=self.run_plan('Which cyber labour hire contracts end within 90 days? Exclude assessments and equipment.')
        self.assertEqual({'CN2'},{r['cn_id'] for r in result['facts']})

    def test_model_cannot_broaden_entity(self):
        result=self.run_plan('Which cyber contracts held by AUCYBER end within 90 days?')
        self.assertEqual([],result['facts'])

    def test_expired_panel_explicitly_labelled(self):
        result=self.run_plan('Which panels is DXC on?')
        statuses={r['son_id']:r['date_status'] for r in result['facts']}
        self.assertTrue(statuses['SON1'].startswith('expired'))
        self.assertTrue(statuses['SON2'].startswith('within'))

    def test_sources_and_calculated_days_preserved(self):
        result=self.run_plan('Which cyber contracts end within 90 days?')
        self.assertTrue(all(r['days_until_end']==2 for r in result['facts']))
        self.assertTrue(all(r['page_url'].startswith('https://example.org/') for r in result['facts']))

    def test_full_set_count_not_display_sample(self):
        with closing(sqlite3.connect(self.path/'intelligence.db')) as c:
            for i in range(20):
                c.execute('INSERT INTO renewals SELECT ?,buyer,supplier,supplier_abn,description,category_family,current_value,current_end_date,contact_safety,page_url,api_url FROM renewals WHERE cn_id=?',('CNX'+str(i),'CN2'))
            c.commit()
        result=self.run_plan('Which cyber contracts end within 90 days?')
        self.assertIn('22 matching',result['sections'][0])
        self.assertEqual(8,len(result['facts']))

    def test_semantic_candidates_keep_exact_constraints(self):
        from unittest.mock import patch
        plan=rule_plan('Contracts held by DXC ending within 90 days at least $100 excluding assessments and equipment')
        plan.update(hybrid=True,search_topic='incident response',explicit_search_topic=True)
        def fake_rank(rows,topic,field):
            self.assertEqual({'CN2','CN4'},{r['cn_id'] for r in rows})
            return rows,{'mode':'hybrid','topic':topic,'eligible_records':len(rows),'matched_records':len(rows)}
        with patch('hybrid.rank',side_effect=fake_rank):
            result=analyse(plan,assistant.read_rows,assistant.like,date(2026,10,8))
        self.assertEqual({'CN2','CN4'},{r['cn_id'] for r in result['facts']})

    def test_semantic_search_cannot_relax_value_or_abn(self):
        from unittest.mock import patch
        for question in ['Cyber contracts ending within 90 days above $100',
                         'Cyber contracts ending within 90 days for ABN 12345678901']:
            plan=rule_plan(question);plan.update(hybrid=True,search_topic='cyber')
            with patch('hybrid.rank',return_value=([],{'mode':'hybrid','topic':'cyber','eligible_records':0,'matched_records':0})) as ranked:
                result=analyse(plan,assistant.read_rows,assistant.like,date(2026,10,8))
                self.assertEqual([],ranked.call_args.args[0])
                self.assertFalse(result['facts'])

    def test_semantic_failure_falls_back_without_broadening(self):
        from unittest.mock import patch
        plan=rule_plan('Which cyber contracts end within 90 days?')
        plan.update(hybrid=True,search_topic='cyber')
        with patch('hybrid.rank',side_effect=ValueError('index unavailable')):
            result=analyse(plan,assistant.read_rows,assistant.like,date(2026,10,8))
        self.assertEqual({'CN2','CN3'},{r['cn_id'] for r in result['facts']})
        self.assertEqual('keyword fallback',result['retrieval_status'][0]['mode'])

    def test_semantic_search_keeps_excluded_roles(self):
        from unittest.mock import patch
        plan=rule_plan('Contracts ending within 90 days excluding cyber')
        plan.update(hybrid=True,search_topic='business analysis',explicit_search_topic=True)
        with patch('hybrid.rank',side_effect=lambda rows,*args:(rows,{'mode':'hybrid','topic':'business analysis','eligible_records':len(rows),'matched_records':len(rows)})):
            result=analyse(plan,assistant.read_rows,assistant.like,date(2026,10,8))
        self.assertTrue(all(r['cn_id'] not in ('CN2','CN3') for r in result['facts']))

    def test_explicit_topic_keeps_separate_positive_role_constraint(self):
        from unittest.mock import patch
        plan=rule_plan('Cyber contracts ending within 90 days')
        plan.update(hybrid=True,search_topic='cloud migration',explicit_search_topic=True)
        with patch('hybrid.rank',side_effect=lambda rows,*args:(rows,{'mode':'hybrid','topic':'cloud migration','eligible_records':len(rows),'matched_records':len(rows)})) as ranked:
            analyse(plan,assistant.read_rows,assistant.like,date(2026,10,8))
        self.assertEqual({'CN2','CN3'},{r['cn_id'] for r in ranked.call_args.args[0]})

    def test_staffing_priority_survives_high_service_similarity(self):
        from unittest.mock import patch
        plan=rule_plan('Cyber labour hire contracts ending within 90 days')
        plan.update(hybrid=True,search_topic='cyber',explicit_search_topic=True)
        def fake_rank(rows,*args):
            rows.sort(key=lambda row:row['cn_id']=='CN2')
            for index,row in enumerate(rows):row['retrieval']={'fusion_score':1-index*0.1}
            return rows,{'mode':'hybrid','topic':'cyber','eligible_records':len(rows),'matched_records':len(rows)}
        with patch('hybrid.rank',side_effect=fake_rank):
            result=analyse(plan,assistant.read_rows,assistant.like,date(2026,10,8))
        self.assertEqual('CN2',result['facts'][0]['cn_id'])


if __name__=='__main__':
    unittest.main()

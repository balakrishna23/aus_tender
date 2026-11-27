import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from market_intelligence.acquire_rap import load_collection
from market_intelligence.parsers import SourceError
from market_intelligence.rap_extract import digest, reviewer_id
from market_intelligence.rap_review import (audit_path, coverage, evaluate, identity_index,
    identity_reason, identity_assessment, legal_name_key, partition, review, safe_plan_url, verify)
from test_market_rap_acquisition import collection

ACCOUNT='00000000-0000-4000-8000-000000000099'
HUMAN='00000000-0000-4000-8000-000000000098'


def context(): return dict(snapshots=[],entities=[],baseline=[])


def entity(name, abn='11111111111', individual=False, source='abn', record_id='one', kind='company'):
    return dict(name=name, abn=abn, is_individual=individual, source=source, record_id=record_id, entity_type=kind)


class RapReviewTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,ILLOURA_RAP_DIRECTORY_REVIEWER_IDS=ACCOUNT,ILLOURA_RAP_REVIEWER_IDS=HUMAN)
        self.env.start(); self.addCleanup(self.env.stop)

    def test_directory_scope_never_grants_pdf_or_commitment_authority(self):
        self.assertEqual(reviewer_id(ACCOUNT,'rap_directory'),ACCOUNT)
        self.assertEqual(reviewer_id(HUMAN),HUMAN)
        for account, scope in [(ACCOUNT,'rap_extract'),(HUMAN,'rap_directory'),('not-a-uuid','rap_directory')]:
            with self.assertRaises(SourceError): reviewer_id(account,scope)
        with patch.dict(os.environ,ILLOURA_RAP_REVIEWER_IDS=ACCOUNT):
            with self.assertRaises(SourceError): reviewer_id(ACCOUNT)

    def test_unexplained_names_and_representative_offices_need_register_evidence(self):
        for name in ['Synthetic Person','Synthetic & Invented','A. Synthetic',
            'Dr Synthetic Foundation','SyntheticPerson','Synthetic Person MP Office',
            'Senator Synthetic Community Office','S. I. Synthetic','Synthetic and Invented',
            'Ltd','LTD','Pty Ltd','The Pty Limited']:
            with self.subTest(name=name): self.assertIsNotNone(identity_reason(name,{}))
        self.assertIsNone(identity_reason('Regional Community Health Association',{}))
        ctx=context();ctx['entities']=[entity('Synthetic Person Pty Ltd')]
        self.assertIsNone(identity_reason(' SYNTHETIC Person Pty. Ltd ',identity_index(ctx)))

    def test_brand_words_allowed_with_positive_organisational_evidence(self):
        for name in ['Synthetic Person Pty Ltd','Synthetic & Invented Partners','A. Synthetic Engineering',
            'Invented Mining','Imaginary Bank','Invented Family Trust','Invented Australia',
            'Invented Network','Imaginary Solutions','XYZ','XYZ Invented','Invented Law',
            'Imaginary Health','Invented Shire','Imaginary Gallery','Invented Limited']:
            with self.subTest(name=name): self.assertIsNone(identity_reason(name,{}))

    def test_legal_suffix_normalisation_basis_and_collision_checks(self):
        ctx=context();ctx['entities']=[entity('The Invented Group Pty Limited.')]
        index=identity_index(ctx)
        self.assertEqual(legal_name_key('THE Invented Group Pty Limited.'),'invented group')
        self.assertEqual(identity_assessment('The Invented Group Pty Limited',index),(None,'register_exact'))
        self.assertEqual(identity_assessment('Invented Group',index),(None,'register_legal_normalised'))
        for suffix in ['Ltd','Limited','Pty Ltd','Inc.','Incorporated','Co','Company','PLC','LLP']:
            self.assertEqual(legal_name_key('The Invented Group '+suffix),'invented group')
        ctx['entities'].append(entity('Invented Group Inc',abn='22222222222'))
        self.assertEqual(identity_assessment('The Invented Group Pty Limited',identity_index(ctx)),
                         (None,'register_multiple_non_individual'))
        ctx['entities'][-1]['is_individual']=True
        self.assertEqual(identity_reason('Invented Group',identity_index(ctx)),'individual_register_match')
        ctx['entities']=[entity('Synthetic Person Limited')]
        self.assertIsNone(identity_reason('Synthetic Person',identity_index(ctx)))

    def test_individual_and_sole_trader_always_quarantined(self):
        for row in [entity('Synthetic Person',individual=True),entity('Synthetic Person',kind='Individual'),entity('Synthetic Person',kind='IND'),entity('Synthetic Person',kind='Sole Trader')]:
            ctx=context();ctx['entities']=[row]
            self.assertEqual(identity_reason('Synthetic Person',identity_index(ctx)),'individual_register_match')

    def test_nameless_individual_abn_overrides_other_register_name(self):
        ctx=context();ctx['entities']=[entity(None,individual=True),entity('Synthetic Firm',source='acnc')]
        self.assertEqual(identity_reason('Synthetic Firm',identity_index(ctx)),'individual_register_match')

    def test_multiple_non_individual_entities_clear_same_abn_is_one_candidate(self):
        ctx=context();ctx['entities']=[entity('Synthetic Firm'),entity('Synthetic Firm',abn='22222222222')]
        self.assertEqual(identity_assessment('Synthetic Firm',identity_index(ctx)),
                         (None,'register_multiple_non_individual'))
        ctx['entities'][1]=entity('Synthetic Firm',source='acnc')
        self.assertIsNone(identity_reason('Synthetic Firm',identity_index(ctx)))
        ctx['entities'].append(entity('Synthetic Firm',individual=True))
        self.assertEqual(identity_reason('Synthetic Firm',identity_index(ctx)),'individual_register_match')

    def test_multiple_register_candidates_audited_without_abn_association(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);bundle=load_collection(root)
            name=bundle['records'][0]['organisation']
            for normalised in (False,True):
                ctx=context();ctx['entities']=[entity(name),entity(
                    name+' Pty Ltd' if normalised else name,abn='22222222222')]
                for individual in (False,True):
                    with self.subTest(normalised=normalised,individual=individual):
                        ctx['entities'][1]['is_individual']=individual
                        with patch('market_intelligence.rap_review.load_collection',return_value=copy.deepcopy(bundle)):
                            reviewed,audit=evaluate(root,ctx)
                        decision=audit['decisions'][0]
                        self.assertEqual(audit['policy_version'],'rap-directory-privacy-3')
                        self.assertEqual(reviewed['provenance']['privacy_policy'],'rap-directory-privacy-3')
                        self.assertEqual(decision['register_candidate_count'],2)
                        self.assertEqual(decision['cleared'],not individual)
                        self.assertEqual(decision['reasons'],['individual_register_match'] if individual else [])
                        self.assertEqual(decision['identity_basis'],
                            'register_exact' if individual else 'register_multiple_non_individual')
                        if not individual:
                            self.assertEqual(reviewed['records'][0],bundle['records'][0])
                        self.assertNotIn('abn',decision)
            ctx=context();ctx['entities']=[entity(name),entity(name,source='acnc')]
            decision=partition(bundle,ctx)[1][0]
            self.assertEqual(decision['register_candidate_count'],1)
            self.assertEqual(decision['identity_basis'],'register_exact')

    def test_plan_url_adversaries(self):
        for url in ['http://reconciliation.my.salesforce.com/a','https://evil.invalid/a',
            'https://reconciliation.my.salesforce.com.evil.invalid/a','https://user:secret@reconciliation.my.salesforce.com/a',
            'https://reconciliation.my.salesforce.com/a?q=x','https://reconciliation.my.salesforce.com/a#x',
            'https://reconciliation.my.salesforce.com/%70erson%40example.invalid',
            'https://reconciliation.my.salesforce.com/%2570erson%2540example.invalid',
            'https://reconciliation.my.salesforce.com/0412345678',
            'https://reconciliation.my.salesforce.com/contact-1800-123-456.pdf',
            'https://reconciliation.my.salesforce.com/%2b61-412-345-678',
            'https://reconciliation.my.salesforce.com/%3fkey=private',
            'https://reconciliation.my.salesforce.com/%0dheader',
            'https://reconciliation.my.salesforce.com:444/a','https://reconciliation.my.salesforce.com/../a']:
            with self.subTest(url=url),self.assertRaises(SourceError): safe_plan_url(url)
        for host in ['reconciliation.my.salesforce.com','recaus.my.salesforce.com','www.reconciliation.org.au']:
            safe_plan_url('https://'+host+'/sfc/p/AbCdEFg')
            safe_plan_url('https://'+host+'/sfc/p/A000123456789B/a/C0412345678D/Opaque_Id')
        safe_plan_url('')

    def test_duplicates_and_conflicts_quarantine_all_involved(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection(Path(tmp));bundle=load_collection(tmp)
            for conflict in (False,True):
                changed=copy.deepcopy(bundle);row=copy.deepcopy(changed['records'][0])
                if conflict: row.update(record_key='other',rap_type='Elevate')
                changed['records'].append(row)
                cleared,decisions=partition(changed,context())
                self.assertEqual(len(cleared),3)
                self.assertIn('duplicate_or_conflicting_organisation',decisions[0]['reasons'])
                self.assertFalse(decisions[-1]['cleared'])

    def test_period_verbatim_validity_missing_link_and_contact_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection(Path(tmp));bundle=load_collection(tmp)
            bundle['records'][0]['period_as_published']='2026-2028'
            cleared,decisions=partition(bundle,context())
            self.assertEqual(len(cleared),4)
            self.assertFalse(decisions[0]['period_valid'])
            self.assertTrue(decisions[0]['missing_plan_link'])
            for field,value in [('organisation','person@example.invalid'),('period_as_published','0412 345 678')]:
                changed=copy.deepcopy(bundle);changed['records'][0][field]=value
                self.assertIn('privacy_check_failed',partition(changed,context())[1][0]['reasons'])

    def test_coverage_loss_raw_and_cleared_empty_and_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection(Path(tmp));bundle=load_collection(tmp);ctx=context()
            ctx['baseline']=[dict(snapshot_id='fixture',payload=dict(rap_type='Reflect'),provenance={})]*10
            reasons=coverage(bundle,bundle['records'],ctx)
            self.assertIn('collection_overall_coverage_loss',reasons)
            self.assertNotIn('cleared_type_coverage_loss',reasons)
            self.assertIn('empty_cleared_subset',coverage(bundle,[],context()))
            bundle['provenance']['complete']=False
            self.assertIn('incomplete_collection',coverage(bundle,bundle['records'],context()))

    def test_legacy_baseline_skips_cleared_comparison_but_checks_minimum_fraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection(Path(tmp));bundle=load_collection(tmp);ctx=context()
            ctx['baseline']=[dict(snapshot_id='legacy',payload=r,provenance={}) for r in bundle['records']]
            self.assertEqual(coverage(bundle,bundle['records'][:2],ctx),[])
            self.assertIn('low_cleared_fraction',coverage(bundle,bundle['records'][:1],ctx))
            self.assertNotIn('cleared_overall_coverage_loss',coverage(bundle,bundle['records'][:1],ctx))
            self.assertIn('low_cleared_fraction',coverage(bundle,bundle['records'][:1],context()))

    def test_automated_baseline_checks_cleared_and_prior_raw_collection_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection(Path(tmp));bundle=load_collection(tmp);ctx=context()
            previous=dict(privacy_policy='previous-policy',type_counts=dict.fromkeys(bundle['provenance']['type_counts'],2))
            ctx['baseline']=[dict(snapshot_id='automated',payload=r,provenance=previous) for r in bundle['records']]
            reasons=coverage(bundle,bundle['records'][:2],ctx)
            for reason in ['collection_overall_coverage_loss','collection_type_coverage_loss',
                           'cleared_overall_coverage_loss','cleared_type_coverage_loss']:
                self.assertIn(reason,reasons)
            self.assertNotIn('low_cleared_fraction',reasons)

    def test_two_pass_bundle_audit_and_independent_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);destination=root/'bundle.json'
            with patch('market_intelligence.rap_review.load_context',return_value=context()) as load:
                self.assertEqual(review(None,root,destination,ACCOUNT)['status'],'reviewed')
                self.assertEqual(load.call_count,2)
                self.assertEqual(verify(None,root,destination)['status'],'verified')
            bundle=json.loads(destination.read_text());audit=json.loads(audit_path(destination).read_text())
            self.assertEqual(set(bundle['privacy_review']),{'reviewer_id','contains_personal_data','payload_sha256'})
            self.assertEqual(bundle['privacy_review']['payload_sha256'],digest({k:bundle[k] for k in ['records','provenance']}))
            self.assertEqual(audit['counts'],dict(input=4,cleared=4,quarantined=0,malformed_periods=0,cleared_malformed_periods=0))
            self.assertNotIn('Association',json.dumps(audit))

    def test_payload_provenance_audit_and_context_tampering_rejected(self):
        for mode in ('payload','provenance','audit','register','baseline'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);collection(root);destination=root/'bundle.json'
                with patch('market_intelligence.rap_review.load_context',return_value=context()):review(None,root,destination,ACCOUNT)
                bundle=json.loads(destination.read_text());ctx=context()
                if mode=='payload': bundle['records'][0]['period_as_published']='tampered'
                if mode=='provenance':bundle['provenance']['union_count']=99
                if mode in ('payload','provenance'):
                    # Even a recomputed clearance digest cannot defeat independent replay.
                    bundle['privacy_review']['payload_sha256']=digest({k:bundle[k] for k in ['records','provenance']})
                    destination.write_text(json.dumps(bundle))
                if mode=='audit':
                    audit=json.loads(audit_path(destination).read_text());audit['counts']['quarantined']=100
                    audit_path(destination).write_text(json.dumps(audit))
                if mode=='register':ctx['snapshots']=[dict(source='acnc',snapshot_id='changed')]
                if mode=='baseline':ctx['baseline']=[dict(snapshot_id='changed',payload=dict(rap_type='Reflect'),provenance={})]
                with patch('market_intelligence.rap_review.load_context',return_value=ctx):
                    with self.assertRaises(SourceError):verify(None,root,destination)

    def test_second_pass_context_changes_prevent_clearance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);ctx=context();ctx['snapshots']=[dict(source='oric',snapshot_id='changed')]
            with patch('market_intelligence.rap_review.load_context',side_effect=[context(),ctx]):
                self.assertEqual(review(None,root,root/'bundle.json',ACCOUNT)['reasons'],['review_error'])
            self.assertFalse((root/'bundle.json').exists())

    def test_identical_already_imported_bundle_can_be_reverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);path=root/'bundle.json'
            with patch('market_intelligence.rap_review.load_context',return_value=context()):review(None,root,path,ACCOUNT)
            bundle=json.loads(path.read_text());ctx=context()
            provenance=dict(bundle['provenance'],rap_privacy_review=bundle['privacy_review'])
            ctx['baseline']=[dict(snapshot_id='already-imported',payload=r,provenance=provenance) for r in bundle['records']]
            with patch('market_intelligence.rap_review.load_context',return_value=ctx):
                self.assertEqual(verify(None,root,path)['status'],'verified')

    def test_incomplete_review_records_reason_and_creates_no_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);(root/'Reflect.html').write_bytes(b'broken')
            with patch('market_intelligence.rap_review.load_context',return_value=context()):
                result=review(None,root,root/'bundle.json',ACCOUNT)
            self.assertEqual(result['reasons'],['collection_invalid'])
            self.assertFalse((root/'bundle.json').exists())
            self.assertEqual(json.loads(audit_path(root/'bundle.json').read_text())['verifier_pass'],'not_run')

    def test_errors_are_classified_without_private_exception_details_or_partial_results(self):
        from sqlalchemy.exc import SQLAlchemyError
        from market_intelligence.acquire_common import AcquisitionError
        for stage in ('review','verifier'):
            for error,reason in [(AcquisitionError('synthetic private details'),'collection_invalid'),
                                 (OSError('synthetic private details'),'review_error'),
                                 (SQLAlchemyError('synthetic private details'),'review_error'),
                                 (RuntimeError('synthetic private details'),'review_error')]:
                with self.subTest(stage=stage,error=type(error).__name__),tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);collection(root);path=root/'bundle.json'
                    good=evaluate(root,context())
                    with patch('market_intelligence.rap_review.load_context',return_value=context()),patch(
                        'market_intelligence.rap_review.evaluate',side_effect=[error] if stage=='review' else [good,error]):
                        result=review(None,root,path,ACCOUNT)
                    self.assertEqual(result['reasons'],[reason])
                    audit=json.loads(audit_path(path).read_text())
                    self.assertEqual(audit['failure_stage'],stage)
                    self.assertEqual(audit['review_pass'],'error' if stage=='review' else 'passed')
                    self.assertEqual(audit['verifier_pass'],'not_run' if stage=='review' else 'error')
                    self.assertNotIn('counts',audit)
                    self.assertNotIn('decisions',audit)
                    self.assertNotIn('synthetic private details',json.dumps(audit))
                    self.assertFalse(path.exists())

    def test_malformed_periods_are_counted_without_privacy_exclusion_and_basis_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root)
            bundle=load_collection(root)
            bundle['records'][0]['period_as_published']='January to December'
            bundle['records'][1]['period_as_published']='from November -0001'
            with patch('market_intelligence.rap_review.load_collection',return_value=bundle):
                reviewed,audit=evaluate(root,context())
            self.assertEqual(audit['counts']['malformed_periods'],2)
            self.assertEqual(audit['counts']['cleared_malformed_periods'],2)
            self.assertTrue(all(not d['reasons'] for d in audit['decisions']))
            self.assertTrue(all(d['identity_basis']=='organisation_vocabulary' for d in audit['decisions']))
            self.assertEqual(reviewed['records'][0]['period_as_published'],'January to December')
            self.assertEqual(reviewed['provenance']['privacy_partition']['malformed_periods'],2)

    def test_report_discloses_total_and_cleared_malformed_period_counts(self):
        from datetime import date
        from market_intelligence.research import build_rap
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);bundle=load_collection(root)
            bundle['records'][0]['period_as_published']='January to December'
            provenance=dict(privacy_partition=dict(quarantined=3,malformed_periods=2,cleared_malformed_periods=1))
            rows=[dict(payload=row,snapshot_id='fixture',provenance=provenance) for row in bundle['records']]
            engine=MagicMock();conn=engine.connect.return_value.execution_options.return_value.__enter__.return_value
            results=[MagicMock() for _ in range(6)]
            results[0].mappings.return_value=rows
            results[1].__iter__.return_value=iter([])
            results[2].mappings.return_value=[]
            results[3].mappings.return_value=[]
            results[4].mappings.return_value=[]
            results[5].scalar.return_value=None
            conn.execute.side_effect=results
            report=build_rap(engine,date(2026,11,20))
            self.assertEqual(report['summary']['privacy_exclusions'],3)
            self.assertEqual(report['summary']['malformed_periods'],2)
            self.assertEqual(report['summary']['cleared_malformed_periods'],1)

    def test_blocked_review_writes_reasons_without_promotable_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);ctx=context()
            rows=load_collection(root)['records']
            ctx['entities']=[entity(r['organisation'],individual=True) for r in rows]
            with patch('market_intelligence.rap_review.load_context',return_value=ctx):
                result=review(None,root,root/'bundle.json',ACCOUNT)
            self.assertEqual(result['status'],'blocked')
            self.assertIn('empty_cleared_subset',result['reasons'])
            self.assertFalse((root/'bundle.json').exists())
            self.assertTrue(audit_path(root/'bundle.json').exists())

    def test_service_import_requires_collection_even_with_valid_envelope(self):
        from market_intelligence.research import import_research
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);path=root/'bundle.json'
            with patch('market_intelligence.rap_review.load_context',return_value=context()):review(None,root,path,ACCOUNT)
            with self.assertRaises(SourceError):import_research(None,path)

    def test_structural_hash_decimal_runs_are_not_contact_text(self):
        from market_intelligence.research import import_research
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);collection(root);bundle=load_collection(root)
            bundle['records'][0]['record_key']='a'*20+'04123456781'+'b'*33
            bundle['privacy_review']=dict(reviewer_id=HUMAN,contains_personal_data=False,
                payload_sha256=digest({k:bundle[k] for k in ['records','provenance']}))
            path=root/'human-bundle.json';path.write_text(json.dumps(bundle))
            engine=MagicMock();engine.begin.return_value.__enter__.return_value.execute.return_value.scalar.return_value='existing-snapshot'
            self.assertEqual(import_research(engine,path)['status'],'unchanged')


if __name__=='__main__':unittest.main()

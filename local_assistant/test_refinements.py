import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from planning import rule_plan,validated_model_plan
from public_research import findings
import assistant


class RefinementTests(unittest.TestCase):
    def test_exclusion_stops_at_include(self):
        p=rule_plan('Show renewals, exclude assessments but include equipment')
        self.assertEqual(['assessments'],p['exclude'])

    def test_do_not_exclude_is_not_exclusion(self):
        self.assertEqual([],rule_plan('Show renewals, do not exclude equipment')['exclude'])

    def test_negated_cyber_is_not_positive_role(self):
        p=rule_plan('Show ICT renewals excluding cyber contracts')
        self.assertEqual([],p['roles'])
        self.assertEqual(['cyber'],p['exclude_roles'])

    def test_cybersecurity_and_ba_synonyms(self):
        self.assertEqual(['cyber'],rule_plan('Show cybersecurity renewals')['roles'])
        self.assertEqual(['analysis'],rule_plan('Show BA renewals')['roles'])
    def test_between_days(self):
        p=rule_plan('Cyber contracts expiring between 30 and 90 days')
        self.assertEqual((30,90),(p['start_days'],p['days']))

    def test_value_bounds(self):
        p=rule_plan('Cyber renewals above $100k and below AUD $1 million')
        self.assertEqual((100_000,1_000_000),(p['min_value'],p['max_value']))

    def test_abn(self):
        self.assertEqual(['13634409671'],rule_plan('What contracts does ABN 13 634 409 671 hold?')['abns'])

    def test_negated_expired(self):
        self.assertEqual('current',rule_plan('Show current panels for Hays, excluding expired memberships')['panel_status'])

    def test_open_is_not_planned(self):
        self.assertEqual('open',rule_plan('Show open cyber opportunities')['opportunity_status'])

    def test_clearance_and_location_not_fabricated(self):
        p=rule_plan('Show cyber opportunities for NV1 cleared candidates in Canberra')
        self.assertTrue(any('Clearance' in x for x in p['missing']))
        self.assertTrue(any('Delivery location' in x for x in p['missing']))

    def test_bad_model_structure(self):
        for value in [[],{'tasks':'company','entities':[]},{'tasks':[],'entities':[123]}]:
            with self.assertRaises(ValueError):
                validated_model_plan('Tell me about Acme',lambda *a:(json.dumps(value),{}),rule_plan('Tell me about Acme'))

    def test_role_phrase_is_not_entity(self):
        p=validated_model_plan('Cyber contracts held by Acme ending within 60 days',
            lambda *a:(json.dumps({'tasks':['renewals'],'entities':['Acme','cyber contracts','ending within 60 days']}),{}),
            rule_plan('Cyber contracts held by Acme ending within 60 days'))
        self.assertEqual(['Acme'],p['entities'])

    def test_unknown_entity_facts_only_does_not_broaden(self):
        with patch.object(assistant,'read_rows',return_value=[{'built_at':'fixture-build'}]):
            r=assistant.answer('Show cyber contracts held by AnUnknownCompany ending within 30 days',False,verify=False)
        self.assertFalse(r['facts'])
        self.assertTrue(any('broad' in x for x in r['missing']))

    def test_historical_research_period_is_labelled(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'r.json'
            path.write_text(json.dumps({'reviewed_at_utc':'2020-01-01T00:00:00+00:00','review_method':'manual',
                'records':[{'organisation':'Acme','kind':'RAP','title':'Old plan','period_end_year':2022}]}))
            r=findings(['Acme'],path=path)[0]
            self.assertTrue(r['period_status'].startswith('historical'))
            self.assertTrue(r['review_status'].startswith('needs recheck'))

    def test_missing_research_file(self):
        self.assertEqual([],findings(['Acme'],path=Path('nonexistent-research.json')))


if __name__=='__main__':unittest.main()

from datetime import date
import unittest

from market_intelligence.panel_access import eligibility,validate_facts,months_before,calendar_items


class PanelAccessTests(unittest.TestCase):
    def test_unknown_profile_is_never_eligible(self):
        facts={'requirements':[{'profile_key':'supply_nation_status','accepted_values':['Certified'],'label':'Certification'}]}
        self.assertEqual(eligibility(facts,{})['status'],'unknown')
        self.assertEqual(eligibility(facts,{'supply_nation_status':'pending'})['status'],'blocked')
        self.assertEqual(eligibility(facts,{'supply_nation_status':'Certified'})['status'],'requirements_met')
        self.assertEqual(eligibility({}, {})['status'],'unknown')

    def test_calendar_months_and_notice_dates(self):
        self.assertEqual(months_before(date(2028,5,31),3),date(2028,2,29))
        panel=dict(son_id='SON1',panel_name='Example',current_expiry=date(2027,5,31),source_url='https://example.invalid',
                   facts={'current_expiry':'2027-05-31'},evidence_url='https://example.invalid/evidence')
        rows=calendar_items(panel,date(2026,1,1))
        reminders=[r for r in rows if 'reminder' in r['event']]
        self.assertEqual([r['date'] for r in reminders],[date(2026,5,31),date(2026,11,30),date(2027,2,28)])
        self.assertEqual(next(r for r in rows if r['event']=='notice_expiry')['basis'],'published_notice; intake unknown')
        self.assertFalse(any('intake' in r['event'] for r in rows))

    def test_dates_require_valid_explicit_evidence(self):
        with self.assertRaises(ValueError):validate_facts({'current_expiry':'2027-02-30'})
        with self.assertRaises(ValueError):validate_facts({'current_expiry':'2027-01-01','max_possible_expiry':'2026-01-01'})
        with self.assertRaises(ValueError):validate_facts({'requirements':[{'profile_key':'anything'}]})


if __name__=='__main__':unittest.main()

"""TODAY list draft (spec engine 7): every item is owned, dated, evidenced and safe."""
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import classify  # noqa: E402
import today_list  # noqa: E402

TODAY = date(2026, 10, 7)


def renewal(cn, days, family='labour hire and recruitment', abn='18008476944', buyer='APRA', value=497887.5,
            email='buyer@apra.gov.au'):
    return dict(cn_id=cn, days_left=days, current_end_date=(TODAY + timedelta(days=days)).isoformat(),
                category_family=family, supplier_abn=abn, supplier='DXC', buyer=buyer, description='Contractor',
                current_value=value, supplier_on_mmr_list=1, contact_name='Thi Anna Ly', contact_email=email,
                page_url=f'https://www.tenders.gov.au/Cn/Show/{cn}', api_url='https://api')


PRIMES = [dict(organisation='DXC TECHNOLOGY AUSTRALIA PTY LIMITED', abn='18008476944', mmr_contracts=5,
               list_date='2026-10-07')]


class TodayTests(unittest.TestCase):
    def test_dxc_prime_action_hooks_on_the_best_timed_staffing_renewal(self):
        renewals = [renewal('CN4226826', 54), renewal('CN-LICENCE', 5, family='ICT equipment and general', value=9e6),
                    renewal('CN-SOON', 3)]
        [action] = today_list.prime_actions(renewals, PRIMES, TODAY)
        self.assertEqual(action['owner'], 'Lane')
        self.assertEqual(action['contact_safety'], 'SAFE_GENERAL')
        self.assertIn('CN4226826', action['evidence'])            # 54 days beats 3 days; licences don't count
        self.assertIn('5 active MMR contract(s)', action['why_now'])
        self.assertIn('no bench data', action['assumptions'])

    def test_live_items_are_formal_channel_only(self):
        closes = (datetime.now(classify.CANBERRA) + timedelta(days=4)).isoformat()
        opportunity = dict(status_effective='Open', is_relevant=1, closes_at=closes, closes='soon', role_families='cyber',
                           source='BuyICT', kind='RFQ', title='AI Engineer', source_id='LH-08047', buyer='Treasury',
                           url='https://buyict/LH-08047')
        [action] = today_list.live_actions([opportunity], TODAY)
        self.assertEqual(action['contact_safety'], 'FORMAL_CHANNEL_ONLY')
        self.assertIn('formal Q&A only', action['person_and_route'])

    def test_every_today_item_is_owned_dated_and_evidenced(self):
        exemption = [dict(buyer='APRA', category_family='labour hire and recruitment', first_published='2026-05-01')] * 4
        plans = [dict(expected_from='2026-11-01', expected_to='2026-12-31', title='eDiscovery platform',
                      buyer='AGD', contact_name=None, contact_email='x@ag.gov.au', contact_phone=None,
                      role_families=None, url='https://plan')]
        actions, picked = today_list.build([renewal('CN4226826', 54)], PRIMES, exemption, plans, [], TODAY)
        self.assertTrue(picked)
        self.assertEqual({a['action_type'] for a in picked},
                         {'Tier-2 offer to prime', 'Capability introduction to agency',
                          'Register interest in planned procurement'})
        for a in picked:
            self.assertTrue(a['owner'] and a['due'] and a['evidence'] and a['contact_safety'])
            self.assertGreaterEqual(date.fromisoformat(a['due']), TODAY)

    def test_scores_follow_the_configured_thresholds(self):
        self.assertEqual(today_list.score({k: 1.0 for k in classify.config('scoring')['weights']}), (100, 'ACT NOW'))
        self.assertEqual(today_list.score({k: 0.0 for k in classify.config('scoring')['weights']})[1], 'IGNORE')


if __name__ == '__main__':
    unittest.main()

import unittest
from market_intelligence.crm import reconcile


class CrmExportTests(unittest.TestCase):
    def test_relationships_duplicates_and_per_lane_limits(self):
        rows=[dict(account_key='a',lane='federal'),dict(account_key='a',lane='federal'),dict(account_key='b',lane='federal'),
              dict(account_key='c',lane='private'),dict(account_key='d',lane='private')]
        relationships={'a':dict(relationship_status='active',last_activity_date='2026-11-19'),
                       'c':dict(relationship_status='do_not_pursue')}
        out,excluded=reconcile(rows,relationships,1)
        self.assertEqual([r['account_key'] for r in out],['a','d']);self.assertEqual(excluded,1)
        self.assertFalse(out[0]['new_target']);self.assertEqual(out[0]['action'],'continue existing relationship')
        self.assertTrue(out[1]['new_target'])


if __name__=='__main__':unittest.main()

import unittest
from verification import latest_fields,verify_record,fetch_contract,OfficialRedirects


def package(amount='100.00',end='2026-10-09T13:00:00Z',amendment=0):
    return {'releases':[{'date':'2026-10-08T01:00:00Z','parties':[
        {'name':'Buyer','roles':['procuringEntity']},
        {'name':'Supplier','roles':['supplier'],'additionalIdentifiers':[{'scheme':'AU-ABN','id':'123'}]}],
        'contracts':[{'id':'CN123','description':'Cyber analyst','period':{'endDate':end},
                      'amendments':[{'id':f'CN123-A{amendment}'}],
                      'value':{'amount':amount,'currency':'AUD'}}]}]}


class VerificationTests(unittest.TestCase):
    def test_canberra_date_not_utc_date(self):
        self.assertEqual(latest_fields(package(),'CN123')['current_end_date'],'2026-10-10')

    def test_latest_amendment_wins_over_release_order(self):
        p=package(amendment=2)
        p['releases']+=package(amount='10',amendment=0)['releases']
        self.assertEqual(latest_fields(p,'CN123')['current_value'],100)

    def test_wrong_cn_not_verified(self):
        with self.assertRaises(ValueError):latest_fields(package(),'CN999')

    def test_fields_match_without_hiring_claim(self):
        r=verify_record({'cn_id':'CN123','supplier':'Supplier','current_value':100},lambda cn:(package(),'hash'))
        self.assertEqual(r['status'],'matched')
        self.assertIn('does not verify hiring',r['proof_scope'])

    def test_mismatch_exposed(self):
        r=verify_record({'cn_id':'CN123','current_value':101},lambda cn:(package(),'hash'))
        self.assertEqual(r['status'],'mismatch')
        self.assertEqual(r['mismatches'][0]['field'],'current_value')

    def test_missing_not_match(self):
        r=verify_record({'cn_id':'CN123','current_value':None},lambda cn:(package(),'hash'))
        self.assertEqual(r['status'],'mismatch')

    def test_source_down_not_absence(self):
        def down(cn):raise TimeoutError('source timeout')
        r=verify_record({'cn_id':'CN123'},down)
        self.assertEqual(r['status'],'unavailable')

    def test_url_input_rejected_before_network(self):
        with self.assertRaises(ValueError):fetch_contract('http://localhost/secret')

    def test_cross_host_redirect_rejected(self):
        with self.assertRaises(ValueError):
            OfficialRedirects().redirect_request(None,None,302,'',{},'https://localhost/secret')


if __name__=='__main__':unittest.main()

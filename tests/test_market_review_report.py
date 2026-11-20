from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from market_intelligence.review_report import write_queue


def pending(name='Example Agency',status='exact_abn',stale=False,recheck=False):
    return dict(buyer_name=name,buyer_abn='51824753556',buyer_key=name,
                match_status=status,review_status='needs_recheck' if recheck else 'unreviewed',
                register_context_stale=stale,organisation_id='gov:O-000001',
                evidence='valid_abn_unique_in_current_register',candidate_ids=['gov:O-000001'],
                candidate_evidence=[dict(name='Official Agency',organisation_id='gov:O-000001',
                    abn='51824753556',source='agor',snapshot_id='example-snapshot',
                    source_url='https://example.invalid/register')])


class IdentityReviewReportTests(unittest.TestCase):
    def render(self,rows):
        queue=dict(rows=rows,limitations=['Human evidence review required'],proposal_generation={'id':'example'})
        original=deepcopy(queue)
        with tempfile.TemporaryDirectory() as folder:
            result=write_queue(queue,folder)
            page=Path(result['html']).read_text(encoding='utf-8')
            data=json.loads(Path(result['json']).read_text(encoding='utf-8'))
        self.assertEqual(original,queue)
        return page,data

    def test_recheck_stale_and_conflicts_precede_exact_proposals(self):
        rows=[pending('A exact'),pending('B conflict','conflict'),
              pending('C stale',stale=True),pending('D recheck',recheck=True)]
        page,_=self.render(rows)
        positions=[page.index('<h2>'+name) for name in ['D recheck','C stale','B conflict','A exact']]
        self.assertEqual(positions,sorted(positions))
        self.assertIn('rebuild proposals',page)

    def test_complete_evidence_remains_in_json(self):
        page,data=self.render([pending()])
        self.assertEqual(data['rows'][0]['candidate_evidence'][0]['snapshot_id'],'example-snapshot')
        self.assertEqual(data['proposal_generation']['id'],'example')
        self.assertIn('51824753556',page)
        self.assertIn('gov:O-000001',page)
        self.assertNotIn('<form',page)

    def test_escape_buyer_candidate_and_unsafe_url(self):
        row=pending('<script>buyer</script>')
        row['candidate_evidence'][0].update(name='<script>candidate</script>',source_url='javascript:alert(1)')
        page,_=self.render([row])
        self.assertNotIn('<script>',page)
        self.assertNotIn('href="javascript:',page)
        self.assertIn('&lt;script&gt;',page)

    def test_empty_queue_and_missing_candidates(self):
        page,_=self.render([])
        self.assertIn('No pending identities',page)
        row=pending();row['candidate_evidence']=[];row['organisation_id']=None;row['buyer_abn']=None
        page,_=self.render([row])
        self.assertIn('No supported register candidate',page)
        self.assertIn('Unresolved',page)


if __name__=='__main__':unittest.main()

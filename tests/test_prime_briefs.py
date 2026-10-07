"""Brief claims must be traceable and unknown delivery/contact facts stay unknown."""
import tempfile
import unittest
from pathlib import Path

import prime_briefs as pb


class PrimeBriefTests(unittest.TestCase):
    def fixture(self):
        action = dict(organisation='DXC Technology (group)', action_type='Tier-2 offer to prime',
                      priority='ACT NOW', score=86, owner='Lane', due='2026-10-09', contact_safety='SAFE_GENERAL',
                      why_now='Hook: APRA CN4226826 ends 2026-11-30 (54 days)',
                      evidence='https://www.tenders.gov.au/Cn/Show/apra',
                      score_parts='timing 1.0', assumptions='Delivery readiness neutral; MMR unconfirmed')
        primes = [dict(organisation='DXC Australia', group_name=action['organisation'], abn='18008476944',
                       mmr_contracts=5, list_date='2026-02-25', match='alias', match_review='confirmed'),
                  dict(organisation='DXC Enterprise', group_name=action['organisation'], abn='11111111111',
                       mmr_contracts=1, list_date='2026-02-25', match='name', match_review='unreviewed')]
        candidate = dict(cn_id='CN4262189', supplier_abn='18008476944', buyer='DFAT', description='IT Support',
                         current_value=32197728.73, category_family='ICT equipment and general',
                         current_end_date='2029-06-30', days_left=997,
                         page_url='https://www.tenders.gov.au/Cn/Show/dfat')
        renewal = dict(candidate, cn_id='CN4226826', buyer='APRA', current_end_date='2026-11-30', days_left=54,
                       category_family='labour hire and recruitment', page_url=action['evidence'])
        rate = dict(cn_id='CN4259794', role_title='Senior Business Analyst', estimated_day_rate=1188,
                    page_url='https://www.tenders.gov.au/Cn/Show/rate')
        return [action], primes, [candidate], [renewal], [], [rate]

    def test_dxc_group_sources_nine_steps_and_unknowns(self):
        brief = pb.build(*self.fixture())[0]
        self.assertIn('6 MMR contracts', brief['why'])
        self.assertIn('2026-02-25', brief['why'])
        self.assertIn('CN4262189', brief['steps'])
        self.assertIn('equipment and general — check', brief['steps'])
        self.assertIn('CN4226826', brief['offer'])
        self.assertIn('pending: engine 6', brief['steps'])
        self.assertIn('pending: bench', brief['steps'])
        for i in range(1, 10):
            self.assertRegex(brief['steps'], rf'(?m)^{i} ')
        self.assertIn('Hypothetical', brief['illustrative_maths'])
        self.assertIn('$1,287,909.15', brief['illustrative_maths'])
        self.assertIn('$261,360.00', brief['illustrative_maths'])
        self.assertIn('estimate', brief['illustrative_maths'])
        self.assertIn('https://www.tenders.gov.au/Cn/Show/rate', brief['source_links'])

    def test_multiple_abn_matches_do_not_inflate_list_count(self):
        args = self.fixture()
        args[1].append(dict(args[1][0], abn='22222222222'))
        self.assertIn('6 MMR contracts', pb.build(*args)[0]['why'])

    def test_no_samples_no_invented_rate_or_person(self):
        args = self.fixture()
        brief = pb.build(*args[:-1], [])[0]
        self.assertIn('estimate unavailable', brief['illustrative_maths'])
        self.assertIn('no verified prime contact', brief['steps'])
        self.assertIn('to confirm against the bench', brief['offer'])

    def test_rate_contracts_are_not_double_counted(self):
        args = self.fixture()
        args[-1].extend([args[-1][0], dict(args[-1][0], cn_id='CN2', estimated_day_rate=2000)])
        brief = pb.build(*args)[0]
        self.assertIn('median analysis rate $1,594.00', brief['illustrative_maths'])
        self.assertIn('2 linked analysis contracts', brief['illustrative_maths'])

    def test_unclassified_role_title_is_not_an_analysis_rate(self):
        args = self.fixture()
        args[-1][0]['role_title'] = 'Unspecified'
        self.assertIn('estimate unavailable', pb.build(*args)[0]['illustrative_maths'])

    def test_render_escapes_data_and_export_removes_only_generated_stale_files(self):
        brief = pb.build(*self.fixture())[0]
        brief['organisation'] = '<script>alert(1)</script>'
        page = pb.render(brief)
        self.assertNotIn('<script>', page)
        self.assertIn('href="https://www.tenders.gov.au/Cn/Show/dfat"', page)
        self.assertEqual(page.count('<tr>'), 9)
        with tempfile.TemporaryDirectory() as directory:
            pb.export([brief], directory)
            target = Path(directory) / brief['html_file']
            self.assertTrue(target.exists())
            personal = target.parent / 'notes-abc123.html'
            personal.write_text('keep')
            pb.export([], directory)
            self.assertFalse(target.exists())
            self.assertTrue(personal.exists())

    def test_closed_actions_excluded_and_top_ten_only(self):
        args = self.fixture()
        args[0][0]['stage'] = 'won'
        self.assertEqual(pb.build(*args), [])
        actions = [dict(args[0][0], stage='identified', organisation=f'Prime {i}', score=i) for i in range(12)]
        briefs = pb.build(actions, *args[1:])
        self.assertEqual(len(briefs), 10)
        self.assertEqual(briefs[0]['score'], 11)

"""Relevance, real status and contact safety (Illoura spec 3.1, 3.3, section 8), on the spec's real examples."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import classify  # noqa: E402


class RelevanceTests(unittest.TestCase):
    def test_spec_act_titles(self):
        cases = {
            'Window cleaning services (Canberra Health Services)': 0,
            'ICMS Replacement Design and Discovery RFI (Justice)': 1,
            'Computer Aided Dispatch and E000 Telephony RFI (Justice)': 1,
            'Private and Public Cloud Management Services ATN (Digital Canberra)': 1,
            'Traffic Management Services for Events ACT': 0,
            'Oracle CRM System for CIT': 1,
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(classify.classify(title)['is_relevant'], expected)

    def test_every_result_says_why(self):
        result = classify.classify('Cyber Authorisation Process Reform solution')
        self.assertEqual((result['vertical'], result['is_relevant']), ('ICT', 1))
        self.assertIn('Cyber', result['relevance_reason'])

    def test_one_weak_word_is_unclear_not_relevant(self):
        result = classify.classify('Establishing pigeon pea as a summer pulse crop: farming system trials')
        self.assertEqual((result['is_relevant'], result['needs_review']), (0, 1))
        self.assertEqual(classify.classify('Digital platform for grants')['is_relevant'], 1)

    def test_description_does_not_make_a_title_relevant(self):
        result = classify.classify('Demolition of dwellings', description='data system platform software')
        self.assertEqual(result['is_relevant'], 0)

    def test_exclude_keyword_overrides(self):
        result = classify.classify('Head contractor for the new ICT building')
        self.assertEqual(result['is_relevant'], 0)
        self.assertIn('excluded', result['relevance_reason'])

    def test_category_code_and_name(self):
        self.assertEqual(classify.classify('Sustainment', category='81110000 - Computer services')['category_family'],
                         'ICT services')
        self.assertEqual(classify.classify('Routers', category='Information and technology')['is_relevant'], 1)
        self.assertEqual(classify.family('43200000'), 'ICT equipment and general')
        self.assertEqual(classify.family('80111600'), 'labour hire and recruitment')
        self.assertIsNone(classify.family('72100000'))

    def test_roles(self):
        result = classify.classify('Senior Business Analyst')
        self.assertEqual((result['is_relevant'], result['role_families']), (1, 'analysis'))
        self.assertEqual(classify.classify('Penetration Testing Services')['role_families'], 'cyber')
        self.assertIsNone(classify.classify('Upgrade of the Automatic Weather Station (AWS) network')['role_families'])

    def test_buyict_is_always_ict(self):
        self.assertEqual(classify.classify('Change Manager', source_is_ict=True)['relevance_reason'],
                         'ICT marketplace (BuyICT)')


class StatusTests(unittest.TestCase):
    NOW = datetime(2026, 10, 7, 12, 0, tzinfo=classify.CANBERRA)

    def test_closing_formats(self):
        expected = datetime(2026, 10, 22, 23, 59, tzinfo=classify.CANBERRA)
        for text in ('Thursday, 22 October 2026 – 11:59pm, Canberra time',
                     'Thursday, 22 October 2026 • 11:59pm, Canberra time', '22-Oct-2026 11:59 pm'):
            with self.subTest(text=text):
                self.assertEqual(classify.closing_time(text), expected)
        self.assertEqual(classify.closing_time('Tue, 03 November 2026 2:00 pm').hour, 14)
        self.assertEqual(classify.closing_time('21-May-2026').hour, 23)  # a date alone closes at day end
        self.assertIsNone(classify.closing_time('to be advised'))

    def test_past_closing_time_is_closed_whatever_the_portal_says(self):
        # PICI0008792, Oracle CRM System for CIT: shown Open by the portal, closed 21 May 2026.
        self.assertEqual(classify.effective_status('Open', 'Thu, 21 May 2026 2:00 pm', self.NOW), 'Closed')
        self.assertEqual(classify.effective_status('Open', '3-Nov-2026 3:00 pm', self.NOW), 'Open')
        self.assertEqual(classify.effective_status('Awarded', None, self.NOW), 'Closed')

    def test_contact_safety(self):
        self.assertEqual(classify.contact_safety('Open'), 'FORMAL_CHANNEL_ONLY')
        self.assertEqual(classify.contact_safety('Closed'), 'SAFE_GENERAL')
        self.assertEqual(classify.contact_safety('Open', opted_out=True), 'NO_CONTACT')


if __name__ == '__main__':
    unittest.main()

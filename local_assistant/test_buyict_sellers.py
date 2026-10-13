import unittest
import buyict_sellers as buyict


class CleaningTests(unittest.TestCase):
    def test_abn_needs_eleven_digits(self):
        self.assertEqual(buyict.clean_abn('ABN: 77 639 679 020'), '77639679020')
        self.assertEqual(buyict.clean_abn('ABN: 1234'), '')
        self.assertEqual(buyict.clean_abn(None), '')

    def test_website_becomes_https_and_rejects_non_addresses(self):
        self.assertEqual(buyict.clean_website('http://www.example.com.au'), 'https://www.example.com.au/')
        self.assertEqual(buyict.clean_website('example.com.au/contact/'), 'https://example.com.au/contact')
        self.assertEqual(buyict.clean_website('kpmg.com/au/en/home.html'), 'https://kpmg.com/au/en/home.html')
        for value in ('', None, 'info@example.com.au', 'see our website', 'localhost'):
            self.assertEqual(buyict.clean_website(value), '')


class CataloguePagingTests(unittest.TestCase):
    def test_reads_every_page_and_returns_the_profile_widget(self):
        sellers = [{'sys_id': f's{i}', 'abn': 'ABN: 77639679020'} for i in range(250)]

        class FakePortal:
            portal = 'p'
            def __init__(self):
                self.posts = []
            def get(self, url):
                class Page:
                    def json(self_inner):
                        widget = {'id': 'seller_catalogue', 'data': {'seller_catalogue_profile_modal': {'sys_id': 'modal'},
                                  'all_mps': [{'sys_id': 'all_marketplaces'}]}}
                        return {'result': {'containers': [{'widgets': [{'instance_id': 'rect', 'widget': widget}]}]}}
                return Page()
            def post(self, url, body):
                tab = dict(next(m for m in body['all_mps'] if m['sys_id'] == 'all_marketplaces'))
                start = tab.get('window_start', 0)
                self.posts.append(start)
                tab.update(num_results=len(sellers), seller_cards=sellers[start:start + 100])
                return {'data': dict(body, all_mps=[tab])}

        portal = FakePortal()
        cards, modal = buyict.catalogue(portal)
        self.assertEqual(modal, 'modal')
        self.assertEqual(portal.posts, [0, 100, 200])
        self.assertEqual(len(cards), 250)

    def test_schema_has_no_personal_contact_columns(self):
        for column in ('email', 'phone', 'person', 'contact_name'):
            self.assertNotIn(column, buyict.SCHEMA)


if __name__ == '__main__':
    unittest.main()

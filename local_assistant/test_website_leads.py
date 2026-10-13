import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import buyict_sellers, company_coverage, contact_discovery, website_leads as leads


class Response:
    def __init__(self, status=200, headers=None, text='', payload=None):
        self.status_code, self.headers, self.text, self.content = status, headers or {}, text, text.encode()
        self.payload = payload
    def json(self):
        return self.payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def make_databases(root):
    with closing(sqlite3.connect(root / 'intelligence.db')) as db:
        db.execute('CREATE TABLE mmr_primes (abn)')
        db.execute("INSERT INTO mmr_primes VALUES ('11111111111')")
        db.commit()
    with closing(sqlite3.connect(root / 'contract_api.db')) as db:
        db.execute('CREATE TABLE contracts (supplier_abn, current_end_date, category_family)')
        db.executemany('INSERT INTO contracts VALUES (?, ?, ?)', [('22222222222', '2099-01-01', 'ICT services'),
                                                                   ('33333333333', '2099-01-01', 'ICT services'),
                                                                   ('44444444444', '2099-01-01', 'ICT services')])
        db.commit()
    with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
        db.executescript(company_coverage.SCHEMA + buyict_sellers.SCHEMA + leads.SCHEMA)
        for abn, name in (('11111111111', 'TELCO GIANT LIMITED'), ('22222222222', 'Acme Analytics Pty Ltd'),
                          ('33333333333', 'Quiet Co Pty Ltd'), ('44444444444', 'On BuyICT Pty Ltd')):
            db.execute('INSERT INTO suppliers (supplier_key, name, abn) VALUES (?, ?, ?)', ('ABN:' + abn, name, abn))
        db.execute("""INSERT INTO buyict_sellers (sys_id, name, legal_name, abn, website, source_url) VALUES
            ('s1', 'Telco Giant', 'Telco Giant Group Pty Ltd', '99999999999', 'https://telcogiant.com.au/', 'https://buyict/s1'),
            ('s2', 'On BuyICT', 'On BuyICT Pty Ltd', '44444444444', 'https://onbuyict.com.au/', 'https://buyict/s2')""")
        db.commit()


class HelperTests(unittest.TestCase):
    def test_stem_drops_legal_words(self):
        self.assertEqual(leads.stem('HAYS SPECIALIST RECRUITMENT (AUSTRALIA) PTY LTD'), 'hays specialist')
        self.assertEqual(leads.stem('The Trustee for Projects Assured Trust'), 'projects assured')

    def test_directories_and_social_sites_are_not_candidates(self):
        results = [{'url': 'https://abr.business.gov.au/ABN/View?abn=1'}, {'url': 'https://au.linkedin.com/company/acme'},
                   {'url': 'https://www.acme.com.au/about'}, {'url': 'https://acme.com.au/contact'},
                   {'url': 'https://www.yellowpages.com.au/acme'}, {'url': 'https://acmegroup.com/'}]
        self.assertEqual(leads.candidate_sites(results), ['https://www.acme.com.au/', 'https://acmegroup.com/'])


class SourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        make_databases(self.root)
        patcher = patch.object(leads.assistant, 'DB_DIR', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def connect(self):
        return closing(sqlite3.connect((self.root / 'company_contacts.db').resolve().as_uri(), uri=True))

    def test_gap_skips_buyict_websites_and_puts_mmr_first(self):
        with self.connect() as db:
            self.assertEqual([row[0] for row in leads.gap(db)], ['11111111111', '22222222222', '33333333333'])

    def test_wikidata_prefers_australian_site_and_only_known_abns(self):
        payload = {'results': {'bindings': [
            {'item': {'value': 'Q1'}, 'abn': {'value': '22 222 222 222'}, 'site': {'value': 'https://acme.co.nz'}},
            {'item': {'value': 'Q1'}, 'abn': {'value': '22 222 222 222'}, 'site': {'value': 'https://acme.com.au'}},
            {'item': {'value': 'Q2'}, 'abn': {'value': '55555555555'}, 'site': {'value': 'https://other.com.au'}},
            {'item': {'value': 'Q3'}, 'abn': {'value': '44444444444'}, 'site': {'value': 'https://onbuyict.com.au'}}]}}
        class Session:
            def get(self, url, **kwargs):
                return Response(payload=payload)
        with self.connect() as db:
            self.assertEqual(leads.wikidata(db, Session()), 1)
            rows = db.execute('SELECT abn, website, source, strength FROM website_leads').fetchall()
        self.assertEqual(rows, [('22222222222', 'https://acme.com.au/', 'wikidata', 1)])

    def test_related_seller_links_same_name_other_abn(self):
        with self.connect() as db:
            self.assertEqual(leads.related_sellers(db), 1)
            abn, website, identity, strength = db.execute('SELECT abn, website, identity, strength FROM website_leads').fetchone()
        self.assertEqual((abn, website, strength), ('11111111111', 'https://telcogiant.com.au/', 3))
        self.assertIn('99999999999', identity)

    def test_search_keeps_only_checked_sites_and_logs_every_supplier(self):
        results = {'11111111111': [{'url': 'https://www.linkedin.com/company/x'}, {'url': 'https://telcogiant.com.au/'}],
                   '22222222222': [{'url': 'https://wrong.com.au/'}, {'url': 'https://acme.com.au/'}],
                   '33333333333': []}
        class Session:
            def get(self, url, params=None, **kwargs):
                abn = params['q'].split()[-1]
                return Response(payload={'web': {'results': results[abn]}})
        def fake_verify(site, abn, name):
            if site == 'https://acme.com.au/':
                return 'ABN shown on website', site + 'privacy'
            if site == 'https://telcogiant.com.au/':
                return 'Name shown on website; ABN not found (name match only)', site
            return None, ''
        with self.connect() as db, patch.object(leads, 'verify_site', fake_verify), patch.object(leads.time, 'sleep'):
            self.assertEqual(leads.search(db, 10, 'key', Session()), 3)
            stored = dict(db.execute('SELECT abn, strength FROM website_leads'))
            log = dict(db.execute('SELECT abn, outcome FROM website_search_log'))
            self.assertEqual(leads.search(db, 10, 'key', Session()), 0)  # nothing is searched twice
        self.assertEqual(stored, {'11111111111': 4, '22222222222': 2})
        self.assertEqual(log['33333333333'], 'no company site in results')


class VerifyTests(unittest.TestCase):
    def test_abn_found_on_privacy_page(self):
        pages = {'https://acme.com.au/robots.txt': Response(404),
                 'https://acme.com.au/': Response(200, {'Content-Type': 'text/html'},
                     '<a href="/blog">Blog</a><a href="/privacy-policy">Privacy</a><p>Acme Analytics</p>'),
                 'https://acme.com.au/privacy-policy': Response(200, {'Content-Type': 'text/html'}, '<p>ABN 22 222 222 222</p>')}
        class Session:
            def get(self, url, **kwargs):
                return pages[url]
        with patch.object(contact_discovery, 'safe_url', side_effect=lambda url, allowed: url), patch.object(leads.time, 'sleep'):
            found = leads.verify_site('https://acme.com.au/', '22222222222', 'Acme Analytics Pty Ltd', Session())
            pages['https://acme.com.au/privacy-policy'] = Response(200, {'Content-Type': 'text/html'}, '<p>No number</p>')
            name_only = leads.verify_site('https://acme.com.au/', '22222222222', 'Acme Analytics Pty Ltd', Session())
            nothing = leads.verify_site('https://acme.com.au/', '22222222222', 'Different Name Pty Ltd', Session())
        self.assertEqual(found, ('ABN shown on website', 'https://acme.com.au/privacy-policy'))
        self.assertEqual(name_only[0], 'Name shown on website; ABN not found (name match only)')
        self.assertEqual(nothing, (None, ''))


if __name__ == '__main__':
    unittest.main()

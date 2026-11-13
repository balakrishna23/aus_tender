import unittest
from unittest.mock import patch
import contact_discovery as contacts


class ContactDiscoveryTests(unittest.TestCase):
    def test_priority_research_excludes_expired_and_equipment_only_suppliers(self):
        import tempfile,sqlite3
        from pathlib import Path
        from contextlib import closing
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with closing(sqlite3.connect(root/'contract_api.db')) as db:
                db.execute('CREATE TABLE contracts(supplier_abn,current_end_date,category_family)')
                db.executemany('INSERT INTO contracts VALUES (?,?,?)',[
                    ('1','2027-01-01','ICT services'),('2','2025-01-01','labour hire and recruitment'),
                    ('3','2027-01-01','ICT equipment and general')])
                db.commit()
            self.assertEqual(contacts.priority_supplier_keys(root,'2026-10-24'),{'ABN:1'})

    def test_page_redirect_cannot_reuse_another_hosts_robots_permission(self):
        from urllib.robotparser import RobotFileParser
        robot=RobotFileParser('https://data3.com.au/robots.txt')
        robot.parse(['User-agent: *','Allow: /'])
        class Session:
            def __init__(self):self.visits=[]
            def get(self,url,**kwargs):
                self.visits.append(url)
                return type('Response',(),{'status_code':301,'headers':{'Location':'https://www.data3.com/contact'}})()
        session=Session()
        with patch.object(contacts,'safe_url',side_effect=lambda url,allowed:url):
            with self.assertRaisesRegex(ValueError,'own robots review'):
                contacts.get_public(session,'https://data3.com.au/contact',{'data3.com.au'},robot)
        self.assertEqual(session.visits,['https://data3.com.au/contact'])

    def test_form_examples_and_embedded_templates_are_not_contacts(self):
        parser = contacts.ContactHTML()
        parser.feed('''<input placeholder="jane@agency.gov.au">
            <input placeholder="+61 400 000 000">
            <template><a href="mailto:fake@example.com">example</a></template>
            <a href="mailto:enquiries@business.com.au?subject=Hello">Email</a>
            <a href="tel:+61262300233">Call</a>''')
        self.assertEqual(parser.mail, ['enquiries@business.com.au'])
        self.assertEqual(parser.phone, ['+61262300233'])

    def test_text_contacts_keep_business_lines_and_drop_fax_mobile_and_images(self):
        mails, phones = contacts.text_contacts(
            'Call (02) 6230 0233 or 1300 123 456. Fax: 02 6230 0299. Mobile 0412 345 678. '
            'ABN 12 345 678 901. Email info@business.com.au. Logo logo@2x.png')
        self.assertEqual(mails, ['info@business.com.au'])
        self.assertEqual(phones, ['(02) 6230 0233', '1300 123 456'])

    def test_purpose_separates_company_channels_from_individuals(self):
        site = 'business.com.au'
        cases = {('email', 'enquiries@business.com.au'): 'functional business mailbox',
                 ('email', 'DFPAdelaide@business.com.au'): 'functional business mailbox',
                 ('email', 'sales.team@business.com.au'): 'functional business mailbox',
                 ('email', 'christopher.smith@business.com.au'): 'named individual: confirm role on source page',
                 ('email', 'ismail.rosales@business.com.au'): 'named individual: confirm role on source page',
                 ('email', 'privacy@business.com.au'): 'privacy/legal mailbox',
                 ('email', 'someone@business.ca'): 'email domain differs from company website',
                 ('email', 'kkeenan@mail.business.com.au'): 'unclear mailbox: review',
                 ('phone', '(02) 6230 0233'): 'Australian business line',
                 ('phone', '+61262300233'): 'Australian business line',
                 ('phone', '1300 123 456'): 'Australian business line',
                 ('phone', '0412 345 678'): 'mobile: confirm it is a business line',
                 ('phone', '+1 571 814 7801'): 'non-Australian or unrecognised number',
                 ('phone', '+61 (08) 9221 3300'): 'Australian business line',
                 ('phone', '+61 (0) 7 3243 3000'): 'Australian business line',
                 ('phone', '+64 (0) 4 499 1200'): 'non-Australian or unrecognised number',
                 ('phone', '+0415737080'): 'mobile: confirm it is a business line',
                 ('email', 'businessmarketing@business.com.au'): 'functional business mailbox',
                 ('email', 'Requests@business.com.au'): 'functional business mailbox',
                 ('email', 'caleb@business.com.au'): 'unclear mailbox: review'}
        for (kind, value), expected in cases.items():
            self.assertEqual(contacts.purpose(kind, value, site), expected, value)

    def test_redirects_only_to_the_same_brand(self):
        self.assertEqual(contacts.brand('www.data3.com.au'), 'data3')
        allowed = {'data3.com.au'}
        self.assertEqual(contacts.follow('https://data3.com.au/', 'https://www.data3.com/', allowed), 'https://www.data3.com/')
        self.assertEqual(allowed, {'data3.com.au', 'data3.com'})
        self.assertEqual(contacts.follow('https://fujitsu.com/au', 'https://global.fujitsu/en-au', {'fujitsu.com'}),
                         'https://global.fujitsu/en-au')
        self.assertEqual(contacts.follow('https://planittesting.com/', 'https://planit.com/', {'planittesting.com'}),
                         'https://planit.com/')
        for old, new in (('oldname.com.au', 'parking-service.com'), ('altair.com', 'siemens.com'), ('app.com.au', 'apple.com')):
            with self.assertRaises(ValueError):
                contacts.follow(f'https://{old}/', f'https://{new}/', {old})

    def test_contact_pages_are_visited_before_about_pages(self):
        links = ['https://b.com.au/about-us/charity', 'https://b.com.au/our-team', 'https://b.com.au/contact-us',
                 'https://b.com.au/offices']
        self.assertEqual(sorted(links, key=contacts.link_rank),
                         ['https://b.com.au/contact-us', 'https://b.com.au/offices', 'https://b.com.au/our-team',
                          'https://b.com.au/about-us/charity'])

    def test_text_is_read_on_contact_pages_but_not_team_pages(self):
        pages = {
            'https://business.com.au/robots.txt': (301, {'Location': 'https://www.business.com.au/robots.txt'}, ''),
            'https://www.business.com.au/robots.txt': (200, {}, 'User-agent: *\nAllow: /'),
            'https://business.com.au/': (200, {'Content-Type': 'text/html'},
                                         '<a href="/team">Team</a><a href="/contact">Contact</a>'),
            'https://business.com.au/contact': (200, {'Content-Type': 'text/html'}, '<p>Phone (02) 6230 0233</p>'),
            'https://business.com.au/team': (200, {'Content-Type': 'text/html'}, '<p>jane.citizen@business.com.au</p>'),
        }
        class Response:
            def __init__(self, status, headers, text):
                self.status_code, self.headers, self.text, self.content = status, headers, text, text.encode()
        class Session:
            def get(self, url, **kwargs):
                return Response(*pages[url])
        with patch.object(contacts, 'safe_url', side_effect=lambda url, allowed: url), patch.object(contacts.time, 'sleep'):
            found, events = contacts.discover('https://business.com.au/', Session())
        self.assertEqual([(f['kind'], f['value'], f['locator']) for f in found],
                         [('phone', '(02) 6230 0233', 'published phone text')])
        self.assertEqual([e['url'] for e in events],
                         ['https://business.com.au/', 'https://business.com.au/contact', 'https://business.com.au/team'])

    def test_contact_link_found_by_label_or_sitemap(self):
        sitemap = ('<sitemapindex><sitemap><loc>https://shop.com.au/page-sitemap.xml</loc></sitemap></sitemapindex>')
        pages = {
            'https://label.com.au/robots.txt': (200, {}, 'User-agent: *\nAllow: /'),
            'https://label.com.au/': (200, {'Content-Type': 'text/html'}, '<a href="/get-started">Contact us</a><a href="/blog">Blog</a>'),
            'https://label.com.au/get-started': (200, {'Content-Type': 'text/html'}, '<p>Call 1300 123 456</p>'),
            'https://shop.com.au/robots.txt': (200, {}, 'User-agent: *\nAllow: /\nSitemap: https://shop.com.au/sitemap.xml'),
            'https://shop.com.au/': (200, {'Content-Type': 'text/html'}, '<p>Welcome</p>'),
            'https://shop.com.au/sitemap.xml': (200, {'Content-Type': 'application/xml'}, sitemap),
            'https://shop.com.au/page-sitemap.xml': (200, {'Content-Type': 'application/xml'},
                '<urlset><url><loc>https://shop.com.au/reach-us/contact</loc></url><url><loc>https://shop.com.au/blog</loc></url></urlset>'),
            'https://shop.com.au/reach-us/contact': (200, {'Content-Type': 'text/html'}, '<p>(02) 6230 0233</p>'),
        }
        class Response:
            def __init__(self, status, headers, text):
                self.status_code, self.headers, self.text, self.content = status, headers, text, text.encode()
        class Session:
            def get(self, url, **kwargs):
                return Response(*pages[url])
        with patch.object(contacts, 'safe_url', side_effect=lambda url, allowed: url), patch.object(contacts.time, 'sleep'):
            label, _ = contacts.discover('https://label.com.au/', Session())
            shop, events = contacts.discover('https://shop.com.au/', Session())
        self.assertEqual([(f['value'], f['source_url']) for f in label], [('1300 123 456', 'https://label.com.au/get-started')])
        self.assertEqual([(f['value'], f['source_url']) for f in shop], [('(02) 6230 0233', 'https://shop.com.au/reach-us/contact')])
        self.assertNotIn('https://shop.com.au/blog', [e['url'] for e in events])

    def test_rejects_other_hosts_and_private_network_destinations(self):
        with self.assertRaises(ValueError):
            contacts.safe_url('https://other-business.com/contact', 'business.com')
        with patch.object(contacts.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                contacts.safe_url('https://business.com/contact', 'business.com')

    def test_redirect_robots_is_checked_before_fetch(self):
        class Response:
            status_code = 302
            headers = {'Location': '/private'}
        class Session:
            calls = []
            def get(self, url, **kwargs):
                self.calls.append(url)
                return Response()
        robot = contacts.RobotFileParser()
        robot.parse(['User-agent: *', 'Disallow: /private'])
        session = Session()
        with patch.object(contacts, 'safe_url', side_effect=lambda url, allowed: url):
            with self.assertRaises(ValueError):
                contacts.get_public(session, 'https://business.com/contact', 'business.com', robot)
        self.assertEqual(session.calls, ['https://business.com/contact'])

    def test_buyict_website_queues_supplier_and_reviewed_map_wins(self):
        import json, sqlite3, tempfile
        from contextlib import closing
        from pathlib import Path
        import buyict_sellers, company_coverage, sync_approachable_routes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'inputs').mkdir()
            (root / 'inputs' / 'supplier_websites.json').write_text(json.dumps({'records': [
                {'supplier_abn': '22222222222', 'website_url': 'https://reviewed.com.au/contact',
                 'identity_source_url': 'https://register.example/2', 'identity_status': 'reviewed'}]}), encoding='utf-8')
            with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
                db.executescript(company_coverage.SCHEMA + sync_approachable_routes.SCHEMA + buyict_sellers.SCHEMA + contacts.SCHEMA)
                for abn in ('11111111111', '22222222222', '33333333333'):
                    db.execute('INSERT INTO suppliers (supplier_key, name, abn) VALUES (?, ?, ?)', ('ABN:' + abn, 'S' + abn, abn))
                db.execute("""INSERT INTO buyict_sellers (sys_id, abn, website, source_url) VALUES
                    ('a', '11111111111', 'https://one.com.au/', 'https://www.buyict.gov.au/sp?sid=a'),
                    ('b', '22222222222', 'https://other.com.au/', 'https://www.buyict.gov.au/sp?sid=b')""")
                # Queued earlier without a website: must move to website_established.
                db.execute("""INSERT INTO contact_research_queue VALUES ('ABN:11111111111', '', '', 'website_not_established', NULL, '', '')""")
                db.commit()
            with patch.object(contacts, 'ROOT', root), patch.object(contacts, 'MAP', root / 'inputs' / 'supplier_websites.json'), \
                    patch.object(contacts.assistant, 'DB_DIR', root), patch('builtins.print'):
                contacts.run()
            with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
                queue = {row[0]: row[1:] for row in db.execute(
                    'SELECT supplier_key, website_url, identity_source_url, status FROM contact_research_queue')}
        self.assertEqual(queue['ABN:11111111111'], ('https://one.com.au/', 'https://www.buyict.gov.au/sp?sid=a', 'website_established'))
        self.assertEqual(queue['ABN:22222222222'], ('https://reviewed.com.au/contact', 'https://register.example/2', 'website_established'))
        self.assertEqual(queue['ABN:33333333333'][2], 'website_not_established')

    def test_crawl_visits_each_site_once_and_records_every_outcome(self):
        import sqlite3, tempfile
        from contextlib import closing
        from pathlib import Path
        import buyict_sellers, company_coverage, sync_approachable_routes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
                db.executescript(company_coverage.SCHEMA + sync_approachable_routes.SCHEMA + buyict_sellers.SCHEMA + contacts.SCHEMA)
                sellers = [('11111111111', 'https://group.com.au/'), ('22222222222', 'https://group.com.au/'),
                           ('33333333333', 'https://quiet.com.au/'), ('44444444444', 'https://down.com.au/')]
                for abn, site in sellers:
                    db.execute('INSERT INTO suppliers (supplier_key, name, abn) VALUES (?, ?, ?)', ('ABN:' + abn, abn, abn))
                    db.execute('INSERT INTO buyict_sellers (sys_id, abn, website, source_url) VALUES (?, ?, ?, ?)', (abn, abn, site, 'src'))
                db.commit()
            visits = []
            def fake_discover(website):
                visits.append(website)
                if 'down' in website:
                    raise contacts.socket.gaierror(11001, 'getaddrinfo failed')
                if 'quiet' in website:
                    return [], [{'url': website, 'status': 'fetched'}]
                return [{'kind': 'email', 'value': 'info@group.com.au', 'purpose': 'functional business mailbox',
                         'source_url': website + 'contact', 'source_sha256': 'x', 'checked_at_utc': 't',
                         'locator': 'published mailto anchor', 'review_status': 'candidate_not_reviewed'}], []
            with patch.object(contacts, 'ROOT', root), patch.object(contacts, 'MAP', root / 'none.json'), \
                    patch.object(contacts.assistant, 'DB_DIR', root), patch.object(contacts, 'discover', fake_discover), \
                    patch('builtins.print'):
                contacts.run(crawl=True, limit=10, workers=3)
            with closing(sqlite3.connect(root / 'company_contacts.db')) as db:
                status = dict(db.execute('SELECT supplier_key, status FROM contact_research_queue'))
                candidates = sorted(row[0] for row in db.execute('SELECT supplier_key FROM contact_candidates'))
        self.assertEqual(sorted(visits), ['https://down.com.au/', 'https://group.com.au/', 'https://quiet.com.au/'])
        self.assertEqual(status, {'ABN:11111111111': 'candidates_found', 'ABN:22222222222': 'candidates_found',
                                  'ABN:33333333333': 'no_published_contact_found', 'ABN:44444444444': 'blocked_or_unavailable'})
        self.assertEqual(candidates, ['ABN:11111111111', 'ABN:22222222222'])


if __name__ == '__main__':
    unittest.main()

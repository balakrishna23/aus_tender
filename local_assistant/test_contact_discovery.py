import unittest
from unittest.mock import patch
import contact_discovery as contacts


class ContactDiscoveryTests(unittest.TestCase):
    def test_form_examples_and_embedded_templates_are_not_contacts(self):
        parser = contacts.ContactHTML()
        parser.feed('''<input placeholder="jane@agency.gov.au">
            <input placeholder="+61 400 000 000">
            <template><a href="mailto:fake@example.com">example</a></template>
            <a href="mailto:enquiries@business.com.au?subject=Hello">Email</a>
            <a href="tel:+61262300233">Call</a>''')
        self.assertEqual(parser.mail, ['enquiries@business.com.au'])
        self.assertEqual(parser.phone, ['+61262300233'])

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


if __name__ == '__main__':
    unittest.main()

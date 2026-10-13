import sqlite3
import unittest
from contextlib import closing
import company_coverage as coverage
import sync_approachable_routes as routes

PENDING = 'email/phone not established; web review pending'
REVIEWED = 'reviewed public business route available'


class ContactStatusTests(unittest.TestCase):
    def test_status_and_counts_include_approachable_routes(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.executescript(coverage.SCHEMA + routes.SCHEMA)
            for key in ('ABN:1', 'ABN:2', 'ABN:3', 'ABN:4'):
                db.execute('INSERT INTO suppliers (supplier_key, contact_status) VALUES (?, ?)', (key, PENDING))
            db.execute("INSERT INTO supplier_contacts (supplier_key, kind, value) VALUES ('ABN:1', 'email', 'a@b.com.au')")
            db.execute("""INSERT INTO approachable_routes (route_id, supplier_key, preferred_kind, secondary_kind)
                VALUES ('r2', 'ABN:2', 'email', 'phone'), ('r3', 'ABN:3', 'contact_form', ''),
                       ('gone', 'ABN:9', 'email', '')""")
            counts = coverage.contact_counts(db)
            status = dict(db.execute('SELECT supplier_key, contact_status FROM suppliers'))
            stored = dict(db.execute('SELECT name, value FROM coverage_info'))
        self.assertEqual(status, {'ABN:1': REVIEWED, 'ABN:2': REVIEWED, 'ABN:3': REVIEWED, 'ABN:4': PENDING})
        # A contact form alone is a reviewed route but not an email/phone; a route for an unknown key is not counted.
        self.assertEqual(counts, {'suppliers_with_reviewed_business_routes': 3, 'suppliers_with_reviewed_email_phone': 2,
                                  'suppliers_without_reviewed_email_phone': 2})
        self.assertEqual(stored['suppliers_with_reviewed_business_routes'], '3')


if __name__ == '__main__':
    unittest.main()

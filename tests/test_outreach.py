import os
import smtplib
import tempfile
import unittest
from datetime import date
from unittest.mock import Mock, patch

import outreach

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log

ENV = {'OUTREACH_FROM_NAME': 'Sam Lee', 'OUTREACH_FROM_EMAIL': 'sam@example.com',
       'OUTREACH_COMPANY': 'Example Staffing', 'OUTREACH_ABN': '12 345 678 901',
       'OUTREACH_PHONE': '0400 000 000', 'OUTREACH_ADDRESS': '1 Example St',
       'OUTREACH_COOLDOWN_DAYS': '14', 'OUTREACH_KEYWORDS': ''}


def release(**overrides):
    row = {'source': 'buyict', 'source_id': 'LH-1', 'title': '1 Senior Business Analyst',
           'category': '', 'buyer': 'Agency A', 'contact_name': '', 'contact_email': 'Buyer@Agency.gov.au',
           'status': 'open', 'closes_on': '2099-10-12', 'closes_at': '12 Oct 2099', 'url': 'https://x/1'}
    row.update(overrides)
    return row


class MemoryStore:
    def __init__(self, releases=(), suppressed=()):
        self._releases, self._suppressed, self.rows = list(releases), set(suppressed), []

    def releases(self):
        return self._releases

    def outreach_rows(self):
        return self.rows

    def append_outreach(self, rows):
        self.rows.extend(dict(r) for r in rows)

    def update_outreach(self, key, **fields):
        for row in self.rows:
            if row['release_key'] == key:
                row.update(fields)

    def suppressed(self):
        return self._suppressed

    def add_suppressed(self, email, reason):
        self._suppressed.add(email)


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {**ENV, 'AUSTENDER_DB_DIR': self.temp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_selection_rules(self):
        rows = [release(),
                release(url='https://x/2', contact_email=''),                       # no email
                release(url='https://x/3', status='closed'),                        # closed
                release(url='https://x/4', source='nsw', source_id='R1',
                        title='Road resurfacing works'),                            # not about people
                release(url='https://x/5', source='nsw', source_id='R2',
                        title='Registered nurses panel'),                           # people keyword
                release(url='https://x/6', contact_email='blocked@agency.gov.au'),  # suppressed
                release(url='https://x/7', closes_on='2000-01-01')]                 # closing date passed
        chosen = outreach.select_candidates(rows, already_queued={'buyict:https://x/9'},
                                            suppressed={'blocked@agency.gov.au'}, today=date(2026, 10, 3))
        self.assertEqual([r['url'] for r in chosen], ['https://x/1', 'https://x/5'])

    def test_personalised_render(self):
        subject, body = outreach.render(release(contact_name='Jordan Smith', title='7 Content Designers'))
        self.assertEqual(subject, 'Content Designers (LH-1) - suitable candidates available')
        self.assertIn('Hi Jordan,', body)
        self.assertIn('we currently have Content Designers available', body)
        self.assertIn('Monday 12 October 2099', body)
        self.assertIn('ABN 12 345 678 901', body)
        self.assertIn('unsubscribe', body)
        _, generic = outreach.render(release(contact_name="No contact details reported. Please direct questions"))
        self.assertIn('Hi there,', generic)
        self.assertIn('a Senior Business Analyst available', generic)

    def test_queue_then_send_only_approved_with_cooldown(self):
        store = MemoryStore([release(), release(url='https://x/2', contact_email='other@agency.gov.au'),
                             release(url='https://x/3')])  # same address as the first
        outreach.queue(store)
        self.assertEqual(len(store.rows), 3)
        self.assertEqual(len(outreach.queue(store)), 0)  # nothing queued twice
        store.rows[0]['approve'] = 'yes'
        store.rows[2]['approve'] = 'yes'
        smtp = Mock()
        sent = outreach.send(store, delay=0, smtp_factory=lambda: smtp)
        self.assertEqual(sent, 1)
        self.assertEqual(smtp.send_message.call_count, 1)
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message['To'], 'Buyer@Agency.gov.au')
        self.assertIn('unsubscribe', message['List-Unsubscribe'])
        self.assertEqual([r['status'] for r in store.rows], ['sent', 'queued', 'skipped'])
        self.assertIn('last 14 days', store.rows[2]['note'])  # same address, cooldown

    def test_dry_run_and_suppression(self):
        store = MemoryStore([release()])
        outreach.queue(store)
        smtp_factory = Mock()
        self.assertEqual(outreach.send(store, dry_run=True, auto_approve=True, delay=0,
                                       smtp_factory=smtp_factory), 1)
        smtp_factory.assert_not_called()
        outreach.suppress(store, 'buyer@agency.gov.au', 'requested')
        self.assertEqual(outreach.send(store, auto_approve=True, delay=0, smtp_factory=smtp_factory), 0)
        self.assertEqual(store.rows[0]['status'], 'skipped')
        smtp_factory.assert_not_called()

    def test_per_run_limit(self):
        store = MemoryStore([release(url=f'https://x/{i}', contact_email=f'p{i}@a.gov.au') for i in range(5)])
        outreach.queue(store)
        smtp = Mock()
        self.assertEqual(outreach.send(store, auto_approve=True, max_emails=2, delay=0,
                                       smtp_factory=lambda: smtp), 2)
        self.assertEqual([r['status'] for r in store.rows].count('queued'), 3)


class OutreachFailureTests(unittest.TestCase):
    setUp = OutreachTests.setUp

    def _store(self, n=3):
        store = MemoryStore([release(url=f'https://x/{i}', contact_email=f'p{i}@a.gov.au') for i in range(n)])
        outreach.queue(store)
        return store

    def test_login_failure_leaves_everything_queued(self):
        store = self._store()
        def broken_login():
            raise smtplib.SMTPAuthenticationError(535, b'bad password')
        with self.assertRaises(smtplib.SMTPAuthenticationError):
            outreach.send(store, auto_approve=True, delay=0, smtp_factory=broken_login)
        self.assertEqual({r['status'] for r in store.rows}, {'queued'})

    def test_rejected_address_fails_alone_and_dropped_connection_stops(self):
        store = self._store(3)
        smtp = Mock()
        smtp.send_message.side_effect = [smtplib.SMTPRecipientsRefused({'p0@a.gov.au': (550, b'no')}),
                                         None, smtplib.SMTPServerDisconnected('gone')]
        self.assertEqual(outreach.send(store, auto_approve=True, delay=0, smtp_factory=lambda: smtp), 1)
        self.assertEqual([r['status'] for r in store.rows], ['failed', 'sent', 'queued'])

    def test_rows_found_by_key_after_people_sort_the_sheet(self):
        store = self._store(2)
        store.rows.reverse()  # someone sorted the sheet
        store.rows[0]['approve'] = 'yes'  # approve the row now on top (originally the second)
        smtp = Mock()
        outreach.send(store, delay=0, smtp_factory=lambda: smtp)
        self.assertEqual(smtp.send_message.call_args.args[0]['To'], 'p1@a.gov.au')
        self.assertEqual([r['status'] for r in store.rows], ['sent', 'queued'])


if __name__ == '__main__':
    unittest.main()

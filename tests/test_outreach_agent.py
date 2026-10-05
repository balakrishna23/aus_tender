import email
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log

import outreach_agent as agent  # noqa: E402

PROFILE = {
    'company': 'Acme Staffing', 'abn': '11 222 333 444', 'website': 'www.acme.test',
    'what_we_supply': [{'area': 'ICT specialists', 'roles': ['security consultants']}],
    'clearances': ['Baseline', 'NV1'], 'locations': ['NSW'],
    'signature': {'name': 'Sam Sender', 'title': 'BD', 'phone': '+61 2 1111 2222', 'email': 'agent@gmail.test'},
}
CLOSES = (date.today() + timedelta(days=10)).isoformat()


def release(ref='LH-1', email_addr='buyer@agency.gov.au', closes=CLOSES, title='1 Senior Security Consultant'):
    return {'source': 'buyict', 'source_id': ref, 'title': title, 'buyer': 'Test Agency', 'category': '',
            'status': 'open', 'closes_on': closes, 'closes_at': closes, 'contact_email': email_addr,
            'contact_name': '', 'url': f'https://example.test/{ref}', 'location': 'NSW'}


GOOD_BODY = ('Hi TA Procurement team,\n\nI noticed Test Agency has released LH-1 for a Senior Security Consultant in NSW. '
             'We supply security consultants who hold or can obtain NV1 clearance and can work in NSW. '
             'We will respond through the formal BuyICT process and wanted to make sure our capability '
             'statement, which is attached, reaches the right team. It outlines the ICT specialists we supply '
             'to government agencies and how we screen candidates before they are put forward. If there is '
             'anything about the requirement you would like suppliers to keep in mind, or a better contact '
             'for similar roles in future, I would be glad to hear from you.\n\nThank you for your time.')


class FakeSheet:
    def __init__(self, enabled=True):
        self.enabled, self.logged, self.dnc, self.off = enabled, [], [], None

    def sending_enabled(self):
        return self.enabled

    def switch_off(self, reason):
        self.off = reason

    def log_send(self, values):
        self.logged.append(values)

    def do_not_contact(self, mode):
        return set()

    def add_do_not_contact(self, address, reason, mode):
        self.dnc.append((address, mode))


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': self.dir.name, 'OUTREACH_MODE': 'test',
                                      'OUTREACH_TEST_RECIPIENT': 'dummy@test.example',
                                      'OUTREACH_FORWARD_TO': 'me@test.example',
                                      'OUTREACH_FROM_EMAIL': 'agent@gmail.test'})
        env.start()
        self.addCleanup(env.stop)
        self.attachment = os.path.join(self.dir.name, 'cap.pdf')
        with open(self.attachment, 'wb') as f:
            f.write(b'%PDF-1.4 test')
        patcher = patch.object(agent, 'ATTACHMENT_PATH', self.attachment)
        patcher.start()
        self.addCleanup(patcher.stop)


class DraftCheckTests(AgentTestCase):
    def facts(self):
        return agent.tender_facts(release())

    def test_good_draft_passes(self):
        self.assertEqual(agent.check_draft('Senior Security Consultant (LH-1)', GOOD_BODY, self.facts(), PROFILE), [])

    def test_invented_number_link_and_missing_reference_are_caught(self):
        body = GOOD_BODY.replace('LH-1', 'the tender').replace('NV1 clearance', 'NV1 clearance and 15 years '
                                                                                 'experience, see www.acme.test')
        problems = agent.check_draft('Security role', body, self.facts(), PROFILE)
        self.assertTrue(any('15' in p for p in problems))
        self.assertTrue(any('link' in p for p in problems))
        self.assertTrue(any('reference' in p for p in problems))

    def test_rejected_ai_draft_falls_back_to_template(self):
        subject, body, writer, note = agent.write_email(release(), PROFILE,
                                                        ai=lambda f, p, **kw: ('Hi', 'Hi there, buy now for $5'))
        self.assertEqual(writer, 'template')
        self.assertIn('rejected', note)
        self.assertIn('LH-1', subject)
        self.assertIn('capability statement is attached', body)

    def test_ai_failure_falls_back_to_template(self):
        def broken(facts, profile, **kw):
            raise agent.WriterError('HTTP 500')
        self.assertEqual(agent.write_email(release(), PROFILE, ai=broken)[2], 'template')

    def test_good_ai_draft_is_used(self):
        result = agent.write_email(release(), PROFILE, ai=lambda f, p, **kw: ('Senior Security Consultant (LH-1)', GOOD_BODY))
        self.assertEqual(result[2], 'ai')


class MessageTests(AgentTestCase):
    def test_test_mode_goes_only_to_dummy_with_attachment_signature_and_opt_out(self):
        msg = agent.build_message(release(), 'Subject (LH-1)', GOOD_BODY, PROFILE, 'test')
        self.assertEqual(msg['To'], 'dummy@test.example')
        self.assertTrue(msg['Subject'].startswith('[TEST → buyer@agency.gov.au]'))
        attachments = [p.get_filename() for p in msg.iter_attachments()]
        self.assertEqual(attachments, ['Acme Staffing - Capability Statement.pdf'])
        text = msg.get_body(preferencelist=('plain',)).get_content()
        self.assertIn('Sam Sender', text)
        self.assertIn('ABN 11 222 333 444', text)
        self.assertIn('unsubscribe', text)

    def test_live_mode_goes_to_the_contact(self):
        with patch.dict(os.environ, {'OUTREACH_MODE': 'live'}):
            msg = agent.build_message(release(), 'Subject (LH-1)', GOOD_BODY, PROFILE, 'live')
        self.assertEqual(msg['To'], 'buyer@agency.gov.au')
        self.assertNotIn('TEST', msg['Subject'])

    def test_test_mode_without_dummy_refuses(self):
        with patch.dict(os.environ, {'OUTREACH_TEST_RECIPIENT': ''}):
            with self.assertRaises(RuntimeError):
                agent.build_message(release(), 'S', GOOD_BODY, PROFILE, 'test')


class GuardrailTests(AgentTestCase):
    def test_send_window(self):
        sydney = agent.SYDNEY
        self.assertTrue(agent.in_send_window(datetime(2026, 10, 6, 10, 0, tzinfo=sydney))[0])   # Tuesday
        self.assertFalse(agent.in_send_window(datetime(2026, 10, 6, 8, 59, tzinfo=sydney))[0])  # too early
        self.assertFalse(agent.in_send_window(datetime(2026, 10, 6, 16, 0, tzinfo=sydney))[0])  # too late
        self.assertFalse(agent.in_send_window(datetime(2026, 10, 10, 10, 0, tzinfo=sydney))[0])  # Saturday
        self.assertIn('holiday', agent.in_send_window(datetime(2026, 10, 5, 10, 0, tzinfo=sydney))[1])  # Labour Day

    def test_warm_up_then_full_cap(self):
        with patch.dict(os.environ, {'OUTREACH_DAILY_CAP': '20'}):
            with agent.closing(agent.state_db()) as conn:
                self.assertEqual(agent.daily_cap(conn), 5)
                conn.execute("INSERT INTO state VALUES ('first_send_day', ?)",
                             ((date.today() - timedelta(days=20)).isoformat(),))
                self.assertEqual(agent.daily_cap(conn), 20)

    def test_candidates_skip_cooldown_soon_closing_and_unsubscribed(self):
        releases = [release('LH-1', 'a@x.gov.au'), release('LH-2', 'b@x.gov.au', closes=date.today().isoformat()),
                    release('LH-3', 'c@x.gov.au'), release('LH-4', 'd@x.gov.au')]
        with agent.closing(agent.state_db()) as conn:
            conn.execute("INSERT INTO sends (mode, release_key, intended_to, sent_at, status) VALUES "
                         "('test', 'other', 'c@x.gov.au', ?, 'sent')", (agent.now_text(),))
            agent.suppress(conn, 'd@x.gov.au', 'unsubscribe', 'test')
            self.assertEqual([r['source_id'] for r in agent.candidates(conn, 'test', releases)], ['LH-1'])
            # A test-mode unsubscribe does not block live sending.
            self.assertIn('LH-4', [r['source_id'] for r in agent.candidates(conn, 'live', releases)])


class RunTests(AgentTestCase):
    def run_agent(self, sheet, releases, **kw):
        smtp = Mock()
        results = agent.run(now=True, sheet=sheet, smtp_factory=lambda: smtp, profile=PROFILE, releases=releases,
                            ai=lambda f, p, **kw: (f"Senior Security Consultant ({f['reference']})",
                                             GOOD_BODY.replace('LH-1', f['reference'])),
                            sleep=lambda s: None, **kw)
        return results, smtp

    def test_sends_within_cap_records_and_never_repeats(self):
        sheet = FakeSheet()
        releases = [release(f'LH-{i}', f'p{i}@x.gov.au') for i in range(1, 8)]
        results, smtp = self.run_agent(sheet, releases)
        self.assertEqual(len(results), 5)  # warm-up cap
        self.assertEqual(smtp.send_message.call_count, 5)
        self.assertTrue(all(m.args[0]['To'] == 'dummy@test.example' for m in smtp.send_message.call_args_list))
        self.assertEqual(len(sheet.logged), 5)
        again, smtp2 = self.run_agent(sheet, releases)
        self.assertEqual(again, [])  # cap reached today; nothing re-sent
        smtp2.send_message.assert_not_called()

    def test_off_switch_stops_everything(self):
        results, smtp = self.run_agent(FakeSheet(enabled=False), [release()])
        self.assertEqual(results, [])
        smtp.send_message.assert_not_called()

    def test_dry_run_never_connects(self):
        smtp_factory = Mock()
        results = agent.run(dry_run=True, smtp_factory=smtp_factory, profile=PROFILE, releases=[release()],
                            use_ai=False)
        self.assertEqual([r['status'] for r in results], ['dry run'])
        smtp_factory.assert_not_called()


class InboxTests(AgentTestCase):
    def message(self, frm, subject, body, in_reply_to=''):
        raw = f'From: {frm}\nSubject: {subject}\nIn-Reply-To: {in_reply_to}\n\n{body}'
        return email.message_from_string(raw)

    def test_classify(self):
        self.assertEqual(agent.classify(self.message('Mail Delivery Subsystem <mailer-daemon@googlemail.com>',
                                                     'Delivery Status Notification (Failure)', 'x'))[0], 'bounce')
        self.assertEqual(agent.classify(self.message('a@x.gov.au', 'Re: role', 'Please unsubscribe me'))[0],
                         'unsubscribe')
        self.assertEqual(agent.classify(self.message('a@x.gov.au', 'Re: role', 'Thanks, send CVs'))[0], 'reply')

    def test_unsubscribe_reply_blocks_the_agency_it_was_meant_for(self):
        with agent.closing(agent.state_db()) as conn:
            conn.execute("INSERT INTO sends (mode, release_key, intended_to, sent_to, message_id, status) VALUES "
                         "('test', 'k', 'buyer@agency.gov.au', 'dummy@test.example', '<m1@gmail.test>', 'sent')")
            raw = self.message('dummy@test.example', 'Re: role', 'unsubscribe', '<m1@gmail.test>').as_bytes()
            imap = Mock()
            imap.search.return_value = ('OK', [b'1'])
            imap.fetch.return_value = ('OK', [(b'1', raw)])
            sheet = FakeSheet()
            counts = agent.process_inbox(conn, sheet, smtp_factory=Mock(), imap_factory=lambda: imap)
            self.assertEqual(counts['unsubscribe'], 1)
            self.assertIn('buyer@agency.gov.au', agent.suppressed(conn, 'test'))
            self.assertEqual(sheet.dnc, [('buyer@agency.gov.au', 'test')])

    def test_unrelated_mail_is_left_alone(self):
        with agent.closing(agent.state_db()) as conn:
            raw = self.message('no-reply@accounts.google.com', 'Security alert', 'New sign-in').as_bytes()
            imap = Mock()
            imap.search.return_value = ('OK', [b'1'])
            imap.fetch.return_value = ('OK', [(b'1', raw)])
            smtp = Mock()
            counts = agent.process_inbox(conn, FakeSheet(), smtp_factory=lambda: smtp, imap_factory=lambda: imap)
            self.assertEqual(counts, {'reply': 0, 'unsubscribe': 0, 'bounce': 0})
            smtp.send_message.assert_not_called()
            imap.store.assert_not_called()  # stays unread

    def test_real_reply_is_forwarded(self):
        with agent.closing(agent.state_db()) as conn:
            conn.execute("INSERT INTO sends (mode, release_key, intended_to, sent_to, message_id, status) VALUES "
                         "('live', 'k', 'a@x.gov.au', 'a@x.gov.au', '<m2@gmail.test>', 'sent')")
            raw = self.message('a@x.gov.au', 'Re: role', 'Interested, please call', '<m2@gmail.test>').as_bytes()
            imap = Mock()
            imap.search.return_value = ('OK', [b'1'])
            imap.fetch.return_value = ('OK', [(b'1', raw)])
            smtp = Mock()
            agent.process_inbox(conn, FakeSheet(), smtp_factory=lambda: smtp, imap_factory=lambda: imap)
            forwarded = smtp.send_message.call_args.args[0]
            self.assertEqual(forwarded['To'], 'me@test.example')
            self.assertIn('Interested', forwarded.get_content())


class SignOffTests(unittest.TestCase):
    def test_trailing_sign_offs_are_removed(self):
        self.assertEqual(agent.strip_sign_off('Hi there,\n\nBody text.\n\nKind regards'), 'Hi there,\n\nBody text.')
        self.assertEqual(agent.strip_sign_off('Hi there,\n\nBody.\n\nRegards,\nSam Smith'), 'Hi there,\n\nBody.')
        self.assertEqual(agent.strip_sign_off('Hi there,\n\nThank you for your time.'),
                         'Hi there,\n\nThank you for your time.')


class PersonalisationTests(AgentTestCase):
    def test_promises_are_caught(self):
        facts = agent.tender_facts(release())
        for claim in ('Our candidates are available to start on 9 November.',
                      'We can put forward candidates who meet the seniority requirement.',
                      'Illoura Services holds Baseline clearance eligibility.',
                      'We currently have a Senior Security Consultant available.',
                      'Our candidates can commence immediately.'):
            body = GOOD_BODY.replace('Thank you for your time.', claim)
            self.assertTrue(any('promises' in p for p in agent.check_draft('S (LH-1)', body, facts, PROFILE)), claim)
        self.assertEqual(agent.check_draft('S (LH-1)', GOOD_BODY, facts, PROFILE), [])

    def test_rejected_draft_gets_one_revision_with_feedback(self):
        calls = []

        def writer(facts, profile, agency=None, feedback=None):
            calls.append(feedback)
            if feedback is None:
                return 'S (LH-1)', GOOD_BODY.replace('Thank you for your time.', 'Our candidates can commence now.')
            return 'S (LH-1)', GOOD_BODY
        subject, body, writer_name, note = agent.write_email(release(), PROFILE, ai=writer)
        self.assertEqual(writer_name, 'ai')
        self.assertIsNone(calls[0])
        self.assertTrue(any('promises' in p for p in calls[1]))
        self.assertIn('revised once', note)

    def test_quarters_and_plain_word_agency_activity(self):
        self.assertTrue(agent._quarter_passed('Quarter 2 2025/2026', date(2026, 10, 6)))
        self.assertFalse(agent._quarter_passed('Quarter 2 2026/2027', date(2026, 10, 6)))
        self.assertFalse(agent._quarter_passed('Quarter 3 2026/2027', date(2026, 10, 6)))
        self.assertEqual([agent._often(n) for n in (0, 3, 10, 40)], [None, 'occasionally', 'regularly', 'frequently'])

    def test_named_contact_is_greeted_by_first_name(self):
        facts = agent.tender_facts(dict(release(), contact_name='Ms Bella Darmody'))
        self.assertEqual(facts['greeting_name'], 'Bella')
        self.assertEqual(agent.tender_facts(dict(release(), contact_name='Dr. Sam Lee'))['greeting_name'], 'Sam')
        self.assertEqual(agent.tender_facts(dict(release(), contact_name='Procurement Team'))['greeting_name'], 'TA Procurement team')


class GreetingTests(AgentTestCase):
    def test_team_greeting_from_buyer_and_mailbox(self):
        self.assertEqual(agent.team_greeting('Australian Prudential Regulation Authority', 'procurement@apra.gov.au'),
                         'APRA Procurement team')
        self.assertEqual(agent.team_greeting('Department of Agriculture, Fisheries and Forestry',
                                             'DBDProcurement@aff.gov.au'), 'DAFF Procurement team')
        self.assertEqual(agent.team_greeting('Australian Digital Health Agency', 'tenderresponses@digitalhealth.gov.au'),
                         'ADHA Procurement team')

    def test_named_contact_then_mailbox_owner_then_team(self):
        row = release()
        self.assertEqual(agent.greeting_for(dict(row, contact_name='Ms Bella Darmody')), 'Bella')
        with patch.object(agent, 'person_for_address', return_value='Thi Anna Ly'):
            self.assertEqual(agent.greeting_for(row), 'Thi Anna Ly')  # full name: given name unknown
        with patch.object(agent, 'person_for_address', return_value=None):
            self.assertEqual(agent.greeting_for(dict(row, buyer='Test Agency')), 'TA Procurement team')

"""Daily brief selection, editorial notes and send window, with synthetic data only."""
import importlib.util
import io
import json
import os
import sqlite3
from contextlib import closing, nullcontext, redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'daily_brief', Path(__file__).resolve().parents[1] / 'deploy/illoura_daily_brief.py')
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)

NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)  # 07:00 Sydney (AEDT) on 13 November


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'intelligence.db'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE opportunities(source TEXT, source_id TEXT, closes_at TEXT, title TEXT, '
                       'buyer TEXT, url TEXT, status_effective TEXT, is_relevant INTEGER)')

    def add(self, key, closes, source='BuyICT', status='Open', relevant=1, title='Role', buyer='Agency'):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?)',
                       (source, key, closes, title, buyer, 'https://www.buyict.gov.au/x?' + key, status, relevant))

    def at(self, hours):
        return (NOW + timedelta(hours=hours)).isoformat()

    def test_labour_hire_closing_soonest_within_window(self):
        self.add('LH-1', self.at(10))            # too soon (< 18 h)
        self.add('LH-2', self.at(48))
        self.add('LH-3', self.at(24))
        self.add('LH-4', self.at(24 * 30))       # beyond 21 days
        self.add('LH-5', self.at(30), status='Closed')
        self.add('LH-6', self.at(30), relevant=0)
        self.add('LH-7', self.at(30), source='AusTender')
        self.add('PCS-1', self.at(20))
        self.add('LH-8', self.at(36))
        self.assertEqual(brief.select_ids(self.db, NOW), ['LH-3', 'LH-8', 'LH-2'])

    def test_offsets_are_compared_as_instants_not_text(self):
        # 06:00 +11:00 on the 14th is 19:00 UTC on the 13th: 23 h after NOW, so eligible...
        self.add('LH-A', '2026-11-14T06:00:00+11:00')
        # ...while 01:00 +00:00 on the 13th is only 5 h after NOW, though it sorts later as text.
        self.add('LH-B', '2026-11-13T01:00:00+00:00')
        self.add('LH-C', self.at(40))
        self.add('LH-D', self.at(41))
        self.assertEqual(brief.select_ids(self.db, NOW), ['LH-A', 'LH-C', 'LH-D'])

    def test_professional_services_fill_when_labour_hire_is_scarce(self):
        self.add('LH-1', self.at(30))
        self.add('PCS-1', self.at(20))
        self.add('PCS-2', self.at(25))
        self.assertEqual(brief.select_ids(self.db, NOW), ['LH-1', 'PCS-1', 'PCS-2'])

    def test_malformed_close_times_are_ignored(self):
        self.add('LH-1', 'Friday, 27 November 2026')
        self.add('LH-2', '')
        self.add('LH-3', '2026-11-20T10:00:00')  # no offset: ambiguous, ignored
        self.assertEqual(brief.select_ids(self.db, NOW), [])

    def test_new_since_baseline(self):
        self.add('LH-1', self.at(30), title='Data � engineer')
        self.add('LH-2', self.at(40))
        rows = brief.new_since_baseline(self.db, ['LH-2'])
        self.assertEqual([r[0] for r in rows], ['LH-1'])
        self.assertIn('Canberra', rows[0][3])


class EditorialTests(unittest.TestCase):
    def record(self, description='Deliver services.'):
        return {'content': {'description': description, 'mainDetails': [], 'rfqOpen': True}}

    def test_notes_have_the_fields_the_composer_reads(self):
        report = {'results': [{'spider': 'buy_ict', 'status': 'OK'}]}
        notes = brief.editorial_notes(date(2026, 11, 13), {'LH-1': self.record()},
                                      [('LH-9', 'Role', 'Agency', '20 November, 02:00 PM Canberra', 'https://x')],
                                      report)
        for field in ('edition_date', 'subject', 'preheader', 'introduction', 'opportunities',
                      'additional_sections', 'coverage_note'):
            self.assertIn(field, notes)
        self.assertEqual(notes['edition_date'], '2026-11-13')
        self.assertIn('13 November 2026', notes['subject'])
        self.assertIn('1 open request ', notes['subject'] + ' ')
        self.assertIn('automatic', notes['coverage_note'].lower())
        self.assertEqual(notes['additional_sections'][0]['items'][0]['links'][0]['url'], 'https://x')
        self.assertIsInstance(notes['opportunities']['LH-1'], list)

    def test_restricted_listing_text_adds_an_access_warning(self):
        report = {'results': []}
        notes = brief.editorial_notes(date(2026, 11, 13), {
            'LH-1': self.record('This request is only open to selected sellers on the panel.'),
            'LH-2': self.record()}, [], report)
        self.assertTrue(any('access' in n for n in notes['opportunities']['LH-1']))
        self.assertFalse(any('access' in n for n in notes['opportunities']['LH-2']))
        self.assertEqual(notes['additional_sections'], [])

    def test_source_gaps_are_named(self):
        notes = brief.editorial_notes(date(2026, 11, 13), {}, [], {'results': [{'spider': 'nsw_tender', 'status': 'FAILED'}]})
        self.assertIn('nsw_tender', notes['coverage_note'])
        self.assertIn('0 open requests', notes['subject'])

    def test_clean_removes_replacement_characters(self):
        self.assertEqual(brief.clean('A � B'), 'A - B')

    def test_still_open_requires_open_listing_and_two_hours(self):
        def closing(_text):
            return NOW + timedelta(hours=5)
        listing = {'content': {'mainDetails': [{'label': 'RFQ closing date', 'value': 'x'}], 'rfqOpen': True}}
        self.assertTrue(brief.still_open(listing, NOW, closing))
        listing['content']['rfqOpen'] = False
        self.assertFalse(brief.still_open(listing, NOW, closing))
        listing['content']['rfqOpen'] = True
        self.assertFalse(brief.still_open(listing, NOW + timedelta(hours=4), closing))


class SendWindowTests(unittest.TestCase):
    def test_send_at_0815_sydney_with_noon_cutoff(self):
        target, cutoff = brief.send_window(NOW)
        self.assertEqual((target.hour, target.minute, target.day), (8, 15, 13))
        self.assertEqual((cutoff.hour, cutoff.minute), (12, 0))
        self.assertEqual(target.utcoffset(), timedelta(hours=11))  # AEDT in November
        self.assertEqual(target.astimezone(timezone.utc).hour, 21)


class SendSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.out = self.work / 'reports/daily_sweep_current'
        self.out.mkdir(parents=True)
        self.other = self.work / 'reports/daily_sweep_other'
        self.other.mkdir()
        self.write(self.out, 'editorial_notes.json', {'edition_date': '2026-11-13'})
        self.write(self.other, 'editorial_notes.json', {'edition_date': '2026-11-13'})
        self.write(self.work, 'latest_run.json', {'run_dir': str(self.out), 'sent': False})
        self.addCleanup(patch.stopall)
        patch.object(brief, 'WORK', self.work).start()

    def write(self, folder, name, value):
        brief.write_json(folder / name, value)

    def call_send(self):
        helper = Mock()
        with (patch.dict('sys.modules', {'send_gmail': SimpleNamespace(send=helper)}),
              patch.object(brief, 'send_lock', side_effect=nullcontext)):
            with self.assertRaisesRegex(SystemExit, 'edition'):
                brief.send(self.out)
        helper.assert_not_called()

    def test_other_run_sent_blocks_changed_request_count(self):
        self.write(self.other, 'send_receipt.json', {'sent': True, 'subject': 'Different count'})
        self.call_send()

    def test_other_run_uncertain_blocks_send(self):
        self.write(self.other, 'send_attempt.json', {})
        self.call_send()

    def test_only_explicit_undelivered_resolves_attempt(self):
        self.write(self.other, 'send_attempt.json', {})
        self.write(self.other, 'send_reconciliation.json', {'delivered': True})
        self.call_send()
        self.write(self.other, 'send_reconciliation.json', {'delivered': False})
        brief.check_edition(self.out)

    def test_other_editions_and_folders_without_notes_are_ignored(self):
        self.write(self.other, 'send_receipt.json', {'sent': True})
        self.write(self.other, 'editorial_notes.json', {'edition_date': '2026-11-12'})
        brief.check_edition(self.out)
        (self.other / 'editorial_notes.json').unlink()
        brief.check_edition(self.out)

    @unittest.skipIf(os.name == 'nt', 'Real flock is verified on Linux')
    def test_lock_is_nonblocking_and_released_after_failure(self):
        with brief.send_lock():
            with self.assertRaisesRegex(SystemExit, 'in progress'):
                with brief.send_lock():
                    self.fail('Acquired a held lock')
        with self.assertRaises(ValueError):
            with brief.send_lock():
                raise ValueError('fixture')
        with brief.send_lock():
            pass

    @unittest.skipIf(os.name == 'nt', 'Real flock is verified on Linux')
    def test_send_holds_lock_through_helper_and_state_update(self):
        def check_locked(*args):
            with self.assertRaises(SystemExit):
                with brief.send_lock():
                    self.fail('Send lock released early')
            self.write(self.out, 'send_receipt.json', {'sent': True})
        # TOKEN points at a temp path: on the server the real token folder is private to scraper.
        with (patch.dict('sys.modules', {'send_gmail': SimpleNamespace(send=check_locked)}),
              patch.object(brief, 'TOKEN', self.out / 'no-token.json'),
              patch.object(brief, 'record_sent', side_effect=check_locked), redirect_stdout(io.StringIO())):
            brief.send(self.out)

    def run_brief(self, moments, enabled='enabled'):
        systemctl = Mock(return_value=SimpleNamespace(returncode=0, stdout=enabled))
        output = io.StringIO()
        with (patch.object(brief, 'bounded_command'), patch.object(brief, 'scraper') as scraper,
              patch.object(brief, 'refresh_market_research', return_value={'status':'disabled'}),
              patch.object(brief, 'utc_now', side_effect=[moments[0]] * 3 + moments), patch.object(brief.time, 'sleep') as sleep,
              redirect_stdout(output)):
            brief.run(systemctl=systemctl)
        return scraper, sleep, systemctl, output.getvalue()

    def test_disable_during_wait_prevents_send(self):
        scraper, sleep, systemctl, output = self.run_brief([NOW, NOW, NOW + timedelta(hours=2)], 'disabled')
        self.assertEqual(scraper.call_count, 1)
        sleep.assert_called_once()
        self.assertIn('not enabled', output)
        systemctl.assert_called_once()

    def test_waking_at_noon_or_next_day_prevents_send(self):
        for hours in (5, 24):
            with self.subTest(hours=hours):
                scraper, _, systemctl, output = self.run_brief([NOW, NOW, NOW + timedelta(hours=hours)])
                self.assertEqual(scraper.call_count, 1)
                systemctl.assert_called_once()
                self.assertIn('cutoff', output)

    def test_enabled_before_cutoff_sends(self):
        scraper, _, _, _ = self.run_brief([NOW, NOW, NOW + timedelta(hours=2)])
        self.assertEqual(scraper.call_args.args[1], 'send')

    def test_timer_errors_and_non_enabled_states_fail_closed(self):
        for state in ('enabled-runtime', 'masked', 'disabled'):
            runner = Mock(return_value=SimpleNamespace(returncode=0, stdout=state))
            self.assertFalse(brief.timer_enabled(runner))
        for error in (OSError('synthetic'), brief.subprocess.TimeoutExpired('systemctl', 30)):
            self.assertFalse(brief.timer_enabled(Mock(side_effect=error)))


class ReconcileTests(unittest.TestCase):
    setUp = SendSafetyTests.setUp
    write = SendSafetyTests.write

    def attempt(self):
        self.write(self.out, 'send_attempt.json', {
            'message_id_header': '<synthetic-id>', 'subject': 'Synthetic brief',
            'sender': 'synthetic-sender', 'recipient': 'synthetic-recipient'})

    def session(self, found_query=None, mismatch=False, internal_date=None):
        session = Mock()
        def get(url, params, timeout):
            if url.endswith('/profile'):
                value = {'emailAddress': 'synthetic-sender'}
            elif url.endswith('/messages'):
                found = found_query and found_query in params['q']
                value = {'messages': [{'id': 'found'}]} if found else {}
            else:
                value = {'id': 'found', 'threadId': 'thread', 'labelIds': ['SENT'], 'payload': {'headers': [
                    {'name': 'Subject', 'value': 'Wrong' if mismatch else 'Synthetic brief'},
                    {'name': 'From', 'value': 'synthetic-sender'},
                    {'name': 'To', 'value': 'synthetic-recipient'}]}}
                if internal_date is not None:
                    value['internalDate'] = internal_date
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: value)
        session.get.side_effect = get
        return session

    def reconcile(self, session, mark=False):
        output = io.StringIO()
        with patch.object(brief, 'send_lock', side_effect=nullcontext), redirect_stdout(output):
            code = brief.reconcile(self.out, mark, session)
        session.post.assert_not_called()
        return code, json.loads(output.getvalue())

    def test_no_receipt_or_attempt_needs_no_gmail(self):
        session = self.session()
        self.assertEqual(self.reconcile(session), (0, {'status': 'nothing_to_reconcile'}))
        self.write(self.out, 'send_receipt.json', {'sent': True})
        self.assertEqual(self.reconcile(session), (0, {'status': 'already_sent'}))
        session.get.assert_not_called()

    def test_found_by_id_or_subject_updates_state(self):
        for query in ('rfc822msgid:', 'subject:'):
            with self.subTest(query=query):
                (self.out / 'send_receipt.json').unlink(missing_ok=True)
                self.attempt()
                for name in ('cloud_run.json', 'newsletter_review.json'):
                    self.write(self.out, name, {'sent': False})
                code, result = self.reconcile(self.session(query))
                self.assertEqual((code, result['status']), (0, 'delivered'))
                receipt = json.loads((self.out / 'send_receipt.json').read_text())
                self.assertEqual(receipt['reconciled_by'], 'reconcile')
                for path in (self.out / 'cloud_run.json', self.out / 'newsletter_review.json',
                             self.work / 'latest_run.json'):
                    self.assertTrue(json.loads(path.read_text())['sent'])

    def test_not_found_requires_explicit_operator_decision(self):
        self.attempt()
        self.assertEqual(self.reconcile(self.session()), (2, {'status': 'not_found_in_sent'}))
        self.assertFalse((self.out / 'send_reconciliation.json').exists())
        self.assertEqual(self.reconcile(self.session(), True), (0, {'status': 'marked_undelivered'}))
        record = json.loads((self.out / 'send_reconciliation.json').read_text())
        self.assertFalse(record['delivered'])
        self.assertEqual(record['message_id_header'], '<synthetic-id>')
        self.assertEqual(len(record['searched']), 2)
        self.assertTrue((self.out / 'send_attempt.json').exists())

    def test_wrong_headers_do_not_confirm_delivery(self):
        self.attempt()
        self.assertEqual(self.reconcile(self.session('rfc822msgid:', mismatch=True))[0], 2)

    def test_identity_mismatch_cannot_mark_undelivered(self):
        self.attempt()
        value = json.loads((self.out / 'send_attempt.json').read_text())
        value['sender'] = 'different-synthetic-sender'
        self.write(self.out, 'send_attempt.json', value)
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.reconcile(self.session(), True)
        self.assertFalse((self.out / 'send_reconciliation.json').exists())

    def test_search_order_and_pagination(self):
        self.attempt()
        session = self.session('subject:')
        original = session.get.side_effect

        def get(url, params, timeout):
            if url.endswith('/messages') and 'rfc822msgid:' in params['q'] and 'pageToken' not in params:
                return SimpleNamespace(raise_for_status=lambda: None,
                                       json=lambda: {'nextPageToken': 'second'})
            return original(url, params, timeout)

        session.get.side_effect = get
        self.assertEqual(self.reconcile(session)[1]['status'], 'delivered')
        params = [call.kwargs['params'] for call in session.get.call_args_list
                  if call.args[0].endswith('/messages')]
        self.assertEqual(params[0]['q'], 'in:sent rfc822msgid:synthetic-id')
        self.assertEqual(json.loads((self.out / 'send_attempt.json').read_text())['message_id_header'],
                         '<synthetic-id>')
        self.assertEqual(params[1]['pageToken'], 'second')
        self.assertIn('subject:', params[2]['q'])

    def test_delivered_old_run_does_not_replace_latest_run(self):
        self.attempt()
        self.write(self.work, 'latest_run.json', {'run_dir': str(self.other), 'sent': False})
        self.reconcile(self.session('rfc822msgid:'))
        self.assertFalse(json.loads((self.work / 'latest_run.json').read_text())['sent'])

    def test_late_reconciliation_preserves_gmail_send_time_and_attempt_fields(self):
        self.attempt()
        attempt = json.loads((self.out / 'send_attempt.json').read_text())
        sent_at = NOW + timedelta(milliseconds=123)
        reconciled_at = NOW + timedelta(days=3)
        for name in ('cloud_run.json', 'newsletter_review.json'):
            self.write(self.out, name, {'sent': False})
        with patch.object(brief, 'utc_now', return_value=reconciled_at):
            self.reconcile(self.session('rfc822msgid:', internal_date=str(int(sent_at.timestamp() * 1000))))
        receipt = json.loads((self.out / 'send_receipt.json').read_text())
        self.assertEqual(receipt['recorded_at_utc'], sent_at.isoformat())
        self.assertEqual(receipt['reconciled_at_utc'], reconciled_at.isoformat())
        for key in ('sender', 'recipient', 'subject'):
            self.assertEqual(receipt[key], attempt[key])
        for path in (self.out / 'cloud_run.json', self.out / 'newsletter_review.json',
                     self.work / 'latest_run.json'):
            self.assertEqual(json.loads(path.read_text())['sent_at_utc'], sent_at.isoformat())

    def test_missing_gmail_send_time_falls_back_to_reconciliation_time(self):
        self.attempt()
        with patch.object(brief, 'utc_now', return_value=NOW):
            self.reconcile(self.session('rfc822msgid:'))
        receipt = json.loads((self.out / 'send_receipt.json').read_text())
        self.assertEqual(receipt['recorded_at_utc'], NOW.isoformat())
        self.assertEqual(receipt['reconciled_at_utc'], NOW.isoformat())

    def test_gmail_failure_cannot_mark_undelivered(self):
        self.attempt()
        session = self.session()
        session.get.side_effect = RuntimeError('Synthetic transport failure')
        with self.assertRaises(RuntimeError):
            self.reconcile(session, True)
        self.assertFalse((self.out / 'send_reconciliation.json').exists())

    def test_rejects_paths_outside_run_root(self):
        with self.assertRaisesRegex(SystemExit, 'directory'):
            brief.reconcile(self.work, session=self.session())

    def test_cli_propagates_not_found_exit_code(self):
        with patch.object(brief, 'reconcile', return_value=2) as reconcile:
            with self.assertRaises(SystemExit) as result:
                brief.main(['reconcile', '--out', str(self.out), '--mark-undelivered'])
        self.assertEqual(result.exception.code, 2)
        reconcile.assert_called_once_with(str(self.out), True)


class ComposeSafetyTests(unittest.TestCase):
    def compose(self, outcomes):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            brief.write_json(out / 'source_refresh.json', {'results': []})
            keys = ['LH-' + str(i) for i in range(len(outcomes))]
            calls = []

            def command(argv, **kwargs):
                calls.append(argv)
                if '--ids' in argv:
                    key = argv[-1]
                    outcome = outcomes[keys.index(key)]
                    if outcome != 'failed':
                        brief.write_json(out / 'shortlist' / (key + '.json'), {'open': outcome == 'open'})
                    return SimpleNamespace(returncode=1 if outcome == 'failed' else 0)
                for name in ('newsletter_preview.html', 'newsletter_preview.txt', 'newsletter_editorial.json'):
                    (out / name).write_text('synthetic')
                brief.write_json(out / 'email_layout_checks.json', [{'passed': True}, {'passed': True}])
                return SimpleNamespace(returncode=0)

            with (patch.dict('sys.modules', {'classify': SimpleNamespace(closing_time=Mock())}),
                  patch.object(brief, 'select_ids', return_value=keys),
                  patch.object(brief, 'new_since_baseline', return_value=[]),
                  patch.object(brief, 'still_open', side_effect=lambda record, *args: record['open']),
                  patch.object(brief.subprocess, 'run', side_effect=command), redirect_stdout(io.StringIO())):
                try:
                    brief.compose(out, NOW)
                except SystemExit:
                    self.assertFalse((out / 'cloud_send_review.json').exists())
                    self.assertEqual(len(calls), len(keys))
                    raise
                self.assertTrue(json.loads((out / 'cloud_send_review.json').read_text())['reviewed'])

    def test_majority_failures_refuse_approval(self):
        for outcomes in (['failed'], ['failed', 'failed', 'open'], ['failed', 'failed', 'closed']):
            with self.subTest(outcomes=outcomes), self.assertRaisesRegex(SystemExit, 'half'):
                self.compose(outcomes)

    def test_closed_listings_half_failure_and_quiet_day_are_allowed(self):
        for outcomes in ([], ['closed', 'closed'], ['failed', 'open'], ['failed', 'closed']):
            with self.subTest(outcomes=outcomes):
                self.compose(outcomes)


class RetryTests(unittest.TestCase):
    setUp = SendSafetyTests.setUp
    write = SendSafetyTests.write

    def stage(self, stage, failures, now=NOW, after_pause=None):
        action = Mock(side_effect=failures)
        output = io.StringIO()
        clock = Mock(return_value=now)
        def sleep(seconds):
            self.assertIn(seconds, brief.RETRY_PAUSES[stage])
            clock.return_value = after_pause or clock.return_value + timedelta(seconds=seconds)
        with (patch.object(brief, 'utc_now', clock), patch.object(brief.time, 'sleep', side_effect=sleep),
              redirect_stdout(output)):
            brief.run_stage(stage, action, brief.send_window(now)[1])
        return action, [json.loads(line) for line in output.getvalue().splitlines()]

    def failure(self, stderr='transient'):
        return brief.subprocess.CalledProcessError(1, ['synthetic'], stderr=stderr)

    def test_collect_transient_retried_once(self):
        action, lines = self.stage('collect', [self.failure(), None])
        self.assertEqual(action.call_count, 2)
        self.assertEqual(action.call_args.args, (7200,))
        self.assertTrue(lines[0]['retry'])

    def test_compose_transient_retried_once(self):
        action, lines = self.stage('compose', [self.failure(), None])
        self.assertEqual(action.call_count, 2)
        self.assertEqual(action.call_args.args, (900,))

    def test_after_latest_start_and_pause_crossing_latest_fail(self):
        for moment in (NOW + timedelta(hours=3), NOW + timedelta(hours=2, minutes=28)):
            with self.assertRaises(brief.subprocess.CalledProcessError):
                self.stage('collect', [self.failure()], now=moment)
        with self.assertRaises(brief.subprocess.CalledProcessError):
            self.stage('collect', [self.failure()], after_pause=NOW + timedelta(hours=3))

    def test_latest_start_boundary_allowed(self):
        action, _ = self.stage('collect', [self.failure(), None], now=NOW + timedelta(hours=2, minutes=25))
        self.assertEqual(action.call_count, 2)

    def test_unresolved_guard_never_retried(self):
        self.write(self.work, 'coordinator_status.json', {'status': 'failed', 'error_type': 'RuntimeError'})
        error = self.failure('RuntimeError: Unresolved sends require reconciliation')
        self.assertTrue(brief.unresolved_guard(error))
        action = Mock(side_effect=error)
        with (patch.object(brief, 'utc_now', return_value=NOW), patch.object(brief.time, 'sleep') as sleep,
              redirect_stdout(io.StringIO()), self.assertRaises(brief.subprocess.CalledProcessError)):
            brief.run_stage('collect', action, brief.send_window(NOW)[1])
        action.assert_called_once()
        sleep.assert_not_called()
        self.assertFalse(brief.unresolved_guard(self.failure('another RuntimeError')))

    def test_two_failures_propagate_nonzero(self):
        for stage, attempts in (('collect', 5), ('compose', 2)):
            action = Mock(side_effect=[self.failure() for _ in range(attempts + 3)])
            with (patch.object(brief, 'utc_now', return_value=NOW), patch.object(brief.time, 'sleep'),
                  redirect_stdout(io.StringIO()), self.assertRaises(brief.subprocess.CalledProcessError) as raised):
                brief.run_stage(stage, action, brief.send_window(NOW)[1])
            self.assertEqual(action.call_count, attempts)
            self.assertNotEqual(raised.exception.returncode, 0)

    def test_collect_outage_recovers_on_a_later_attempt_with_growing_pauses(self):
        # BuyICT timed out for about 90 minutes on 6 Nov 2026; one retry after 5 minutes would lose the day.
        slept = []
        clock = Mock(return_value=NOW)
        def sleep(seconds):
            slept.append(seconds)
            clock.return_value = clock.return_value + timedelta(seconds=seconds + 900)  # each attempt takes 15 minutes
        action = Mock(side_effect=[self.failure(), self.failure(), self.failure(), None])
        with (patch.object(brief, 'utc_now', clock), patch.object(brief.time, 'sleep', side_effect=sleep),
              redirect_stdout(io.StringIO())):
            brief.run_stage('collect', action, brief.send_window(NOW)[1])
        self.assertEqual(action.call_count, 4)
        self.assertEqual(slept, [300, 1200, 2400])

    def test_later_retries_stop_at_the_latest_start(self):
        slept = []
        clock = Mock(return_value=NOW + timedelta(hours=2))  # two hours into the morning
        def sleep(seconds):
            slept.append(seconds)
            clock.return_value = clock.return_value + timedelta(seconds=seconds)
        action = Mock(side_effect=[self.failure() for _ in range(6)])
        with (patch.object(brief, 'utc_now', clock), patch.object(brief.time, 'sleep', side_effect=sleep),
              redirect_stdout(io.StringIO()), self.assertRaises(brief.subprocess.CalledProcessError)):
            brief.run_stage('collect', action, brief.send_window(NOW)[1])
        self.assertLess(action.call_count, 5)
        latest = brief.send_window(NOW)[1].replace(hour=9, minute=30)
        self.assertLessEqual(clock.return_value, latest + timedelta(seconds=1))

    def test_send_failure_never_retried(self):
        scraper = Mock(side_effect=[None, self.failure('uncertain send')])
        with (patch.object(brief, 'utc_now', return_value=NOW + timedelta(hours=2)),
              patch.object(brief, 'refresh_market_research', return_value={'status':'disabled'}),
              patch.object(brief, 'bounded_command') as collect, patch.object(brief, 'scraper', scraper),
              self.assertRaises(brief.subprocess.CalledProcessError)):
            brief.run(systemctl=Mock(return_value=SimpleNamespace(returncode=0, stdout='enabled')))
        self.assertEqual(scraper.call_count, 2)
        self.assertEqual(scraper.call_args.args[1], 'send')
        self.assertEqual(scraper.call_args.kwargs['timeout'], 900)
        self.assertEqual(collect.call_args.args[1], 7200)
        self.assertIn('ILLOURA_MORNING_DEADLINE', collect.call_args.kwargs['env'])

    def test_bounded_command_passes_timeout_and_kills_on_expiry(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (None, '')
        with patch.object(brief.subprocess, 'Popen', return_value=process) as popen:
            brief.bounded_command(['fake'], 45)
        process.communicate.assert_called_once_with(timeout=45)
        self.assertEqual(popen.call_args.kwargs['start_new_session'], os.name != 'nt')
        process.communicate.side_effect = [brief.subprocess.TimeoutExpired('fake', 45), (None, '')]
        with (patch.object(brief.subprocess, 'Popen', return_value=process),
              patch.object(brief, 'kill_tree') as kill,
              self.assertRaises(brief.subprocess.TimeoutExpired)):
            brief.bounded_command(['fake'], 45)
        kill.assert_called_once_with(process)


class ProcessGroupTests(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'Linux process group cleanup')
    def test_detached_descendant_groups_are_killed_and_parent_reaped(self):
        process = Mock(pid=100001)
        entry = SimpleNamespace(parent=SimpleNamespace(name='100002'),
                                read_text=lambda: '100002 (child) S 100001 100002 0')
        fake_path = Mock()
        fake_path.glob.return_value = [entry]
        with (patch.object(brief, 'Path', return_value=fake_path),
              patch.object(brief.os, 'getpgid', side_effect=lambda pid: pid),
              patch.object(brief.os, 'getpgrp', return_value=99999),
              patch.object(brief.os, 'killpg') as kill):
            brief.kill_tree(process)
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {100001, 100002})
        process.wait.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()

"""Cross tests written by Claude for the daily brief code Codex wrote (14 November 2026).

Each test pins one production risk. A failing test here is a finding, not a broken test.
Synthetic data only: example.com addresses, temp directories, no network.
"""
import importlib.util
import json
import random
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    'daily_brief_cross', Path(__file__).resolve().parents[1] / 'deploy/illoura_daily_brief.py')
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)

SYDNEY = brief.SYDNEY


def sydney(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=SYDNEY)


class FindSentMatching(unittest.TestCase):
    """A delivered brief reported as "not found" invites an operator to mark it undelivered and send twice."""

    ATTEMPT = {'sender': 'brief@example.com', 'recipient': 'team@example.com',
               'subject': 'Illoura daily brief | 14 November 2026 | 3 open requests to check',
               'message_id_header': '<abc@example.com>'}

    def session(self, to, sender, profile='brief@example.com'):
        def get(url, params, timeout):
            if url.endswith('/profile'):
                value = {'emailAddress': profile}
            elif url.endswith('/messages'):
                value = {'messages': [{'id': 'm1'}]}
            else:
                value = {'id': 'm1', 'labelIds': ['SENT'], 'payload': {'headers': [
                    {'name': 'Subject', 'value': self.ATTEMPT['subject']},
                    {'name': 'From', 'value': sender}, {'name': 'To', 'value': to}]}}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: value)
        return SimpleNamespace(get=Mock(side_effect=get))

    def test_display_names_around_the_addresses_still_match(self):
        message, _ = brief.find_sent(self.session('Team <team@example.com>', 'Illoura <brief@example.com>'),
                                     self.ATTEMPT)
        self.assertIsNotNone(message)

    def test_address_case_differences_still_match(self):
        # Mail addresses are case-insensitive in practice and Gmail may keep the case typed at setup.
        message, _ = brief.find_sent(self.session('Team@Example.com', 'Brief@Example.COM'), self.ATTEMPT)
        self.assertIsNotNone(message, 'a delivered brief must be recognised whatever the address case')

    def test_a_different_recipient_never_matches(self):
        message, _ = brief.find_sent(self.session('other@example.com', 'brief@example.com'), self.ATTEMPT)
        self.assertIsNone(message)

    def test_a_longer_address_that_only_contains_the_recipient_does_not_match(self):
        # "not-team@example.com" contains "team@example.com" as text but is another mailbox.
        message, _ = brief.find_sent(self.session('not-team@example.com', 'brief@example.com'), self.ATTEMPT)
        self.assertIsNone(message, 'a substring of another address must not confirm delivery')


class ListingRecordShapes(unittest.TestCase):
    """One odd listing from the live site must drop that listing, never the whole morning's brief."""

    NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)

    @staticmethod
    def closing_time(text):
        return datetime.fromisoformat(text) if text else None

    def open_record(self, **content):
        base = {'rfqOpen': True, 'is_cancelled': False,
                'mainDetails': [{'label': 'RFQ closing date', 'value': '2026-11-20T10:00:00+00:00'}]}
        base.update(content)
        return {'content': base}

    def test_a_normal_open_listing_is_open(self):
        self.assertTrue(brief.still_open(self.open_record(), self.NOW, self.closing_time))

    def test_null_details_list_is_not_open_and_does_not_raise(self):
        record = self.open_record(mainDetails=None)
        self.assertFalse(brief.still_open(record, self.NOW, self.closing_time))

    def test_null_content_is_not_open_and_does_not_raise(self):
        self.assertFalse(brief.still_open({'content': None}, self.NOW, self.closing_time))

    def test_non_dict_detail_entries_are_skipped(self):
        record = self.open_record(mainDetails=['text', None,
                                               {'label': 'RFQ closing date', 'value': '2026-11-20T10:00:00+00:00'}])
        self.assertTrue(brief.still_open(record, self.NOW, self.closing_time))

    def test_restricted_hint_survives_null_content(self):
        self.assertFalse(brief.restricted_hint({'content': None}))

    def test_closing_exactly_two_hours_ahead_is_not_open(self):
        record = self.open_record(mainDetails=[{'label': 'RFQ closing date',
                                                'value': (self.NOW + timedelta(hours=2)).isoformat()}])
        self.assertFalse(brief.still_open(record, self.NOW, self.closing_time))


class EmailWording(unittest.TestCase):
    """The brief is read by the team: no programmer artefacts and correct grammar."""

    def notes(self, count, new_rows=()):
        verified = {f'LH-{n}': {'content': {}} for n in range(count)}
        return brief.editorial_notes(date(2026, 11, 14), verified, list(new_rows), {'results': []})

    def test_one_request_is_singular_everywhere(self):
        notes = self.notes(1)
        self.assertIn('1 open request to check', notes['subject'])
        self.assertNotIn('1 open BuyICT requests', notes['preheader'])

    def test_missing_buyer_or_title_never_prints_none(self):
        notes = self.notes(1, [('LH-9', None, None, '20 November, 10:00 AM Canberra', 'https://example.com/9')])
        heading = notes['additional_sections'][0]['items'][0]['heading']
        self.assertNotIn('None', heading)

    def test_source_gap_names_are_listed_once_each(self):
        report = {'results': [{'spider': 'act_tender', 'status': 'PARTIAL'}, {'spider': 'buy_ict', 'status': 'OK'}]}
        note = brief.editorial_notes(date(2026, 11, 14), {}, [], report)['coverage_note']
        self.assertEqual(note.count('act_tender'), 1)
        self.assertNotIn('buy_ict', note)


class EditionGuardWithDamagedHistory(unittest.TestCase):
    """Run folders are kept for months; one damaged old file must not stop every future brief."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.out = self.work / 'reports/daily_sweep_today'
        self.old = self.work / 'reports/daily_sweep_old'
        self.out.mkdir(parents=True)
        self.old.mkdir()
        brief.write_json(self.out / 'editorial_notes.json', {'edition_date': '2026-11-14'})
        patcher = patch.object(brief, 'WORK', self.work)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_damaged_notes_in_an_old_run_without_any_send_do_not_block_today(self):
        (self.old / 'editorial_notes.json').write_text('{"edition_date": "2026-1', encoding='utf-8')
        brief.check_edition(self.out)  # must not raise

    def test_notes_of_the_wrong_shape_in_an_old_run_do_not_block_today(self):
        (self.old / 'editorial_notes.json').write_text('["2026-11-01"]', encoding='utf-8')
        brief.check_edition(self.out)  # must not raise

    def test_damaged_notes_next_to_an_unsettled_send_still_block(self):
        # Fail closed: we cannot tell which edition that attempt belonged to.
        (self.old / 'editorial_notes.json').write_text('{"edition_date": "2026-1', encoding='utf-8')
        brief.write_json(self.old / 'send_attempt.json', {'subject': 'x'})
        with self.assertRaises((SystemExit, ValueError)):
            brief.check_edition(self.out)

    def test_damaged_receipt_for_the_same_edition_blocks(self):
        brief.write_json(self.old / 'editorial_notes.json', {'edition_date': '2026-11-14'})
        (self.old / 'send_receipt.json').write_text('{"sent": tr', encoding='utf-8')
        with self.assertRaises((SystemExit, ValueError)):
            brief.check_edition(self.out)

    def test_stray_files_in_the_reports_folder_are_ignored(self):
        (self.work / 'reports/notes.txt').write_text('operator note', encoding='utf-8')
        brief.check_edition(self.out)

    def test_own_edition_date_must_be_a_real_date(self):
        brief.write_json(self.out / 'editorial_notes.json', {'edition_date': '14 November 2026'})
        with self.assertRaises(ValueError):
            brief.check_edition(self.out)


class SendWindowAcrossTheYear(unittest.TestCase):
    """08:15 and 12:00 are Sydney wall-clock times on every day, including clock-change days."""

    def test_every_day_of_two_years_has_a_three_hour_forty_five_window(self):
        day = datetime(2026, 1, 1, 19, 30, tzinfo=timezone.utc)  # early morning in Sydney
        for _ in range(730):
            target, cutoff = brief.send_window(day)
            self.assertEqual((target.hour, target.minute, cutoff.hour, cutoff.minute), (8, 15, 12, 0))
            self.assertEqual(target.date(), day.astimezone(SYDNEY).date())
            self.assertEqual((cutoff - target).total_seconds(), 3.75 * 3600)
            day += timedelta(days=1)

    def test_clock_change_days_keep_wall_clock_times(self):
        for moment in (sydney(2026, 4, 5, 6, 15), sydney(2026, 10, 4, 6, 15), sydney(2027, 4, 4, 6, 15)):
            target, cutoff = brief.send_window(moment.astimezone(timezone.utc))
            self.assertEqual((target.hour, target.minute), (8, 15))
            self.assertEqual(cutoff.astimezone(SYDNEY).hour, 12)


class RetryBudget(unittest.TestCase):
    """A retry may only start when the worst case still finishes before the noon cutoff."""

    def decide(self, stage, failed_at):
        """Run run_stage with a first failure at failed_at; return (retried, clock after the pause)."""
        clock = {'now': failed_at}
        calls = []

        def action(timeout):
            calls.append((clock['now'], timeout))
            if len(calls) == 1:
                raise subprocess.CalledProcessError(1, stage)

        def sleep(seconds):
            clock['now'] += timedelta(seconds=seconds)

        cutoff = failed_at.astimezone(SYDNEY).replace(hour=12, minute=0, second=0, microsecond=0)
        with patch.object(brief, 'utc_now', side_effect=lambda: clock['now']), \
                patch.object(brief.time, 'sleep', side_effect=sleep), \
                patch.object(brief, 'unresolved_guard', return_value=False), \
                patch('builtins.print'):
            try:
                brief.run_stage(stage, action, cutoff)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                return False, calls, cutoff
        return True, calls, cutoff

    def test_any_granted_retry_can_finish_before_noon_in_the_worst_case(self):
        rng = random.Random(20261114)
        for stage, limit, reserve in (('collect', brief.COLLECT_TIMEOUT_SECONDS, brief.COMPOSE_TIMEOUT_SECONDS),
                                      ('compose', brief.COMPOSE_TIMEOUT_SECONDS, 0)):
            for _ in range(300):
                failed_at = sydney(2026, 11, 14, 6, 15) + timedelta(seconds=rng.randrange(0, 6 * 3600))
                retried, calls, cutoff = self.decide(stage, failed_at.astimezone(timezone.utc))
                if not retried:
                    continue
                started, timeout = calls[1]
                worst_end = started + timedelta(seconds=timeout + reserve + brief.SEND_MARGIN_SECONDS)
                self.assertLessEqual(worst_end, cutoff, f'{stage} retry at {started} can overrun noon')
                self.assertLessEqual(timeout, limit)
                self.assertGreater(timeout, 0)

    def test_no_retry_is_granted_after_half_past_nine(self):
        for minutes in (1, 30, 120):
            failed_at = sydney(2026, 11, 14, 9, 30) + timedelta(minutes=minutes)
            retried, calls, _ = self.decide('collect', failed_at.astimezone(timezone.utc))
            self.assertFalse(retried)
            self.assertLessEqual(len(calls), 1)  # no second attempt

    def test_a_run_started_after_noon_never_calls_the_stage(self):
        # An afternoon start must not collect with a negative or zero time limit.
        retried, calls, _ = self.decide('collect', sydney(2026, 11, 14, 13, 0).astimezone(timezone.utc))
        self.assertFalse(retried)
        self.assertEqual(calls, [])


class SelectionBoundaries(unittest.TestCase):
    """Who gets into the brief is decided at the edges of the 18-hour and 21-day window."""

    NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)

    def rows(self, *items):
        return sorted((self.NOW + timedelta(hours=hours), key, 'Role', 'Agency', 'https://example.com/' + key)
                      for key, hours in items)

    def select(self, *items):
        with patch.object(brief, '_open_buyict', return_value=self.rows(*items)):
            return brief.select_ids('unused.db', self.NOW)

    def test_exactly_eighteen_hours_is_too_soon_and_exactly_twenty_one_days_is_in(self):
        self.assertEqual(self.select(('LH-1', 18), ('LH-2', 18.01), ('LH-3', 21 * 24), ('LH-4', 21 * 24 + 0.01)),
                         ['LH-2', 'LH-3'])

    def test_never_more_than_eight_and_soonest_first(self):
        chosen = self.select(*[(f'LH-{n:02d}', 100 - n) for n in range(20)])
        self.assertEqual(len(chosen), brief.MAX_SELECTED)
        self.assertEqual(chosen[0], 'LH-19')

    def test_professional_services_only_fill_when_fewer_than_three_labour_hire(self):
        self.assertEqual(self.select(('LH-1', 30), ('LH-2', 31), ('LH-3', 32), ('PCS-1', 20)), ['LH-1', 'LH-2', 'LH-3'])
        filled = self.select(('LH-1', 30), ('PCS-1', 20), ('PCS-2', 21))
        self.assertEqual(filled, ['LH-1', 'PCS-1', 'PCS-2'])

    def test_unknown_prefixes_are_never_selected(self):
        self.assertEqual(self.select(('XYZ-1', 30), ('lh-2', 30)), [])


class SendNeverRepeats(unittest.TestCase):
    """The send step: one attempt, under the lock, and an existing receipt elsewhere stops it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.out = self.work / 'reports/daily_sweep_today'
        self.out.mkdir(parents=True)
        brief.write_json(self.out / 'editorial_notes.json', {'edition_date': '2026-11-14'})
        brief.write_json(self.work / 'latest_run.json', {'run_dir': str(self.out), 'status': 'ready', 'sent': False})
        for target, value in (('WORK', self.work), ('TOKEN', self.work / 'token.json')):
            patcher = patch.object(brief, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def send(self, helper):
        with patch.dict('sys.modules', {'send_gmail': SimpleNamespace(send=helper)}), \
                patch.object(brief, 'send_lock', side_effect=nullcontext), patch('builtins.print'):
            brief.send(self.out)

    def test_helper_failure_leaves_the_run_unsent_and_is_not_retried(self):
        helper = Mock(side_effect=RuntimeError('gmail down'))
        with self.assertRaises(RuntimeError):
            self.send(helper)
        self.assertEqual(helper.call_count, 1)
        self.assertFalse(json.loads((self.work / 'latest_run.json').read_text())['sent'])

    def test_success_marks_the_latest_run_sent_with_the_receipt_time(self):
        def helper(out, token):
            brief.write_json(Path(out) / 'send_receipt.json',
                             {'sent': True, 'recorded_at_utc': '2026-11-13T21:15:03+00:00', 'message_id': 'm'})
        self.send(helper)
        state = json.loads((self.work / 'latest_run.json').read_text())
        self.assertEqual((state['status'], state['sent'], state['sent_at_utc']),
                         ('sent', True, '2026-11-13T21:15:03+00:00'))

    def test_a_receipt_for_this_edition_in_another_folder_blocks_before_the_helper(self):
        other = self.work / 'reports/daily_sweep_earlier'
        other.mkdir()
        brief.write_json(other / 'editorial_notes.json', {'edition_date': '2026-11-14'})
        brief.write_json(other / 'send_receipt.json', {'sent': True})
        helper = Mock()
        with self.assertRaises(SystemExit):
            self.send(helper)
        helper.assert_not_called()


if __name__ == '__main__':
    unittest.main()

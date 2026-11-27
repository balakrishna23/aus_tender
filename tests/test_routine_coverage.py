from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


class CoverageTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('routine_composer', ROOT/'routine/scripts/compose_checked_brief.py')
        self.composer = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(ROOT/'routine/scripts'), *sys.path]):
            spec.loader.exec_module(self.composer)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name)

    def write(self, name, value):
        (self.out/name).write_text(json.dumps(value))

    def test_wrapper_and_individual_failures(self):
        self.write('research_refresh.json', [{'step': 'wa_contracts', 'status': 'failed'},
            {'step': 'curated_source_checks', 'status': 'completed'},
            {'step': 'official_sources', 'status': 'failed'}])
        (self.out/'curated_source_checks.log').write_text('\n'.join([
            json.dumps({'source_url': 'https://example.com/failed', 'status': 'unavailable'}),
            json.dumps({'source_url': 'https://example.org/blocked', 'status': 'not fetched: robots disallows this path'}),
            json.dumps({'source_url': 'https://example.com/ok', 'status': 'fetched'}),
            'Fetched 1 of 3 curated source URLs']))
        self.assertEqual(self.composer.supplemental_failures(self.out), [
            'https://example.com/failed', 'https://example.org/blocked', 'official_sources', 'wa_contracts'])

    def test_staged_curated_manifest(self):
        self.write('research_refresh.json', [])
        self.write('curated_source_checks.json', [{'source_url': 'https://example.com/missing', 'status': 'unavailable'}])
        self.assertEqual(self.composer.supplemental_failures(self.out), ['https://example.com/missing'])

    def test_missing_status_is_visible(self):
        self.assertEqual(self.composer.supplemental_failures(self.out), ['research_refresh (status unavailable)'])

    def test_success_has_no_failures(self):
        self.write('research_refresh.json', [{'step': name, 'status': 'completed'} for name in
            ('official_sources', 'buyict_news', 'curated_source_checks', 'wa_contracts')])
        self.assertEqual(self.composer.supplemental_failures(self.out), [])

    def compose(self, success=True, results=None, run_state=None, feed='rebuilt', config=None):
        notes = {'edition_date': str(datetime.now(ZoneInfo('Asia/Kolkata')).date()),
            'opportunities': {}, 'subject': 'Example brief', 'preheader': 'Example', 'introduction': 'Example'}
        notes.update(config or {})
        self.write('editorial_notes.json', notes)
        if run_state is not None:
            self.write('cloud_run.json', run_state)
        self.write('source_refresh.json', {'success': success, 'feed': feed,
            'results': results if results is not None else self.named()})
        self.write('finalization.json', {'steps': [{'step': name, 'exit_code': 0} for name in
            ('intelligence', 'data_quality', 'newsletter_evidence')]})
        self.write('official_source_collection.json', [{'organisation': 'Example organisation', 'status': 'unavailable'}])
        self.write('research_refresh.json', [{'step': 'wa_contracts', 'status': 'failed'}])
        self.write('baseline.json', {'contracts': 0, 'contract_versions': 0})
        self.write('newsletter_review.json', {})
        (self.out/'newsletter_preview.txt').write_text('Example')
        (self.out/'newsletter_preview.html').write_text('<p>Example</p>')
        with patch.object(sys, 'argv', ['compose', '--out', str(self.out)]), \
             patch.object(self.composer, 'load_mail_config', return_value=('sender@example.com', 'reader@example.org')), \
             patch.object(self.composer, 'load_link_id', return_value='example-link'):
            self.composer.main()
        return json.loads((self.out/'newsletter_editorial.json').read_text())

    def listing_fixture(self, count=10):
        spec = importlib.util.spec_from_file_location('fixture_daily_brief', ROOT/'deploy/illoura_daily_brief.py')
        brief = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(brief)
        now = datetime.now(timezone.utc)
        end = (now + timedelta(days=7)).astimezone(ZoneInfo('Australia/Canberra')).replace(hour=17, minute=0)
        (self.out/'shortlist').mkdir(exist_ok=True)
        verified = {}
        for n in range(count, 0, -1):
            key = f'LH-{8600+n:05d}'
            record = {'checked_at_utc': now.isoformat(), 'source_url': 'https://example.com/'+key,
                'content': {'title': 'Data engineer', 'rfqOpen': True,
                    'description': 'Selected sellers only.' if n == count else 'Deliver services.',
                    'mainDetails': [{'label': 'Buyer', 'value': 'Example Agency'},
                        {'label': 'RFQ closing date', 'value': end.strftime('%d %B %Y %I:%M %p')},
                        {'label': 'Deadline for asking questions', 'value': end.strftime('%d %B %Y %I:%M %p')}],
                    'requirements': [{'label': field, 'value': value} for field, value in [
                        ('Location of work', 'ACT'), ('Working arrangements', 'Hybrid'),
                        ('Security clearance', 'NV1'), ('Maximum number of candidates per seller', '2'),
                        ('Initial contract duration', '12 months')]]}}
            verified[key] = record
            self.write('shortlist/'+key+'.json', record)
        baseline = list(verified)[1:]
        self.write('baseline_buyict_ids.json', baseline)
        return brief.editorial_notes(now.astimezone(ZoneInfo('Asia/Kolkata')).date(), verified, baseline, {'results': []})

    def test_ten_item_email_under_650_words_with_details_and_access_warning(self):
        config = self.listing_fixture()
        package = self.compose(config=config)
        self.assertEqual([s['title'] for s in package['sections']], ['Newest openings'])
        items = package['sections'][0]['items']
        self.assertEqual(len(items), 10)
        self.assertEqual([i['heading'].split(' | ')[-1] for i in items], list(config['opportunities']))
        text = (self.out/'newsletter_preview.txt').read_text()
        self.assertLess(len(text.split()), 650)
        self.assertNotIn('Questions', text)
        self.assertIn('Location: ACT; Work: Hybrid; Clearance: NV1; Candidate cap per seller: 2; Initial term: 12 months.', text)
        self.assertIn('check existing access', items[0]['paragraphs'][-1])
        self.assertEqual(text.count('check existing access'), 1)
        self.assertEqual(text.count('New since yesterday.'), 1)
        self.assertTrue(items[0]['paragraphs'][0].startswith('New since yesterday. Closes '))
        self.assertTrue(all(item['paragraphs'][0].startswith('Closes ') for item in items[1:]))
        self.assertEqual(package['preheader'], '10 newest BuyICT openings, rechecked this morning.')

    def test_missing_details_do_not_create_a_period_paragraph_and_source_text_is_clean(self):
        config = self.listing_fixture(1)
        path = self.out/'shortlist/LH-08601.json'
        record = json.loads(path.read_text())
        record['content']['title'] = 'Data \ufffd engineer'
        record['content']['requirements'] = []
        self.write('shortlist/LH-08601.json', record)
        package = self.compose(config=config)
        item = package['sections'][0]['items'][0]
        self.assertEqual(item['heading'], 'Example Agency | Data - engineer | LH-08601')
        self.assertNotIn('.', item['paragraphs'])
        self.assertNotIn('\ufffd', (self.out/'newsletter_preview.html').read_text())

    def test_public_check_and_open_guards_remain(self):
        for label, mutate in (
            ('Stale public check', lambda r: r.update(checked_at_utc='2020-01-01T00:00:00+00:00')),
            ('no longer open', lambda r: r['content'].update(rfqOpen=False)),
            ('no longer open', lambda r: r['content'].update(is_cancelled=True)),
            ('no longer open', lambda r: r['content']['mainDetails'][1].update(value='01 January 2020'))):
            with self.subTest(label=label):
                config = self.listing_fixture(1)
                record = json.loads((self.out/'shortlist/LH-08601.json').read_text())
                mutate(record)
                self.write('shortlist/LH-08601.json', record)
                with self.assertRaisesRegex(ValueError, label):
                    self.compose(config=config)

    def test_receipt_and_edition_guards_remain(self):
        with self.assertRaisesRegex(ValueError, 'Editorial date'):
            self.compose(config={'edition_date': '2020-01-01'})
        self.write('send_receipt.json', {'sent': True})
        with self.assertRaisesRegex(ValueError, 'send receipt'):
            self.compose()

    def test_only_newest_section_even_with_research_sections(self):
        package = self.compose(config={'additional_sections': [{'title': 'Research', 'items': []}],
                                       'coverage_note': 'Internal run details'})
        self.assertEqual([s['title'] for s in package['sections']], ['Newest openings'])
        # With nothing selected the email says so instead of showing an empty section.
        self.assertEqual([i['heading'] for i in package['sections'][0]['items']], ['No openings to list today'])
        text = (self.out/'newsletter_preview.txt').read_text()
        for hidden in ('Research', 'Coverage and access', 'wa_contracts', 'Example organisation', 'Internal run details'):
            self.assertNotIn(hidden, text)
        self.assertEqual(package['from'], 'sender@example.com')

    def named(self, **problems):
        names = ('contract_api', 'aus_contract', 'aus_tender', 'planned_procurement', 'standing_offer', 'buy_ict', 'act_tender', 'nsw_tender')
        return [dict({'spider': name, 'status': 'OK'}, **problems.get(name, {})) for name in names]

    def test_source_gaps_recorded_but_absent_from_email(self):
        self.compose(success=False, results=self.named(planned_procurement={'status': 'PARTIAL', 'crawl_failed': True}),
                     run_state={'source_exit_code': 1, 'source_gaps': ['planned_procurement']})
        review = json.loads((self.out/'newsletter_review.json').read_text())
        self.assertEqual(review['source_gaps'], ['planned_procurement'])
        self.assertNotIn('planned_procurement', (self.out/'newsletter_preview.txt').read_text())

    def test_gap_without_approval_or_with_another_failure_stops_the_brief(self):
        gap = self.named(planned_procurement={'status': 'PARTIAL'})
        ok = {'source_exit_code': 1, 'source_gaps': ['planned_procurement']}
        for label, kwargs in {
            'no run state': dict(success=False, results=gap),
            'run cut short': dict(success=False, results=gap, run_state={'source_exit_code': 124, 'source_gaps': ['planned_procurement']}),
            'other gaps approved': dict(success=False, results=gap, run_state={'source_exit_code': 1, 'source_gaps': ['nsw_tender']}),
            'feed failed as well': dict(success=False, results=gap, run_state=ok, feed='releases feed: FAILED (x)'),
            'runner says success': dict(success=True, results=gap, run_state=ok),
            'success flag missing': dict(success=None, results=gap, run_state=ok),
        }.items():
            with self.subTest(label):
                (self.out/'cloud_run.json').unlink(missing_ok=True)
                with self.assertRaisesRegex(ValueError, 'Source sweep'):
                    self.compose(**kwargs)

    def test_buyict_problem_stops_the_brief(self):
        with self.assertRaisesRegex(ValueError, 'Source sweep'):
            self.compose(success=False, results=self.named(buy_ict={'status': 'FAIL(1)'}))

    def test_mandatory_gate_unchanged(self):
        with self.assertRaisesRegex(ValueError, 'Source sweep'):
            self.compose(success=False)

    def test_success_flag_does_not_override_broken_report(self):
        for kwargs in ({'results': [dict(spider='buy_ict', status='OK')] * 8},
                       {'feed': 'releases feed: FAILED (disk error)'},
                       {'feed': ''}, {'feed': True}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, 'Source sweep'):
                self.compose(**kwargs)

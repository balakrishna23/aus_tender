from contextlib import redirect_stdout
from datetime import datetime
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

    def compose(self, success=True):
        self.write('editorial_notes.json', {'edition_date': str(datetime.now(ZoneInfo('Asia/Kolkata')).date()),
            'opportunities': {}, 'subject': 'Example brief', 'preheader': 'Example', 'introduction': 'Example'})
        self.write('source_refresh.json', {'success': success, 'feed': 'rebuilt',
            'results': [{'spider': str(i), 'status': 'OK'} for i in range(8)]})
        self.write('finalization.json', {'steps': [{'step': name, 'exit_code': 0} for name in
            ('intelligence', 'data_quality', 'newsletter_evidence')]})
        self.write('official_source_collection.json', [{'organisation': 'Example organisation', 'status': 'unavailable'}])
        self.write('research_refresh.json', [{'step': 'wa_contracts', 'status': 'failed'}])
        self.write('baseline.json', {'contracts': 0, 'contract_versions': 0})
        self.write('newsletter_review.json', {})
        (self.out/'newsletter_preview.txt').write_text('Example')
        (self.out/'newsletter_preview.html').write_text('<p>Example</p>')
        database = Mock()
        database.__enter__ = Mock(return_value=database)
        database.__exit__ = Mock(return_value=False)
        database.execute.return_value.fetchone.return_value = (0,)
        with patch.object(sys, 'argv', ['compose', '--out', str(self.out)]), \
             patch.object(self.composer.sqlite3, 'connect', return_value=database), \
             patch.object(self.composer, 'render'), \
             patch.object(self.composer, 'load_mail_config', return_value=('sender@example.com', 'reader@example.org')), \
             patch.object(self.composer, 'load_link_id', return_value='example-link'):
            self.composer.main()
        return json.loads((self.out/'newsletter_editorial.json').read_text())

    def test_coverage_text_distinguishes_supplemental_failures(self):
        package = self.compose()
        paragraphs = package['sections'][-1]['items'][0]['paragraphs']
        self.assertIn('All eight procurement source stages completed successfully.', paragraphs[0])
        self.assertIn('Additional research pages unavailable: Example organisation.', paragraphs[1])
        self.assertIn('Supplemental research failures (separate from the eight procurement collectors): wa_contracts.', paragraphs[1])
        self.assertEqual(package['from'], 'sender@example.com')

    def test_mandatory_gate_unchanged(self):
        with self.assertRaisesRegex(ValueError, 'Source sweep'):
            self.compose(success=False)

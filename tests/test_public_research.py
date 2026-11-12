"""A new source review must not silently freshen older findings."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'local_assistant'))
import public_research


class ResearchDatesTests(unittest.TestCase):
    def test_each_record_keeps_its_actual_review_date(self):
        fresh = datetime.now(timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research.json'
            path.write_text(json.dumps({
                'reviewed_at_utc': '2020-01-01T00:00:00+00:00',
                'review_method': 'Official source review',
                'records': [
                    {'organisation': 'Prime', 'kind': 'RAP', 'title': 'Old'},
                    {'organisation': 'Prime', 'kind': 'RAP', 'title': 'Fresh',
                     'reviewed_at_utc': fresh},
                ]}), encoding='utf-8')
            with patch.object(public_research, 'CHECKS', Path(directory) / 'absent.json'):
                old, new = public_research.findings(['Prime'], path=path)
            self.assertGreater(old['review_age_days'], 30)
            self.assertEqual(new['review_age_days'], 0)
            self.assertEqual(new['reviewed_at_utc'], fresh)
            self.assertIn('needs recheck', old['review_status'])


if __name__ == '__main__':
    unittest.main()

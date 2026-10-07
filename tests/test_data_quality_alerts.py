"""Data-quality alerts after every run (spec section 11)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_spiders  # noqa: E402


class AlertTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        for i, saved in enumerate([120, 95, 0, 140]):
            (self.dir / f'2026100{i}-000000.json').write_text(json.dumps(
                {'results': [{'spider': 'contract_api', 'saved': saved}, {'spider': 'act_tender', 'saved': 1}]}))

    def test_row_count_drop_of_more_than_30_percent(self):
        warnings = run_spiders.data_quality_warnings(
            [{'spider': 'aus_tender', 'before': 1000, 'after': 600, 'saved': 5}], self.dir)
        self.assertEqual(len(warnings), 1)
        self.assertIn('more than 30%', warnings[0])

    def test_nothing_new_from_a_source_that_usually_brings_records(self):
        warnings = run_spiders.data_quality_warnings(
            [{'spider': 'contract_api', 'before': 10, 'after': 10, 'saved': 0},
             {'spider': 'act_tender', 'before': 10, 'after': 10, 'saved': 0}], self.dir)
        self.assertEqual(warnings, ['contract_api: nothing new this run (usually about 120)'])

    def test_a_normal_run_is_quiet(self):
        self.assertEqual(run_spiders.data_quality_warnings(
            [{'spider': 'contract_api', 'before': 100, 'after': 180, 'saved': 80}], self.dir), [])


if __name__ == '__main__':
    unittest.main()

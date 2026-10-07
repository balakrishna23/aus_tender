"""The engine-views spreadsheet is optional until a blank sheet exists for it."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import publish_sheets  # noqa: E402


class OptionalSheetTests(unittest.TestCase):
    def run_publish(self, error):
        def spreadsheet_for(gc, key, registry):
            if key == 'intelligence':
                raise RuntimeError(error)
            return Mock(url=f'https://sheet/{key}')
        with patch.object(publish_sheets, 'client'), patch.object(publish_sheets, 'load_registry', return_value={}), \
                patch.object(publish_sheets, 'spreadsheet_for', side_effect=spreadsheet_for), \
                patch.object(publish_sheets, 'publish'), patch.object(publish_sheets, '_track_access'), \
                patch.object(publish_sheets.activity_log, 'record_share'), \
                patch.dict(publish_sheets.DATASETS, {'releases': ('R', lambda: {}),
                                                     'intelligence': ('I', lambda: {})}, clear=True):
            return publish_sheets.publish_all()

    def test_no_spare_sheet_is_skipped_not_failed(self):
        links = self.run_publish("No spare spreadsheet in the folder for 'Illoura - Engine Views': create one")
        self.assertEqual(links, {'releases': 'https://sheet/releases'})

    def test_other_errors_still_fail(self):
        with self.assertRaises(RuntimeError):
            self.run_publish('quota exceeded')


if __name__ == '__main__':
    unittest.main()

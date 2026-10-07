"""The saved workbook includes tracking and brief data without turning notes into formulas."""
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
import action_tracker
import build_intelligence
import export_views
import prime_briefs


class ExportViewsTests(unittest.TestCase):
    def test_tracker_brief_and_today_columns_in_saved_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'AUSTENDER_DB_DIR': directory}):
            root = Path(directory)
            a = dict(organisation='DXC', action_type='Tier-2 offer to prime', priority='ACT NOW', score=86, owner='Lane')
            with closing(action_tracker.connect()) as conn:
                action_tracker.merge(conn, [a], date.today())
                action_tracker.record_edits(conn, [dict(action_id=a['action_id'], notes='=1+1', status='accepted')])
            with closing(sqlite3.connect(root / 'intelligence.db')) as conn:
                build_intelligence.write(conn, 'build_info', [{'built_at': '2026-10-07T20:50:00'}])
                build_intelligence.write(conn, 'today', [dict(a, rank=1, status='accepted')],
                                         ['rank'] + build_intelligence.ACTION_COLUMNS)
                build_intelligence.write(conn, 'prime_briefs', [dict(organisation='DXC', steps='pending: bench')],
                                         prime_briefs.COLUMNS)
                conn.commit()
            with patch.object(export_views, 'PROJECT_DIR', root), redirect_stdout(io.StringIO()):
                export_views.main()
            workbook = load_workbook(next((root / 'exports').glob('*.xlsx')))
            try:
                tracker = workbook['Tracker']
                headers = [c.value for c in tracker[2]]
                note = tracker.cell(3, headers.index('notes') + 1)
                self.assertEqual((note.value, note.data_type), ('=1+1', 's'))
                self.assertEqual(workbook['Prime briefs']['A3'].value, 'DXC')
                self.assertIn('action_id', [c.value for c in workbook['TODAY'][2]])
                self.assertEqual(tracker.freeze_panes, 'A3')
            finally:
                workbook.close()

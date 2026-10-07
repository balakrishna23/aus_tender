"""Action tracking (spec engine 7, section 12): people's edits are kept, TODAY honours them."""
import os
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import action_tracker as at  # noqa: E402

TODAY = date(2026, 10, 7)


def action(org, kind='Tier-2 offer to prime', score=86, subject=None):
    return dict(priority='ACT NOW', score=score, organisation=org, action_type=kind, why_now='why', owner='Lane',
                person_and_route='route', offer='offer', due='2026-10-09', contact_safety='SAFE_GENERAL',
                evidence='https://www.tenders.gov.au/Cn/Show/x', subject=subject or org)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.conn = at.connect()
        self.addCleanup(self.conn.close)

    def test_ids_are_stable_and_edits_survive_a_rerun(self):
        dxc, ey = action('DXC'), action('EY')
        at.merge(self.conn, [dxc, ey], TODAY)
        self.assertEqual(dxc['action_id'], at.action_id('Tier-2 offer to prime', 'DXC'))
        changed = at.record_edits(self.conn, [dict(action_id=dxc['action_id'], status='Accepted', stage='contacted',
                                                   follow_up='14/10/2026', notes='Intro asked via mutual contact',
                                                   owner='Lane')])
        self.assertEqual(changed, 4)
        rerun = action('DXC', score=90)
        at.merge(self.conn, [rerun], TODAY)        # evidence refreshed, people's fields untouched
        row = self.conn.execute('SELECT * FROM actions WHERE action_id = ?', (dxc['action_id'],)).fetchone()
        self.assertEqual((row['score'], row['status'], row['stage'], row['follow_up'], row['notes']),
                         (90, 'accepted', 'contacted', '2026-10-14', 'Intro asked via mutual contact'))
        history = self.conn.execute('SELECT field, new_value FROM action_history ORDER BY field').fetchall()
        self.assertIn(('status', 'accepted'), [tuple(h) for h in history])

    def test_invalid_values_are_ignored(self):
        a = action('DXC')
        at.merge(self.conn, [a], TODAY)
        self.assertEqual(at.record_edits(self.conn, [dict(action_id=a['action_id'], status='maybe',
                                                          follow_up='next week')]), 0)

    def test_emptied_status_or_stage_keeps_stored_value(self):
        a = action('DXC')
        at.merge(self.conn, [a], TODAY)
        self.assertEqual(at.record_edits(self.conn, [dict(action_id=a['action_id'], status='', stage=' ')]), 0)
        row = self.conn.execute('SELECT status, stage FROM actions').fetchone()
        self.assertEqual(tuple(row), ('proposed', 'identified'))

    def test_today_drops_closed_and_brings_back_due_follow_ups(self):
        done, snoozed, due, fresh = action('Done Co'), action('Later Co'), action('Due Co', score=50), action('New Co')
        at.merge(self.conn, [done, snoozed, due, fresh], TODAY)
        at.record_edits(self.conn, [
            dict(action_id=done['action_id'], status='done', stage='won'),
            dict(action_id=snoozed['action_id'], status='snoozed', follow_up='2026-11-01'),
            dict(action_id=due['action_id'], status='accepted', follow_up='2026-10-01', notes='Waiting on CVs')])
        today = at.apply_to_today(self.conn, [done, snoozed, fresh], TODAY)
        self.assertEqual([(a['organisation'], a['priority']) for a in today],
                         [('Due Co', 'OVERDUE'), ('New Co', 'ACT NOW')])
        self.assertIn('Waiting on CVs', today[0]['why_now'])
        self.assertEqual([a['rank'] for a in today], [1, 2])

    def test_tracker_lists_due_first_and_closed_last(self):
        a, b, c = action('A', score=10), action('B', score=99), action('C', score=50)
        at.merge(self.conn, [a, b, c], TODAY)
        at.record_edits(self.conn, [dict(action_id=b['action_id'], status='declined'),
                                    dict(action_id=a['action_id'], status='accepted', follow_up='2026-01-01')])
        self.assertEqual([r['organisation'] for r in at.tracker_rows(self.conn)], ['A', 'C', 'B'])

    def test_picked_action_is_promoted_when_follow_up_overdue(self):
        a = action('DXC')
        at.merge(self.conn, [a], TODAY)
        at.record_edits(self.conn, [dict(action_id=a['action_id'], status='accepted', follow_up='2026-10-06')])
        result = at.apply_to_today(self.conn, [a], TODAY)
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['priority'], result[0]['due']), ('OVERDUE', '2026-10-06'))
        at.record_edits(self.conn, [dict(action_id=a['action_id'], status='proposed', follow_up='')])
        self.assertEqual(at.apply_to_today(self.conn, [a], TODAY)[0]['priority'], 'ACT NOW')


if __name__ == '__main__':
    unittest.main()

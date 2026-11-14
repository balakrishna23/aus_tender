"""Cross tests written by Claude for the retention (deletion) policy Codex wrote (14 November 2026).

Deleting published data is the one irreversible thing this system does on its own, so the
policy is checked over many generated layouts rather than a few hand-picked ones.
"""
import random
import unittest
from datetime import timedelta
from unittest.mock import patch

import test_cloud_retention as base  # module import: its own tests are not collected twice

ops = base.ops


class RetentionOverGeneratedLayouts(unittest.TestCase):
    def layout(self, seed):
        helper = base.RetentionTests('test_retention_roots_must_not_overlap')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        rng = random.Random(seed)
        count = rng.randrange(1, 15)
        # Days repeat on purpose (equal timestamps) and reach into the future (a wrong clock).
        days = [rng.choice([-40, -9, -3, -3, -1, 0, 0, 2, 30]) + rng.randrange(0, 3) for _ in range(count)]
        valid = {}
        for index, day in enumerate(days):
            name = 'generation-%02d' % index
            helper.generation(name, day)
            valid[name] = day
        protected = set(rng.sample(sorted(valid), k=min(len(valid), rng.randrange(1, 3))))
        damaged = set()
        if rng.random() < 0.6:
            broken = helper.generation('generation-damaged', 0)
            (broken / 'manifest.json').write_text('{"snapshot_finished_utc": "2026-01', encoding='utf-8')
            damaged.add(broken.name)
        if rng.random() < 0.6:
            naive = helper.generation('generation-naive-time', 0)
            ops.atomic_json(naive / 'manifest.json', {'snapshot_finished_utc': '2026-01-01T00:00:00',
                                                      'datasets': {'fixture': {'file': 'fixture.db'}}})
            damaged.add(naive.name)
        note = rng.random() < 0.6
        if note:
            (helper.served / 'generation-note.txt').write_text('operator note', encoding='utf-8')
        return helper, valid, protected, damaged, note

    def expected_survivors(self, helper, valid, protected):
        others = sorted(((helper.now + timedelta(days=day), name) for name, day in valid.items()
                         if name not in protected), reverse=True)
        return protected | {name for _, name in others[:3]}

    def test_policy_holds_for_every_generated_layout(self):
        for seed in range(60):
            with self.subTest(seed=seed):
                helper, valid, protected, damaged, note = self.layout(seed)
                expected = self.expected_survivors(helper, valid, protected)
                with patch.object(ops, '_protected_generations', return_value=set(protected)):
                    first = ops.prune(helper.served, helper.backups, now=helper.now)
                    second = ops.prune(helper.served, helper.backups, now=helper.now)
                left = {path.name for path in helper.served.iterdir() if path.is_dir()}
                self.assertEqual(left - damaged, expected)
                # Anything the policy cannot read is kept for a person to look at, and reported.
                self.assertLessEqual(damaged, left)
                self.assertEqual(first['status'], 'attention' if damaged else 'ok')
                self.assertEqual({item['name'] for item in first['errors']}, damaged)
                # Deleting is never repeated or extended by running again.
                self.assertEqual(second['deleted'], [])
                self.assertEqual(set(first['kept_generations']), expected)
                if note:
                    self.assertTrue((helper.served / 'generation-note.txt').is_file())

    def test_dry_run_plans_exactly_what_a_real_run_then_deletes(self):
        for seed in range(20):
            with self.subTest(seed=seed):
                helper, valid, protected, _, _ = self.layout(seed)
                with patch.object(ops, '_protected_generations', return_value=set(protected)):
                    planned = ops.prune(helper.served, helper.backups, now=helper.now, dry_run=True)
                    self.assertEqual(planned['deleted'], [])
                    self.assertFalse((helper.served / 'retention.json').exists())
                    done = ops.prune(helper.served, helper.backups, now=helper.now)
                self.assertEqual(sorted(item['name'] for item in planned['planned_deletions']),
                                 sorted(item['name'] for item in done['deleted']))


if __name__ == '__main__':
    unittest.main()

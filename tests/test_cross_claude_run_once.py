"""Cross tests written by Claude for the routine runner's deadlines (code by Codex, 14 November 2026)."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'run_once_cross', Path(__file__).resolve().parents[1] / 'routine/run_once.py')
run_once = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_once)

BUDGET = run_once.ROUTINE_BUDGET_SECONDS


class MorningDeadlineInput(unittest.TestCase):
    """The deadline arrives through an environment variable; a bad value must not cancel the morning."""

    def deadline(self, value, monotonic=1000.0, wall=1_800_000_000.0):
        env = {} if value is None else {'ILLOURA_MORNING_DEADLINE': value}
        return run_once.routine_deadline(env, monotonic, wall) - monotonic

    def test_no_deadline_gives_the_normal_budget(self):
        self.assertEqual(self.deadline(None), BUDGET)

    def test_unreadable_values_fall_back_to_the_normal_budget(self):
        for value in ('soon', '', ' ', 'nan', 'inf', '-inf', '12:00'):
            with self.subTest(value=value):
                self.assertEqual(self.deadline(value), BUDGET)

    def test_an_earlier_morning_deadline_shortens_the_budget(self):
        self.assertEqual(self.deadline(str(1_800_000_000.0 + 600)), 600)

    def test_a_later_morning_deadline_never_extends_the_budget(self):
        self.assertEqual(self.deadline(str(1_800_000_000.0 + 10 * BUDGET)), BUDGET)

    def test_a_deadline_in_the_past_leaves_no_time(self):
        self.assertLess(self.deadline('1'), 0)

    def test_stage_limits_fit_inside_the_budgets_the_coordinator_assumes(self):
        # Sources and research run side by side, then evidence: together within the routine budget.
        self.assertLessEqual(max(run_once.SOURCE_TIMEOUT_SECONDS, run_once.RESEARCH_TIMEOUT_SECONDS)
                             + run_once.FINALIZATION_TIMEOUT_SECONDS, BUDGET)
        self.assertLessEqual(run_once.SPIDER_TIMEOUT_SECONDS, run_once.SOURCE_TIMEOUT_SECONDS)


if __name__ == '__main__':
    unittest.main()

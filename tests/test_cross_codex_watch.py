"""Offline execution of the shipped Lambda plus explicit alarm arithmetic."""
import contextlib
import io
import os
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

TEMPLATE = (Path(__file__).resolve().parents[1] / 'deploy/aws/illoura-watch.yaml').read_text(encoding='utf-8')


class WatchRisks(unittest.TestCase):
    def test_inline_lambda_emits_boolean_for_all_response_states(self):
        """Malformed endpoints must emit failure metrics without printing bodies."""
        source = textwrap.dedent(TEMPLATE.split('        ZipFile: |\n', 1)[1].split('\n  CheckSchedule:', 1)[0])
        cloudwatch = Mock()
        fake_boto = SimpleNamespace(client=Mock(return_value=cloudwatch))
        scope = {}
        with patch.dict(sys.modules, {'boto3': fake_boto}):
            exec(compile(source, 'inline_watch_lambda', 'exec'), scope)
        for status, body, expected in ((200, b'{"status":"ok"}', 1), (503, b'{"status":"ok"}', 0),
                                        (200, b'{"status":"attention"}', 0), (200, b'[]', 0),
                                        (200, b'private-response-marker', 0), (200, b'', 0)):
            response = Mock(status=status)
            response.read.return_value = body
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            with patch.dict(os.environ, {'READYZ_URL': 'https://example.com/readyz'}), patch('urllib.request.urlopen', return_value=response), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(scope['handler']({}, None), {'ok': expected})
            self.assertNotIn('private-response-marker', output.getvalue())
            self.assertEqual(cloudwatch.put_metric_data.call_args.kwargs['MetricData'][0]['Value'], expected)

    def test_network_failure_emits_zero(self):
        """An unreachable server must record failure rather than lose a metric."""
        source = textwrap.dedent(TEMPLATE.split('        ZipFile: |\n', 1)[1].split('\n  CheckSchedule:', 1)[0])
        cloudwatch = Mock()
        scope = {}
        with patch.dict(sys.modules, {'boto3': SimpleNamespace(client=lambda _: cloudwatch)}):
            exec(compile(source, 'inline_watch_lambda', 'exec'), scope)
        with patch.dict(os.environ, {'READYZ_URL': 'https://example.com/readyz'}), patch('urllib.request.urlopen', side_effect=TimeoutError()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scope['handler']({}, None), {'ok': 0})

    def test_alarm_outage_durations_and_phase(self):
        """Short reboots must not page, but sustained mostly-failing periods must."""
        for setting in ('Statistic: Average', 'Period: 900', 'EvaluationPeriods: 2', 'DatapointsToAlarm: 2',
                        'Threshold: 0.5', 'ComparisonOperator: LessThanThreshold', 'TreatMissingData: breaching',
                        'ScheduleExpression: rate(5 minutes)'):
            self.assertIn(setting, TEMPLATE)
        def alarms(samples):
            means = [None if not any(v is not None for v in samples[n:n+3]) else
                     sum(v for v in samples[n:n+3] if v is not None) / sum(v is not None for v in samples[n:n+3])
                     for n in range(0, len(samples), 3)]
            breaches = [m is None or m < 0.5 for m in means]
            return any(a and b for a, b in zip(breaches, breaches[1:]))
        for duration in (1, 10, 20, 30, 45):
            outcomes = []
            for phase in range(15):
                samples = [0 if phase <= t < phase + duration else 1 for t in range(-30, 105, 5)]
                outcomes.append(alarms(samples))
            if duration <= 10:
                self.assertFalse(any(outcomes))
            elif duration >= 30:
                self.assertTrue(all(outcomes))
            else:
                self.assertTrue(any(outcomes))
                self.assertFalse(all(outcomes))
        self.assertTrue(alarms([None] * 9))
        self.assertFalse(alarms([0, 1] * 12))  # 2/3 failures alternate with 1/3: never two breaching periods.

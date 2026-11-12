"""Retention policy tests use only disposable, synthetic filesystem fixtures."""
from contextlib import closing, contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    'retention_ops', Path(__file__).resolve().parents[1] / 'deploy/illoura_cloud_ops.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
LINUX_ONLY = unittest.skipIf(os.name == 'nt', 'Real symlink retention is exercised on Linux')


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.cleanup)
        self.served = self.root / 'served'
        self.backups = self.root / 'backups'
        self.served.mkdir()
        self.backups.mkdir()
        self.now = datetime(2026, 1, 20, tzinfo=timezone.utc)

    def cleanup(self):
        # The absolute recursive-cleanup boundary is the fixture returned by
        # TemporaryDirectory, never a deployment path or derived user input.
        if not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise ValueError('Unexpected test cleanup boundary')
        for directory, children, files in os.walk(self.root, followlinks=False):
            parent = Path(directory)
            parent.chmod(0o700)
            children[:] = [name for name in children if not (parent / name).is_symlink()]
            for name in files:
                file = parent / name
                if not file.is_symlink():
                    file.chmod(0o600)
        self.temp.cleanup()

    def generation(self, name, day, size=4096):
        path = self.served / name
        path.mkdir()
        (path / 'fixture.db').write_bytes(b'x' * size)
        ops.atomic_json(path / 'manifest.json', {
            'snapshot_finished_utc': (self.now + timedelta(days=day)).isoformat(),
            'datasets': {'fixture': {'file': 'fixture.db'}}})
        return path

    def backup(self, name, day):
        path = self.backups / name
        path.mkdir()
        (path / 'state.txt').write_text('synthetic state', encoding='utf-8')
        ops.atomic_json(path / 'recovery_manifest.json', {
            'created_at_utc': (self.now + timedelta(days=day)).isoformat(),
            'files': {'state.txt': {'sha256': ops.sha(path / 'state.txt')}}})
        return path

    def point(self, name, target):
        pointer = self.served / name
        if os.path.lexists(pointer):
            pointer.unlink()
        pointer.symlink_to(target.name, target_is_directory=True)

    @LINUX_ONLY
    def test_never_deletes_current_previous_or_three_newest_older(self):
        generations = [self.generation('generation-%02d' % i, i) for i in range(10)]
        self.point('current', generations[1])
        self.point('previous', generations[0])
        protected = {generations[i].name for i in [0, 1, 7, 8, 9]}
        contents = {name: ops.sha(self.served / name / 'fixture.db') for name in protected}
        result = ops.prune(self.served, self.backups, now=self.now)
        actual = {path.name for path in self.served.glob('generation-*')}
        self.assertEqual(actual, protected)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['deleted']), 5)
        for name, digest in contents.items():
            self.assertEqual(ops.sha(self.served / name / 'fixture.db'), digest)
        self.assertEqual((self.served / 'current').resolve(), generations[1])
        self.assertEqual((self.served / 'previous').resolve(), generations[0])

    @LINUX_ONLY
    def test_single_generation_and_duplicate_pointer_are_safe(self):
        only = self.generation('generation-only', 0)
        self.point('current', only)
        first = ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(first['kept_generations'], [only.name])
        self.assertEqual(first['deleted'], [])
        self.point('previous', only)
        second = ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(second['kept_generations'], [only.name])
        self.assertTrue(only.exists())

    @LINUX_ONLY
    def test_legacy_snapshot_counts_toward_three_older(self):
        legacy = self.generation('snapshot-legacy', 0)
        others = [self.generation('generation-%02d' % i, i) for i in range(1, 6)]
        self.point('current', others[-1])
        self.point('previous', others[-2])
        result = ops.prune(self.served, self.backups, now=self.now)
        self.assertFalse(legacy.exists())
        self.assertEqual(len(result['kept_generations']), 5)

    @LINUX_ONLY
    def test_failed_staging_age_is_strictly_greater_than_two_days(self):
        self.point('current', self.generation('generation-only', 0))
        expired = self.served / '.generation-expired'
        exact = self.served / '.generation-exactly-two-days'
        recent = self.served / '.generation-recent'
        for path, seconds in [(expired, 172801), (exact, 172800), (recent, 60)]:
            path.mkdir()
            (path / 'partial.db').write_bytes(b'partial')
            stamp = self.now.timestamp() - seconds
            os.utime(path, (stamp, stamp))
        ops.prune(self.served, self.backups, now=self.now)
        self.assertFalse(expired.exists())
        self.assertTrue(exact.exists())
        self.assertTrue(recent.exists())

    @LINUX_ONLY
    def test_backups_keep_fourteen_complete_and_preserve_incomplete(self):
        self.point('current', self.generation('generation-only', 0))
        for i in range(20):
            self.backup('operations-%02d' % i, i)
        incomplete = self.backups / 'operations-incomplete'
        incomplete.mkdir()
        result = ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(set(result['kept_backups']), {'operations-%02d' % i for i in range(6, 20)})
        self.assertTrue(incomplete.exists())
        self.assertEqual(len(list(self.backups.glob('operations-*'))), 15)
        self.assertEqual(result['status'], 'attention')

    @LINUX_ONLY
    def test_broken_or_escaping_pointer_prevents_all_deletions(self):
        old = self.generation('generation-obsolete', -20)
        self.point('current', self.served / 'generation-missing')
        with self.assertRaises(ValueError):
            ops.prune(self.served, self.backups, now=self.now)
        self.assertTrue(old.exists())
        outside = self.root / 'outside'
        outside.mkdir()
        self.point('current', outside)
        with self.assertRaises(ValueError):
            ops.prune(self.served, self.backups, now=self.now)
        self.assertTrue(old.exists())

    @LINUX_ONLY
    def test_symlink_candidate_is_never_followed(self):
        self.point('current', self.generation('generation-current', 0))
        outside = self.root / 'outside'
        outside.mkdir()
        marker = outside / 'keep.txt'
        marker.write_text('must remain', encoding='utf-8')
        (self.served / 'generation-link').symlink_to(outside, target_is_directory=True)
        (self.served / '.generation-link').symlink_to(outside, target_is_directory=True)
        ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(marker.read_text(), 'must remain')

    @LINUX_ONLY
    def test_dry_run_has_no_deletion_or_report_write(self):
        generations = [self.generation('generation-%02d' % i, i) for i in range(8)]
        self.point('current', generations[-1])
        self.point('previous', generations[-2])
        result = ops.prune(self.served, self.backups, now=self.now, dry_run=True)
        self.assertEqual(len(result['planned_deletions']), 3)
        self.assertEqual(result['reclaimed_bytes'], 0)
        self.assertTrue(all(path.exists() for path in generations))
        self.assertFalse((self.served / 'retention.json').exists())

    def test_pruning_failure_reports_attention_after_publication(self):
        with patch.object(ops, 'prune', side_effect=OSError('fixture failure')):
            report = ops._prune_after_publish(self.served, self.backups)
        self.assertEqual(report['status'], 'attention')
        self.assertEqual(json.loads((self.served / 'retention.json').read_text())['status'], 'attention')

    def test_policy_selection_without_windows_symlink_privilege(self):
        for i in range(9):
            self.generation('generation-%02d' % i, i)
        protected = {'generation-00', 'generation-01'}
        with patch.object(ops, '_protected_generations', return_value=protected):
            report = ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(set(report['kept_generations']), protected | {
            'generation-06', 'generation-07', 'generation-08'})
        self.assertEqual(len(list(self.served.glob('generation-*'))), 5)

    def test_backup_cutoff_and_staging_cleanup_without_symlink_privilege(self):
        self.generation('generation-current', 0)
        for i in range(20):
            self.backup('operations-%02d' % i, i)
        staging = self.served / '.generation-expired'
        staging.mkdir()
        (staging / 'partial.db').write_bytes(b'fixture')
        stamp = self.now.timestamp() - 172801
        os.utime(staging, (stamp, stamp))
        with patch.object(ops, '_protected_generations', return_value={'generation-current'}):
            report = ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(len(report['kept_backups']), 14)
        self.assertFalse(staging.exists())
        self.assertEqual(len(list(self.backups.glob('operations-*'))), 14)
        self.assertGreater(report['reclaimed_bytes'], 0)

    def test_retention_roots_must_not_overlap(self):
        with self.assertRaises(ValueError):
            ops.prune(self.served, self.served / 'nested-backups', now=self.now)

    def test_corrupt_backup_does_not_displace_a_good_retained_copy(self):
        self.generation('generation-current', 0)
        for i in range(20):
            self.backup('operations-%02d' % i, i)
        corrupt = self.backups / 'operations-19'
        (corrupt / 'state.txt').write_text('damaged fixture', encoding='utf-8')
        with patch.object(ops, '_protected_generations', return_value={'generation-current'}):
            report = ops.prune(self.served, self.backups, now=self.now)
        self.assertEqual(set(report['kept_backups']), {'operations-%02d' % i for i in range(5, 19)})
        self.assertTrue(corrupt.exists())
        self.assertEqual(report['status'], 'attention')

    def test_restore_cli_holds_the_same_locks_as_pruning(self):
        entered = []

        @contextmanager
        def lock(path):
            entered.append(path.name)
            yield

        def restore(path):
            self.assertEqual(entered, ['operations.lock', 'routine.lock', '.run.lock'])
            return {'status': 'passed'}

        with patch('sys.argv', ['ops', 'restore-drill', '--backup', str(self.backups)]):
            with patch.object(ops, 'exclusive', side_effect=lock):
                with patch.object(ops, 'restore_drill', side_effect=restore):
                    with redirect_stdout(io.StringIO()):
                        ops.main()

    def test_dry_run_cannot_be_silently_ignored_by_publish(self):
        with patch('sys.argv', ['ops', 'publish', '--dry-run']):
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    ops.main()
        self.assertEqual(stopped.exception.code, 2)

    def source_fixture(self):
        project = self.root / 'project'
        data = project / 'databases'
        data.mkdir(parents=True)
        for name in ['contract_api.db', 'actions.db']:
            with closing(sqlite3.connect(data / name)) as db, db:
                db.execute('CREATE TABLE contracts(id TEXT, payload BLOB)')
                db.execute('INSERT INTO contracts VALUES (?,?)', ('fixture', b'x' * 32768))
        work = self.root / 'work'
        run = work / 'reports/daily_sweep_fixture'
        run.mkdir(parents=True)
        ops.atomic_json(run / 'source_refresh.json', {
            'success': True, 'feed': 'fixture build',
            'results': [{'spider': s, 'status': 'OK'} for s in ops.EXPECTED]})
        ops.atomic_json(run / 'finalization.json', {
            'steps': [{'step': s, 'exit_code': 0} for s in ['intelligence', 'data_quality', 'newsletter_evidence']]})
        ops.atomic_json(run / 'newsletter_evidence.json', {
            'generated_at_utc': self.now.isoformat(), 'build_info': [{'built_at': 'fixture-build'}]})
        ops.atomic_json(run / 'cloud_run.json', {'sent': False})
        return project, data, work, run

    @LINUX_ONLY
    def test_failed_publish_never_prunes_existing_generations(self):
        project, data, work, run = self.source_fixture()
        for i in range(7):
            self.generation('generation-%02d' % i, i)
        self.point('current', self.served / 'generation-06')
        self.point('previous', self.served / 'generation-05')
        before = set(path.name for path in self.served.glob('generation-*'))
        with patch.object(ops, 'prune') as pruner:
            with self.assertRaises(ValueError):
                ops.publish(data, self.served, run, {'contract_api': ['missing']}, backups=self.backups)
        pruner.assert_not_called()
        self.assertEqual(before, set(path.name for path in self.served.glob('generation-*')))
        self.assertEqual((self.served / 'current').resolve().name, 'generation-06')

    @LINUX_ONLY
    def test_successful_publish_survives_cleanup_error(self):
        project, data, work, run = self.source_fixture()
        with patch.object(ops, 'prune', side_effect=OSError('fixture cleanup failure')):
            result = ops.publish(data, self.served, run, {'contract_api': ['contracts']}, backups=self.backups)
        self.assertTrue(result['published'])
        self.assertTrue(result['retention_attention'])
        self.assertEqual(result['retention']['status'], 'attention')
        self.assertEqual((self.served / 'current').resolve().name, result['generation_id'])

    @LINUX_ONLY
    def test_resolved_reader_generation_survives_four_subsequent_publishes(self):
        project, data, work, run = self.source_fixture()
        ops.publish(data, self.served, run, {'contract_api': ['contracts']}, backups=self.backups)
        pinned = (self.served / 'current').resolve()
        original_manifest = (pinned / 'manifest.json').read_bytes()
        original_mode = pinned.stat().st_mode
        for _ in range(4):
            ops.publish(data, self.served, run, {'contract_api': ['contracts']}, backups=self.backups)
            self.assertEqual((pinned / 'manifest.json').read_bytes(), original_manifest)
            self.assertEqual(pinned.stat().st_mode, original_mode)
            with closing(sqlite3.connect(pinned / 'contract_api.db')) as db:
                self.assertEqual(db.execute('SELECT id FROM contracts').fetchone()[0], 'fixture')

    @LINUX_ONLY
    def test_ten_day_run_stays_below_fixed_two_mib_managed_storage_budget(self):
        project, data, work, run = self.source_fixture()
        budget = 2 * 1024 * 1024
        peaks = []
        daily = []
        original_prune = ops._prune_after_publish

        def measure_before_prune(served, backups):
            peaks.append(ops._directory_bytes(served) + ops._directory_bytes(backups))
            return original_prune(served, backups)

        with patch.object(ops, 'datetime', wraps=datetime) as clock:
            for day in range(10):
                when = self.now + timedelta(days=day)
                clock.now.return_value = when
                staging = self.served / ('.generation-fixture-day-%02d' % day)
                staging.mkdir()
                (staging / 'partial.db').write_bytes(b'x' * 4096)
                os.utime(staging, (when.timestamp(), when.timestamp()))
                for _ in range(2):
                    ops.backup_operations(project, work, self.backups)
                if day == 5:
                    previous = (self.served / 'current').resolve()
                    with self.assertRaises(ValueError):
                        ops.publish(data, self.served, run, {'contract_api': ['missing']}, backups=self.backups)
                    self.assertEqual((self.served / 'current').resolve(), previous)
                    for failed in self.served.glob('.generation-*'):
                        if failed != staging:
                            os.utime(failed, (when.timestamp(), when.timestamp()))
                with patch.object(ops, '_prune_after_publish', side_effect=measure_before_prune):
                    result = ops.publish(data, self.served, run, {'contract_api': ['contracts']}, backups=self.backups)
                report = result['retention']
                self.assertEqual(report['status'], 'ok')
                self.assertLessEqual(len(report['kept_generations']), 5)
                self.assertLessEqual(len(report['kept_backups']), 14)
                retained = ops._directory_bytes(self.served) + ops._directory_bytes(self.backups)
                self.assertLessEqual(retained, budget)
                daily.append({'day': day + 1, 'retained_bytes': retained,
                              'generations': len(report['kept_generations']),
                              'backups': len(report['kept_backups'])})
        self.assertLessEqual(max(peaks), budget)
        self.assertEqual(daily[-1]['generations'], 5)
        self.assertEqual(daily[-1]['backups'], 14)
        measurement = {'scope': 'generation and operations-backup roots only',
                       'days': 10, 'complete_backups_created': 20,
                       'source_payload_bytes': 32768, 'budget_bytes': budget,
                       'peak_managed_bytes': max(peaks),
                       'final_retained_bytes': daily[-1]['retained_bytes'], 'daily': daily}
        output = os.environ.get('ILLOURA_RETENTION_TEST_REPORT')
        if output:
            ops.atomic_json(Path(output), measurement)


if __name__ == '__main__':
    unittest.main()

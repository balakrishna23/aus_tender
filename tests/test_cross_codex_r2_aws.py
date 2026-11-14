"""Remote deployment commands are fakes; real file-name inventories are read only."""
import unittest
from unittest.mock import Mock, patch
import test_cross_codex_aws as fixtures

cli = fixtures.cli
ROOT = fixtures.ROOT


class DeploymentRoundTwo(unittest.TestCase):
    def test_no_prefix_regex_validators_remain(self):
        import ast
        tree = ast.parse((ROOT / 'deploy/aws/illoura_aws.py').read_text())
        prefix_calls = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute) and node.func.attr in ('match', 'search')]
        self.assertEqual(prefix_calls, [])

    def test_clock_failure_and_impossible_times_refuse_updates(self):
        for code, clock in [(1, '1400'), (0, ''), (0, '2400'), (0, '1260'), (0, 'garbage')]:
            with self.subTest(code=code, clock=clock):
                remote = Mock()
                remote.run.side_effect = lambda argv, **kw: (code, clock, '') if argv[0] == 'env' else (0, 'inactive', '')
                with self.assertRaises(cli.OperationError):
                    cli.os_update(remote, cli.Settings(), yes=True)
                self.assertFalse(any('unattended-upgrade' in call.args[0] for call in remote.run.call_args_list))
    def test_real_deployment_names_accept_nested_paths(self):
        names = set(cli.collector_files(ROOT)) | set(cli.routine_files(ROOT))
        names |= {'routine/' + n for n in cli.routine_files(ROOT)}
        names |= {'deploy/' + n for n in cli.OPS_FILES + cli.OPS_UNITS}
        names |= {'tests/' + n for n in cli.OPS_TESTS}
        names |= {'app.py', 'test_security.py', 'requirements.txt', cli.MAIL_ENV_HELPER, 'deploy/policy.json'}
        stage = cli.new_stage()
        files = {n: b'synthetic' for n in names}
        remote = Mock()
        def must(remote, argv, *args, **kwargs):
            return '\n'.join(cli.sha256(v) + '  ' + stage + '/' + k for k, v in files.items()) if argv[0] == 'sha256sum' else ''
        with patch.object(cli, 'must', side_effect=must):
            cli.stage_files(remote, stage, files)
        self.assertEqual(remote.put_bytes.call_count, len(files))

    def test_stage_rejects_every_escape_before_upload(self):
        for name in ['../x', 'spiders/../../x', '/x', 'C:\\x', 'spiders//x', './x', 'spiders/./x', 'x\n']:
            with self.subTest(name=name):
                remote = Mock()
                with self.assertRaises(ValueError):
                    cli.stage_files(remote, cli.new_stage(), {name: b'x'})
                remote.run.assert_not_called()

    def deploy(self, fail_at=None, restart=0, rollback=0, health=7):
        remote = Mock()
        def run(argv, **kwargs):
            if argv[:2] == ['sudo', 'cat']:
                return 0, (ROOT / 'api/requirements.txt').read_text(), ''
            if argv[0] == 'curl':
                return health, '', ''
            if argv[:3] == ['sudo', 'systemctl', 'restart']:
                return restart, '', ''
            if argv[:2] == ['sudo', 'install'] and argv[-2].startswith(cli.BACKUP_ROOT):
                return rollback, '', ''
            return 0, '', ''
        remote.run.side_effect = run
        with patch.object(cli, 'stage_files', side_effect=cli.OperationError('stage') if fail_at == 'stage' else None), \
             patch.object(cli, 'run_tests', side_effect=cli.OperationError('test') if fail_at == 'test' else None), \
             patch.object(cli, 'remove_stage') as remove, patch.object(cli.time, 'sleep') as sleep:
            try:
                result = cli.deploy_api(remote, yes=True, wait_seconds=0)
            except cli.OperationError as error:
                result = str(error)
            remove.assert_called_once()
            sleep.assert_not_called()
        return result, remote

    def test_stage_cleanup_on_upload_test_and_health_failures(self):
        for phase in ['stage', 'test', None]:
            with self.subTest(phase=phase):
                result, _ = self.deploy(fail_at=phase)
                self.assertIsInstance(result, str)

    def test_zero_wait_polls_once_then_rolls_back(self):
        result, remote = self.deploy()
        self.assertIn('previous version restored', result)
        self.assertEqual(sum(c.args[0][0] == 'curl' for c in remote.run.call_args_list), 1)

    def test_restart_and_failed_rollback_are_reported(self):
        result, _ = self.deploy(restart=1, rollback=1)
        self.assertIn('ALSO FAILED', result)

    def test_immediately_healthy_api_succeeds_with_zero_wait(self):
        result, _ = self.deploy(health=0)
        self.assertIn('installed_sha256', result)

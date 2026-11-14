"""Adversarial operator tests; all remote execution is an in-memory fake."""
import importlib.util
from pathlib import Path
import random
import shlex
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cross_aws', ROOT / 'deploy/aws/illoura_aws.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class OperatorSafety(unittest.TestCase):
    def test_forbidden_characters(self):
        """Shell metacharacters and Unicode must never reach SSH."""
        remote = cli.SshRemote.__new__(cli.SshRemote)
        remote.client = Mock()
        rng = random.Random(871)
        bad = ' \t\r\n;|&$`\'"<>?!()[]{}\\\x00é−'
        for char in bad:
            with self.subTest(char=repr(char)), self.assertRaises(ValueError):
                remote.run(['cat', 'file' + char + str(rng.randrange(100))])
        remote.client.exec_command.assert_not_called()

    def test_trailing_newline_rejected_before_ssh(self):
        """Regex end anchors must reject a final newline too."""
        remote = cli.SshRemote.__new__(cli.SshRemote)
        remote.client = Mock()
        out, err = Mock(), Mock()
        out.read.return_value, err.read.return_value = b'', b''
        out.channel.recv_exit_status.return_value = 0
        remote.client.exec_command.return_value = (None, out, err)
        with self.assertRaises(ValueError):
            remote.run(['cat', 'file\n'])
        remote.client.exec_command.assert_not_called()

    def test_plain_tokens_round_trip(self):
        """Shell quoting must preserve literal glob and caret arguments."""
        values = ['sudo', '--', '/var/tmp/a', 'a*b', 'a^b', 'x=y', 'a:b', 'a@b', '1,2']
        self.assertEqual(shlex.split(cli.command_line(values)), values)
        for argv in ([], [''], [None], [42]):
            with self.assertRaises(ValueError):
                cli.command_line(argv)

    def test_mutations_need_confirmation(self):
        """An omitted --yes must not reach a remote command or AWS."""
        remote = Mock()
        for action in ('publish', 'rollback', 'prune'):
            with self.assertRaises(cli.OperationError):
                cli.ops_action(remote, action)
        for action in (cli.deploy_api, cli.deploy_ops, cli.deploy_collectors,
                       cli.deploy_routine, cli.set_mail_config):
            with self.assertRaises(cli.OperationError):
                action(remote)
        with patch.object(cli, 'aws') as aws:
            for action in (lambda: cli.watch(cli.Settings(), 'deploy'),
                           lambda: cli.snapshots(cli.Settings(), 'enable')):
                with self.assertRaises(cli.OperationError):
                    action()
            aws.assert_not_called()
        remote.run.assert_not_called()

    def test_stage_traversal_rejected_before_upload(self):
        """An accidental traversal in a staged file must not escape staging."""
        remote = Mock()
        remote.run.return_value = (0, '', '')
        with self.assertRaises(ValueError):
            cli.stage_files(remote, cli.new_stage(), {'../outside.py': b'x'})
        remote.put_bytes.assert_not_called()

    def test_hash_mismatch_aborts(self):
        """Corrupt uploads must fail before a production install."""
        remote = Mock()
        remote.run.return_value = (0, '', '')
        with self.assertRaises(cli.OperationError):
            cli.stage_files(remote, cli.new_stage(), {'app.py': b'new'})
        self.assertFalse(any(c.args[0][0] == 'sudo' for c in remote.run.call_args_list))

    def deploy_failure(self, restart=False):
        remote = Mock()
        live = cli.API_DIR + '/app.py'
        state = {'live': b'old', 'backup': None}
        def run(argv, **kwargs):
            if argv[:2] == ['sudo', 'cat']:
                return 0, (ROOT / 'api/requirements.txt').read_text(), ''
            if argv[:2] == ['sudo', 'install']:
                if argv[-2] == live:
                    state['backup'] = state['live']
                elif argv[-2].startswith(cli.BACKUP_ROOT):
                    state['live'] = state['backup']
                elif argv[-1] == live:
                    state['live'] = b'new'
            if argv == ['sudo', 'systemctl', 'restart', 'illoura-api'] and restart:
                return 1, '', 'restart rejected'
            if argv[0] == 'curl':
                return 7, '', 'not running'
            return 0, '', ''
        remote.run.side_effect = run
        with patch.object(cli, 'stage_files'), patch.object(cli, 'run_tests'):
            with self.assertRaises(cli.OperationError):
                cli.deploy_api(remote, yes=True, wait_seconds=0)
        return state

    def test_unhealthy_api_restores_previous(self):
        """An API that never starts must restore its last working source."""
        self.assertEqual(self.deploy_failure()['live'], b'old')

    def test_restart_command_failure_restores_previous(self):
        """A failed systemctl restart must enter the rollback path too."""
        self.assertEqual(self.deploy_failure(restart=True)['live'], b'old')

    def test_server_test_failure_installs_nothing(self):
        """Failing staged tests must leave the live API untouched."""
        remote = Mock()
        remote.run.return_value = (0, (ROOT / 'api/requirements.txt').read_text(), '')
        with patch.object(cli, 'stage_files'), patch.object(cli, 'run_tests', side_effect=cli.OperationError('tests')):
            with self.assertRaises(cli.OperationError):
                cli.deploy_api(remote, yes=True)
        self.assertFalse(any(c.args[0][:2] == ['sudo', 'install'] for c in remote.run.call_args_list))

    def test_update_window_and_invalid_clock(self):
        """Updates must fail closed in the morning or when clock output is corrupt."""
        for clock in ('0530', '0814', '0815', '0900', '0929', '', 'garbage'):
            remote = Mock()
            remote.run.side_effect = lambda argv, **kw: (0, clock if argv[0] == 'env' else 'inactive', '')
            with self.subTest(clock=clock):
                with self.assertRaises(cli.OperationError):
                    cli.os_update(remote, cli.Settings(), yes=True)
                self.assertFalse(any('unattended-upgrade' in c.args[0] for c in remote.run.call_args_list))

    def test_disable_stops_activating_sender(self):
        """Turning mail off must stop a sender already waiting for send time."""
        remote = Mock()
        remote.run.return_value = (0, 'activating', '')
        result = cli.schedule(remote, 'disable', yes=True)
        self.assertTrue(result['stopped_running_brief'])
        self.assertIn(unittest.mock.call(['sudo', 'systemctl', 'stop', cli.BRIEF_SERVICE], timeout=900), remote.run.call_args_list)

    def test_reconcile_rejects_option_and_path_injection(self):
        """Only a run basename may select records for reconciliation."""
        remote = Mock()
        for name in ('--help', '../daily_sweep_20261114_004500', '', 'daily_sweep_20261114_004500/x', 'daily_sweep_２０２６１１１４_004500'):
            with self.assertRaises(ValueError):
                cli.reconcile_send(remote, name, yes=True)
        remote.run.assert_not_called()

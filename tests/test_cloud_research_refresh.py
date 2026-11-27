"""Offline root-wrapper boundaries; no root, systemd or network needed."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy'
spec = importlib.util.spec_from_file_location('research_wrapper', DEPLOY / 'illoura_research_refresh.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class WrapperTests(unittest.TestCase):
    def setUp(self):
        # SIGKILL/killpg are POSIX-only; mocks keep Windows tests equivalent.
        patcher = patch.object(signal, 'SIGKILL', getattr(signal, 'SIGKILL', 9), create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_blackout_boundaries_and_dst(self):
        # AEST in July, AEDT in November, including both DST transition dates.
        from zoneinfo import ZoneInfo
        zone = ZoneInfo('Australia/Sydney')
        for day in ('2026-07-01', '2026-11-23', '2026-04-05', '2026-10-04'):
            for clock, expected in [('05:29:59', False), ('05:30:00', True), ('09:29:59', True), ('09:30:00', False)]:
                now = datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=zone).astimezone(timezone.utc)
                self.assertEqual(worker.blackout(now), expected, (day, clock))

    def locked(self, root, flock):
        return patch.multiple(worker, LOCK=root / 'shared' / 'refresh.lock',
                              configuration=Mock(return_value=(root, root / 'builds' / 'test')))

    def test_exact_shared_lock_contention(self):
        self.assertEqual(worker.LOCK.as_posix(), '/var/lib/illoura-market-refresh/refresh.lock')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.json'
            config.write_text('{}')
            flock = Mock(side_effect=BlockingIOError)
            fake = SimpleNamespace(flock=flock, LOCK_EX=2, LOCK_NB=4)
            with patch.dict(sys.modules, {'fcntl': fake}), self.locked(root, flock), patch.object(worker, 'blackout', return_value=False), patch.object(worker, 'run_application') as run:
                self.assertEqual(worker.refresh(config)['status'], 'busy')
                self.assertEqual(flock.call_args.args[1], 6)
                run.assert_not_called()

    def test_blackout_rechecked_after_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.json'
            config.write_text('{}')
            fake = SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
            with patch.dict(sys.modules, {'fcntl': fake}), self.locked(root, fake.flock), patch.object(worker, 'blackout', side_effect=[False, True]), patch.object(worker, 'run_application') as run:
                self.assertEqual(worker.refresh(config)['status'], 'skipped')
                run.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'Actual flock requires POSIX')
    def test_real_shared_lock_contention(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.json'
            config.write_text('{}')
            lock_path = root / 'refresh.lock'
            with lock_path.open('a') as owner:
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(worker, 'LOCK', lock_path), patch.object(worker, 'configuration', return_value=(root, root)), patch.object(worker, 'blackout', return_value=False), patch.object(worker, 'run_application') as run:
                    self.assertEqual(worker.refresh(config)['status'], 'busy')
                    run.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Actual process-group and /proc evidence requires Linux')
    def test_real_timeout_stops_parent_and_descendant(self):
        native_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_file = root / 'child.pid'
            script = ('import pathlib,subprocess,sys,time; '
                      'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                      'pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)')
            parents = []
            def launch(_argv, **kwargs):
                process = native_popen([sys.executable, '-c', script, str(pid_file)], **kwargs)
                parents.append(process)
                return process
            try:
                with patch.object(worker.subprocess, 'Popen', side_effect=launch):
                    result = worker.run_application(root, root, timeout=10)
                self.assertEqual(result['reason'], 'timeout_process_group_terminated')
                self.assertIsNotNone(parents[0].poll())
                child_pid = int(pid_file.read_text())
                status = Path('/proc') / str(child_pid) / 'stat'
                if status.exists():
                    self.assertIn(status.read_text().split(') ', 1)[1][0], ('Z', 'X'))
            finally:
                for process in parents:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    process.wait(timeout=5)

    def test_timeout_terminates_complete_process_group(self):
        process = Mock(pid=87654)
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 2695), subprocess.TimeoutExpired('fixture', 5), 0]
        with patch.object(worker.subprocess, 'Popen', return_value=process) as popen, patch.object(worker.os, 'killpg', create=True) as kill:
            result = worker.run_application(Path('/research'), Path('/research/builds/test'))
            self.assertEqual(result['reason'], 'timeout_process_group_terminated')
            self.assertLessEqual(process.wait.call_args_list[0].kwargs['timeout'], 2695)
            self.assertGreater(process.wait.call_args_list[0].kwargs['timeout'], 2694)
            self.assertEqual(kill.call_args_list[0].args, (87654, signal.SIGTERM))
            self.assertTrue(any(call.args == (87654, signal.SIGKILL) for call in kill.call_args_list))
            self.assertTrue(popen.call_args.kwargs['start_new_session'])
            self.assertIn('ubuntu', popen.call_args.args[0])

    def test_timeout_kills_descendants_after_parent_exits(self):
        process = Mock(pid=87654)
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 5), 0]
        with patch.object(worker.subprocess, 'Popen', return_value=process), patch.object(worker.os, 'killpg', create=True) as kill:
            worker.run_application(Path('/research'), Path('/research/builds/test'), timeout=10)
            self.assertEqual(kill.call_args_list[-1].args, (87654, signal.SIGKILL))

    def test_units_have_nonpersistent_sydney_schedule(self):
        timer = (DEPLOY / 'illoura-research-refresh.timer').read_text()
        service = (DEPLOY / 'illoura-research-refresh.service').read_text()
        self.assertIn('10:15:00 Australia/Sydney', timer)
        self.assertIn('RandomizedDelaySec=600', timer)
        self.assertIn('Persistent=false', timer)
        for setting in ('User=root', 'TimeoutStartSec=2700', 'KillMode=control-group', 'Nice=15', 'NoNewPrivileges=true'):
            self.assertIn(setting, service)

    def test_units_that_switch_user_keep_the_setuid_capability(self):
        # On the server (systemd 255) NoNewPrivileges + ProtectKernelTunables/Modules or RestrictAddressFamilies
        # drops CAP_SETUID, so a root service that runs runuser fails with "cannot set user id"
        # (27 Nov: the refresher never started). Verified on the server with systemd-run.
        # The refresher install stages only its own unit; the review unit is checked wherever the repo is complete.
        units = ['illoura-research-refresh.service']
        if (DEPLOY / 'illoura-review-import.service').exists() or (DEPLOY / 'illoura_review_import.py').exists():
            units.append('illoura-review-import.service')
        for unit in units:
            text = (DEPLOY / unit).read_text()
            settings = [line.strip() for line in text.splitlines() if not line.lstrip().startswith('#')]
            self.assertIn('NoNewPrivileges=true', settings, unit)
            for forbidden in ('ProtectKernelTunables=', 'ProtectKernelModules=', 'RestrictAddressFamilies='):
                self.assertFalse([s for s in settings if s.startswith(forbidden)], unit + ': ' + forbidden)

    @unittest.skipUnless(hasattr(worker.os, 'fchmod'), 'POSIX private file mode')
    def test_status_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worker.atomic_status({'status': 'complete'}, root)
            self.assertEqual(json.loads((root / 'status.json').read_text())['status'], 'complete')
            self.assertEqual((root / 'status.json').stat().st_mode & 0o777, 0o600)


    def test_own_build_configuration_preferred_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            own, shared = Path(tmp) / 'research-refresh.json', Path(tmp) / 'refresh.json'
            shared.write_text('{}')
            self.assertEqual(worker.chosen_config(own, shared), shared)
            own.write_text('{}')
            self.assertEqual(worker.chosen_config(own, shared), own)

    def test_own_configuration_gets_the_same_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            own = Path(tmp) / 'research-refresh.json'
            own.write_text(json.dumps({'data_root': tmp, 'build': tmp, 'tools': tmp, 'extra': 1}))
            with self.assertRaises(ValueError):
                worker.configuration(own)


if __name__ == '__main__':
    unittest.main()

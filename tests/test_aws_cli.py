"""AWS operations CLI: fixed remote actions, staged/verified deploys, no network."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "illoura_aws", Path(__file__).resolve().parents[1] / "deploy/aws/illoura_aws.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class FakeRemote:
    """Records every argument list; answers like the real server would."""

    def __init__(self, **options):
        self.calls, self.files = [], {}
        self.timers = {
            t: "disabled" if "collect" in t else "enabled" for t in cli.TIMERS
        }
        self.health_configured = options.get("health_configured", True)
        self.corrupt_upload = options.get("corrupt_upload", False)
        self.tests_ok = options.get("tests_ok", True)
        self.health_ok = options.get("health_ok", True)
        self.flip_timer_on_reload = options.get("flip_timer_on_reload", False)
        self.requirements = options.get(
            "requirements",
            (Path(cli.REPO) / "api/requirements.txt").read_text(encoding="utf-8"),
        )
        self.keys = [{"name": "admin", "sha256": "x" * 64, "enabled": True}]
        self.busy = options.get("busy", ())  # units reported as running

    def run(self, argv, timeout=900):
        cli.command_line(argv)  # every call must pass the same safety check as SSH
        self.calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-active"] and len(argv) == 3:
            return (0, "active\n", "") if argv[2] in self.busy else (3, "inactive\n", "")
        if argv[:2] == ["sudo", "sha256sum"]:
            present = [p for p in argv[2:] if p in self.files]
            lines = [hashlib.sha256(self.files[p]).hexdigest() + "  " + p for p in present]
            return (0 if len(present) == len(argv) - 2 else 1), "\n".join(lines), ""
        if argv[:2] == ["sudo", "install"] and argv[-2] in self.files:
            self.files[argv[-1]] = self.files[argv[-2]]  # installed copies hash like the original
        if argv[0] == "sha256sum":
            lines = []
            for path in argv[1:]:
                data = self.files[path] + (b"tampered" if self.corrupt_upload else b"")
                lines.append(hashlib.sha256(data).hexdigest() + "  " + path)
            return 0, "\n".join(lines), ""
        if argv[:2] == ["systemctl", "is-enabled"]:
            return 0, self.timers[argv[2]] + "\n", ""
        if argv[:3] == ["sudo", "grep", "-c"]:
            return (0, "1\n", "") if self.health_configured else (1, "0\n", "")
        if "unittest" in argv:
            return (0, "OK", "") if self.tests_ok else (1, "", "FAILED (failures=1)")
        if argv[:2] == ["sudo", "cat"] and argv[2].endswith("requirements.txt"):
            return 0, self.requirements, ""
        if argv[:2] == ["sudo", "cat"] and argv[2] == cli.KEYS_FILE:
            return 0, json.dumps(self.keys), ""
        if argv[0] == "curl":
            return (
                (0, '{"status":"ok"}', "")
                if self.health_ok
                else (7, "", "connection refused")
            )
        if argv[-1:] == ["daemon-reload"] and self.flip_timer_on_reload:
            self.timers["illoura-cloud-collect.timer"] = "enabled"
        return 0, "", ""

    def put_bytes(self, data, remote_path, mode=0o644):
        self.files[remote_path] = data

    def close(self):
        pass

    def installs(self):
        return [c for c in self.calls if c[:2] == ["sudo", "install"]]


class SafetyTests(unittest.TestCase):
    def test_shell_syntax_is_refused(self):
        for bad in (
            ["ls", "/tmp; rm -rf /"],
            ["echo", "$(id)"],
            ["echo", "`id`"],
            ["a|b"],
            ["x y"],
            [],
        ):
            with self.assertRaises(ValueError):
                cli.command_line(bad)
        self.assertEqual(
            cli.command_line(["sudo", "grep", "-c", "^KEY=.", "/etc/x"]),
            "sudo grep -c '^KEY=.' /etc/x",
        )

    def test_restore_drill_only_accepts_managed_backups(self):
        remote = FakeRemote()
        with self.assertRaises(ValueError):
            cli.ops_action(remote, "restore-drill", backup="/etc/passwd")
        with self.assertRaises(ValueError):
            cli.ops_action(
                remote,
                "restore-drill",
                backup="/var/backups/illoura/operations-1/../../x",
            )
        cli.ops_action(
            remote,
            "restore-drill",
            backup="/var/backups/illoura/operations-20261112T1-ab12",
        )
        self.assertEqual(
            remote.calls[-1][-2:],
            ["--backup", "/var/backups/illoura/operations-20261112T1-ab12"],
        )

    def test_production_changes_need_yes(self):
        for action in ("publish", "rollback", "prune"):
            with self.assertRaises(cli.OperationError):
                cli.ops_action(FakeRemote(), action)
        remote = FakeRemote()
        cli.ops_action(remote, "prune", dry_run=True)
        self.assertEqual(remote.calls[-1][-2:], ["prune", "--dry-run"])
        with self.assertRaises(ValueError):
            cli.ops_action(FakeRemote(), "publish", dry_run=True)

    def test_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            cli.ops_action(FakeRemote(), "shell", yes=True)


class DeployOpsTests(unittest.TestCase):
    def test_refuses_without_private_health_sender(self):
        remote = FakeRemote(health_configured=False)
        with self.assertRaises(cli.OperationError):
            cli.deploy_ops(remote, yes=True)
        self.assertEqual(remote.files, {})
        self.assertEqual(remote.installs(), [])

    def test_needs_yes(self):
        with self.assertRaises(cli.OperationError):
            cli.deploy_ops(FakeRemote())

    def test_hash_mismatch_installs_nothing_and_cleans_stage(self):
        remote = FakeRemote(corrupt_upload=True)
        with self.assertRaises(cli.OperationError):
            cli.deploy_ops(remote, yes=True)
        self.assertEqual(remote.installs(), [])
        self.assertTrue(any(c[:2] == ["rm", "-rf"] for c in remote.calls))

    def test_failed_staged_tests_install_nothing(self):
        remote = FakeRemote(tests_ok=False)
        with self.assertRaises(cli.OperationError):
            cli.deploy_ops(remote, yes=True)
        self.assertEqual(remote.installs(), [])

    def test_installs_fixed_paths_and_never_enables_a_timer(self):
        remote = FakeRemote()
        result = cli.deploy_ops(remote, yes=True)
        destinations = [c[-1] for c in remote.installs()]
        self.assertTrue(destinations)
        self.assertTrue(
            all(
                d.startswith(("/opt/illoura-ops/", "/etc/systemd/system/"))
                for d in destinations
            )
        )
        self.assertIn(["sudo", "systemctl", "daemon-reload"], remote.calls)
        self.assertFalse(any("enable" in c or "disable" in c for c in remote.calls))
        self.assertEqual(result["timers"]["illoura-cloud-collect.timer"], "disabled")
        for path, data in remote.files.items():
            self.assertNotIn(b"\r\n", data, path)
        policy = json.loads(
            next(v for k, v in remote.files.items() if k.endswith("policy.json"))
        )
        self.assertIn("contract_api", policy)

    def test_unexpected_timer_change_is_reported(self):
        with self.assertRaises(cli.OperationError):
            cli.deploy_ops(FakeRemote(flip_timer_on_reload=True), yes=True)


class DeployApiTests(unittest.TestCase):
    def test_dependency_change_is_refused(self):
        remote = FakeRemote(requirements="fastapi==0.1\n")
        with self.assertRaises(cli.OperationError):
            cli.deploy_api(remote, yes=True)
        self.assertEqual(remote.installs(), [])

    def test_line_endings_alone_are_not_a_dependency_change(self):
        text = (Path(cli.REPO) / "api/requirements.txt").read_text(encoding="utf-8")
        remote = FakeRemote(requirements=text.replace("\n", "\r\n"))
        cli.deploy_api(remote, yes=True, wait_seconds=0)

    def test_tests_run_as_api_user_and_backup_precedes_install(self):
        remote = FakeRemote()
        result = cli.deploy_api(remote, yes=True, wait_seconds=0)
        test_call = next(c for c in remote.calls if "unittest" in c)
        self.assertEqual(test_call[:3], ["sudo", "-u", "illoura-api"])
        installs = remote.installs()
        self.assertEqual(installs[0][-1], result["previous_backup"])
        self.assertEqual(installs[1][-1], "/opt/illoura-api/app.py")

    def test_unhealthy_new_version_is_rolled_back(self):
        remote = FakeRemote(health_ok=False)
        with self.assertRaises(cli.OperationError):
            cli.deploy_api(remote, yes=True, wait_seconds=0)
        backup = remote.installs()[0][-1]
        self.assertEqual(
            remote.installs()[-1][-2:], [backup, "/opt/illoura-api/app.py"]
        )
        restarts = [c for c in remote.calls if c[-2:] == ["restart", "illoura-api"]]
        self.assertEqual(len(restarts), 2)


class VerifyOnlyTests(unittest.TestCase):
    def test_verify_ops_runs_staged_tests_and_installs_nothing(self):
        remote = FakeRemote(
            health_configured=False
        )  # verification does not need the health setting
        result = cli.deploy_ops(remote, test_only=True)
        self.assertTrue(result["tested"])
        self.assertEqual(remote.installs(), [])
        self.assertTrue(any("unittest" in c for c in remote.calls))
        self.assertTrue(any(c[:2] == ["rm", "-rf"] for c in remote.calls))
        self.assertNotIn(["sudo", "systemctl", "daemon-reload"], remote.calls)

    def test_verify_api_installs_nothing_and_never_restarts(self):
        remote = FakeRemote()
        self.assertTrue(cli.deploy_api(remote, test_only=True)["tested"])
        self.assertEqual(remote.installs(), [])
        self.assertFalse(any("restart" in c for c in remote.calls))


class DailyBriefAdminTests(unittest.TestCase):
    def test_health_sender_is_uploaded_never_put_in_a_command(self):
        with self.assertRaises(cli.OperationError):
            cli.set_health_sender(FakeRemote(), "alerts@example.com")
        with self.assertRaises(ValueError):
            cli.set_health_sender(FakeRemote(), "not an address; rm -rf /", yes=True)
        remote = FakeRemote()
        cli.set_health_sender(remote, "Alerts@Example.com", yes=True)
        self.assertNotIn("alerts@example.com", json.dumps(remote.calls).lower())
        uploaded = next(v for k, v in remote.files.items() if k.endswith("/health.env"))
        self.assertEqual(uploaded, b"ILLOURA_GMAIL_SENDER=alerts@example.com\n")
        install = remote.installs()[-1]
        self.assertEqual(install[-1], cli.HEALTH_ENV)
        self.assertIn("0600", install)

    def test_schedule_needs_yes_and_touches_only_the_brief_timer(self):
        with self.assertRaises(cli.OperationError):
            cli.schedule(FakeRemote(), "enable")
        with self.assertRaises(ValueError):
            cli.schedule(FakeRemote(), "restart", yes=True)
        remote = FakeRemote()
        cli.schedule(remote, "enable", yes=True)
        changes = [c for c in remote.calls if c[:2] == ["sudo", "systemctl"]]
        self.assertEqual(changes, [["sudo", "systemctl", "enable", "--now", cli.BRIEF_TIMER]])

    def test_newly_installed_timer_is_not_a_state_change_but_enabling_is(self):
        remote = FakeRemote()
        remote.timers[cli.BRIEF_TIMER] = "not-found"
        original = remote.run

        def run(argv, timeout=900):
            if argv[-1:] == ["daemon-reload"]:
                remote.timers[cli.BRIEF_TIMER] = "disabled"
            return original(argv, timeout)

        remote.run = run
        cli.deploy_ops(remote, yes=True)
        remote = FakeRemote()
        remote.timers[cli.BRIEF_TIMER] = "not-found"
        original_enabled = remote.run

        def run_enabled(argv, timeout=900):
            if argv[-1:] == ["daemon-reload"]:
                remote.timers[cli.BRIEF_TIMER] = "enabled"
            return original_enabled(argv, timeout)

        remote.run = run_enabled
        with self.assertRaises(cli.OperationError):
            cli.deploy_ops(remote, yes=True)

    def test_deploy_ops_ships_the_daily_brief(self):
        result = cli.deploy_ops(FakeRemote(), yes=True)
        self.assertIn("illoura_daily_brief.py", result["installed"])
        self.assertIn("illoura-daily-brief.timer", result["installed"])


class ApiKeyTests(unittest.TestCase):
    def test_key_stays_private_and_server_gets_only_its_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = cli.Settings(private_dir=Path(folder))
            remote = FakeRemote()
            result = cli.api_key(
                remote, settings, "create", "team-member", host="203.0.113.10"
            )
            credential = json.loads(Path(result["credential_file"]).read_text())
            token = credential["api_key"]
            uploaded = b"".join(remote.files.values())
            self.assertNotIn(token.encode(), uploaded)
            self.assertIn(hashlib.sha256(token.encode()).hexdigest().encode(), uploaded)
            self.assertNotIn(token, json.dumps(remote.calls))

    def test_revoke_and_name_validation(self):
        remote = FakeRemote()
        cli.api_key(remote, cli.Settings(), "revoke", "admin")
        self.assertFalse(json.loads(next(iter(remote.files.values())))[0]["enabled"])
        with self.assertRaises(ValueError):
            cli.api_key(FakeRemote(), cli.Settings(), "create", "bad name; rm")


class SettingsAndMainTests(unittest.TestCase):
    def test_settings_come_from_environment_without_hardcoded_account(self):
        settings = cli.Settings.from_env(
            {"ILLOURA_AWS_ACCOUNT": "000000000000", "ILLOURA_AWS_INSTANCE": "x"}
        )
        self.assertEqual((settings.account, settings.instance), ("000000000000", "x"))
        self.assertEqual(cli.Settings.from_env({}).account, "")

    def test_main_runs_fixed_action_through_injected_remote(self):
        remote = FakeRemote()
        cli.main(["prune", "--dry-run"], remote_factory=lambda settings: remote)
        self.assertEqual(
            remote.calls[-1],
            ["sudo", cli.OPS_PYTHON, cli.OPS_SCRIPT, "prune", "--dry-run"],
        )


class DeployCollectorsTests(unittest.TestCase):
    def server_with_current_code(self, **options):
        """A server whose collector files already match the repo, except aus_contract.py."""
        remote = FakeRemote(**options)
        for name in cli.collector_files():
            remote.files[f"{cli.COLLECTOR_DIR}/{name}"] = cli.lf_bytes(Path(cli.REPO) / name)
        remote.files[f"{cli.COLLECTOR_DIR}/spiders/aus_contract.py"] = b"# old version\n"
        return remote

    def test_ships_only_the_runner_and_spiders(self):
        names = cli.collector_files()
        self.assertEqual(names[0], "run_spiders.py")
        self.assertIn("spiders/aus_contract.py", names)
        self.assertTrue(all(n == "run_spiders.py" or n.startswith("spiders/") for n in names))

    def test_needs_yes(self):
        with self.assertRaises(cli.OperationError):
            cli.deploy_collectors(FakeRemote())

    def test_refuses_while_the_brief_or_collection_runs(self):
        for unit in cli.BUSY_UNITS:
            remote = FakeRemote(busy=(unit,))
            with self.subTest(unit=unit), self.assertRaises(cli.OperationError):
                cli.deploy_collectors(remote, yes=True)
            self.assertEqual(remote.files, {})  # nothing staged or installed

    def test_verify_runs_staged_tests_and_installs_nothing(self):
        remote = FakeRemote()
        result = cli.deploy_collectors(remote, test_only=True)
        self.assertTrue(result["tested"])
        self.assertEqual(remote.installs(), [])
        test_run = next(c for c in remote.calls if "unittest" in c)
        self.assertTrue(test_run[1].startswith("PYTHONPATH=/var/tmp/illoura-stage-"))
        self.assertTrue(any(c[:2] == ["rm", "-rf"] for c in remote.calls))

    def test_failed_tests_install_nothing(self):
        remote = self.server_with_current_code(tests_ok=False)
        with self.assertRaises(cli.OperationError):
            cli.deploy_collectors(remote, yes=True)
        self.assertEqual(remote.installs(), [])

    def test_installs_only_changed_files_as_scraper_after_backup(self):
        remote = self.server_with_current_code()
        result = cli.deploy_collectors(remote, yes=True)
        self.assertEqual(result["installed"], ["spiders/aus_contract.py"])
        files = [c for c in remote.installs() if "-d" not in c]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][2:8], ["-o", "scraper", "-g", "scraper", "-m", "0644"])
        self.assertIn(["sudo", "cp", "-p", f"{cli.COLLECTOR_DIR}/spiders/aus_contract.py",
                       result["backup"] + "/spiders/aus_contract.py"], remote.calls)
        self.assertFalse(any("systemctl" in c and "enable" in c for c in remote.calls))

    def test_up_to_date_server_is_left_alone(self):
        remote = self.server_with_current_code()
        remote.files[f"{cli.COLLECTOR_DIR}/spiders/aus_contract.py"] = cli.lf_bytes(
            Path(cli.REPO) / "spiders/aus_contract.py"
        )
        result = cli.deploy_collectors(remote, yes=True)
        self.assertEqual(result["installed"], [])
        self.assertEqual(remote.installs(), [])


if __name__ == "__main__":
    unittest.main()

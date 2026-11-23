"""AWS operations CLI: fixed remote actions, staged/verified deploys, no network."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock

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
        self.failed = options.get("failed", ())  # units reported by systemctl is-failed
        self.sydney_hhmm = options.get("sydney_hhmm", "1830")
        self.reboot_required = options.get("reboot_required", False)
        self.reconcile = options.get("reconcile", (0, '{"status": "delivered"}\n'))
        self.mail_env = options.get("mail_env", True)
        self.mail_env_result = options.get(
            "mail_env_result", (0, '{"status": "written", "keys": ["ILLOURA_MAIL_SENDER"]}\n'))

    def run(self, argv, timeout=900):
        cli.command_line(argv)  # every call must pass the same safety check as SSH
        self.calls.append(list(argv))
        if argv[:5] == ["sudo", "-u", "scraper", "test", "-r"]:
            return (0 if self.mail_env else 1), "", ""
        if any(a.endswith("/routine_mail_env.py") for a in argv) and argv[0] == "sudo":
            return self.mail_env_result[0], self.mail_env_result[1], ""
        if argv[:2] == ["systemctl", "is-failed"]:
            return (0, "failed\n", "") if argv[2] in self.failed else (1, "active\n", "")
        if argv[:3] == ["env", "TZ=Australia/Sydney", "date"]:
            return 0, self.sydney_hhmm + "\n", ""
        if argv[:2] == ["test", "-f"]:
            return (0 if self.reboot_required else 1), "", ""
        if "reconcile" in argv:
            return self.reconcile[0], self.reconcile[1], ""
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
    def test_research_install_is_staged_and_never_enables_timer(self):
        remote = FakeRemote()
        result = cli.research_refresh(remote, 'install', yes=True)
        self.assertTrue(result['installed'])
        test_index = next(i for i, call in enumerate(remote.calls) if 'unittest' in call)
        install_index = next(i for i, call in enumerate(remote.calls) if call[:2] == ['sudo', 'install'])
        self.assertLess(test_index, install_index)
        self.assertFalse(any('enable' in call or 'disable' in call for call in remote.calls))
        self.assertTrue(any(call[-1] == '/etc/systemd/system/illoura-research-refresh.timer' for call in remote.installs()))

    def test_research_failed_staging_tests_install_nothing(self):
        remote = FakeRemote(tests_ok=False)
        with self.assertRaises(cli.OperationError):
            cli.research_refresh(remote, 'install', yes=True)
        self.assertEqual(remote.installs(), [])

    def test_research_timer_changes_require_explicit_yes(self):
        remote = FakeRemote()
        for action in ('install', 'enable', 'disable'):
            with self.assertRaises(cli.OperationError):
                cli.research_refresh(remote, action)
        self.assertEqual(remote.calls, [])

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


class FakeAws:
    """Stands in for the AWS CLI wrapper; records argument lists."""

    def __init__(self, stack_exists=True, contacts=("ops@example.com",)):
        self.calls, self.stack_exists, self.contacts = [], stack_exists, contacts

    def __call__(self, settings, *args, parse=True):
        self.calls.append(list(args))
        if args[:2] == ("lightsail", "get-contact-methods"):
            return {"contactMethods": [{"contactEndpoint": c, "status": "Valid"} for c in self.contacts]}
        if args[:2] == ("cloudformation", "describe-stacks"):
            if not self.stack_exists:
                raise cli.OperationError("AWS CLI failed: Stack with id illoura-watch does not exist")
            return {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": [
                {"OutputKey": "AlarmName", "OutputValue": "illoura-watch-needs-attention"},
                {"OutputKey": "TopicArn", "OutputValue": "arn:aws:sns:ap-southeast-2:000000000000:t"}]}]}
        if args[:2] == ("cloudwatch", "describe-alarms"):
            return {"MetricAlarms": [{"StateValue": "INSUFFICIENT_DATA", "StateReason": "new"}]}
        if args[:2] == ("sns", "list-subscriptions-by-topic"):
            return {"Subscriptions": [{"SubscriptionArn": "PendingConfirmation", "Endpoint": "ops@example.com"}]}
        if args[:2] == ("lightsail", "get-instance"):
            return {"instance": {"addOns": [{"name": "AutoSnapshot", "status": "Enabled",
                                             "snapshotTimeOfDay": cli.SNAPSHOT_TIME_UTC}]}}
        if args[:2] == ("lightsail", "get-auto-snapshots"):
            return {"autoSnapshots": [{"date": "2026-11-14", "status": "Success"}]}
        return {} if parse else ""


class WatchAndSnapshotTests(unittest.TestCase):
    def settings(self, folder, base="https://api.example.test"):
        Path(folder, "api-access.json").write_text(json.dumps({"api_base_url": base}))
        return cli.Settings(private_dir=Path(folder))

    def test_watch_deploy_needs_yes_and_uses_private_url_and_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            settings, fake = self.settings(folder), FakeAws()
            with unittest.mock.patch.object(cli, "aws", fake):
                with self.assertRaises(cli.OperationError):
                    cli.watch(settings, "deploy")
                result = cli.watch(settings, "deploy", yes=True)
        deploy = next(c for c in fake.calls if c[:2] == ["cloudformation", "deploy"])
        self.assertIn("CAPABILITY_IAM", deploy)
        self.assertIn("ReadyzUrl=https://api.example.test/readyz", deploy)
        self.assertIn("AlertEmail=ops@example.com", deploy)
        self.assertNotIn("ops@example.com", json.dumps(result))  # status never shows the address
        self.assertEqual(result["email_subscriptions"][0][:7], "pending")

    def test_watch_refuses_non_https_url_and_bad_email(self):
        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch.object(cli, "aws", FakeAws()):
                with self.assertRaises(cli.OperationError):
                    cli.watch(self.settings(folder, "http://api.example.test"), "deploy", yes=True)
                with self.assertRaises(ValueError):
                    cli.watch(self.settings(folder), "deploy", yes=True, email="x; rm -rf /")

    def test_watch_status_before_deploy(self):
        with tempfile.TemporaryDirectory() as folder:
            with unittest.mock.patch.object(cli, "aws", FakeAws(stack_exists=False)):
                self.assertEqual(cli.watch(self.settings(folder), "status"), {"stack": "not deployed"})

    def test_snapshots_enable_needs_yes_and_runs_after_the_brief(self):
        with tempfile.TemporaryDirectory() as folder:
            settings, fake = self.settings(folder), FakeAws()
            with unittest.mock.patch.object(cli, "aws", fake):
                with self.assertRaises(cli.OperationError):
                    cli.snapshots(settings, "enable")
                result = cli.snapshots(settings, "enable", yes=True)
        request = next(c for c in fake.calls if c[:2] == ["lightsail", "enable-add-on"])
        self.assertEqual(json.loads(request[-1])["autoSnapshotAddOnRequest"]["snapshotTimeOfDay"], "23:00")
        self.assertEqual(result["snapshots"][0]["status"], "Success")

    def test_template_holds_no_private_values(self):
        text = cli.WATCH_TEMPLATE.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\b\d{1,3}(\.\d{1,3}){3}\b")  # no IP address
        self.assertNotRegex(text, r"\b\d{12}\b")  # no account ID
        self.assertNotRegex(text, r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")  # no address
        self.assertIn("TreatMissingData: breaching", text)

    def test_alarm_fires_on_mostly_failing_checks(self):
        # 13 Nov audit: Maximum < 1 let one good check per 15 minutes hide repeated failures.
        text = cli.WATCH_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("Statistic: Average", text)
        self.assertIn("Threshold: 0.5", text)
        self.assertNotIn("Statistic: Maximum", text)


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
        allowed = {"run_spiders.py", *cli.STANDALONE_COLLECTORS, *cli.COLLECTOR_SUPPORT}
        self.assertTrue(all(n in allowed or n.startswith("spiders/") for n in names))

    def test_standalone_collectors_match_the_runner(self):
        # Every standalone script run_spiders.py starts must be shippable, and nothing else.
        runner = (Path(cli.REPO) / "run_spiders.py").read_text(encoding="utf-8")
        for script in cli.STANDALONE_COLLECTORS:
            self.assertIn(f'("{script}"', runner)
            self.assertTrue((Path(cli.REPO) / script).exists())

    def test_every_collector_test_file_is_staged(self):
        # 13 Nov: a fixed list left a new collector test file out of the server run.
        remote = FakeRemote()
        cli.deploy_collectors(remote, test_only=True)
        staged = {Path(p).name for p in remote.files if "/tests/" in p}
        expected = {p.name for p in (Path(cli.REPO) / "tests").glob("test_collectors_*.py")}
        self.assertEqual(staged, expected)
        self.assertIn("test_collectors_unpublished.py", staged)

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


class OsUpkeepTests(unittest.TestCase):
    """13 Nov audit: Ubuntu's 06:00-07:00 updates + needrestart could restart the waiting brief."""

    def test_harden_os_needs_yes(self):
        with self.assertRaises(cli.OperationError):
            cli.harden_os(FakeRemote())

    def test_harden_os_installs_both_settings_and_verifies_them(self):
        remote = FakeRemote(failed=("cloud-final.service",))
        result = cli.harden_os(remote, yes=True)
        targets = [c[-1] for c in remote.installs() if "-d" not in c]
        self.assertEqual(targets, list(cli.OS_FILES.values()))
        for install in remote.installs():
            self.assertIn("root", install)
        self.assertIn(["sudo", "needrestart", "-b", "-r", "l"], remote.calls)
        self.assertIn(["sudo", "systemctl", "restart", "apt-daily-upgrade.timer"], remote.calls)
        self.assertEqual(result["cleared_failed"], ["cloud-final.service"])
        # Never touches an Illoura timer's enable state.
        self.assertFalse([c for c in remote.calls if "enable" in c or "disable" in c])

    def test_settings_files_say_what_the_audit_needs(self):
        system = Path(cli.REPO) / "deploy/system"
        needrestart = (system / "needrestart-illoura.conf").read_text(encoding="utf-8")
        self.assertIn("$nrconf{override_rc}{qr(^illoura-(daily-brief|cloud-collect|cloud-health)", needrestart)
        self.assertTrue(needrestart.rstrip().endswith("= 0;"))
        apt = (system / "apt-daily-upgrade-illoura.conf").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=\n", apt)  # clears Ubuntu's 06:00 before setting ours
        self.assertIn("OnCalendar=*-*-* 03:30 Australia/Sydney", apt)

    def test_schedule_disable_also_stops_a_waiting_brief(self):
        remote = FakeRemote(busy=(cli.BRIEF_SERVICE,))
        result = cli.schedule(remote, "disable", yes=True)
        self.assertIn(["sudo", "systemctl", "stop", cli.BRIEF_SERVICE], remote.calls)
        self.assertTrue(result["stopped_running_brief"])
        idle = FakeRemote()
        self.assertFalse(cli.schedule(idle, "disable", yes=True)["stopped_running_brief"])
        self.assertNotIn(["sudo", "systemctl", "stop", cli.BRIEF_SERVICE], idle.calls)

    def test_os_update_refuses_in_the_brief_window_or_while_busy(self):
        for remote in (FakeRemote(sydney_hhmm="0700"), FakeRemote(busy=(cli.BRIEF_SERVICE,))):
            with self.assertRaises(cli.OperationError):
                cli.os_update(remote, cli.Settings(), yes=True, reboot=True)
            self.assertFalse([c for c in remote.calls if "unattended-upgrade" in c])
        with self.assertRaises(cli.OperationError):
            cli.os_update(FakeRemote(), cli.Settings())

    def test_os_update_reboots_only_when_needed_and_asked(self):
        waited = []
        waiter = lambda settings, seconds: waited.append(seconds) or "ok"
        remote = FakeRemote(reboot_required=True)
        result = cli.os_update(remote, cli.Settings(), yes=True, reboot=True, waiter=waiter)
        self.assertIn(["sudo", "systemctl", "reboot"], remote.calls)
        self.assertEqual(result["readyz_after_reboot"], "ok")
        for remote, reboot in ((FakeRemote(reboot_required=True), False), (FakeRemote(), True)):
            result = cli.os_update(remote, cli.Settings(), yes=True, reboot=reboot, waiter=waiter)
            self.assertNotIn(["sudo", "systemctl", "reboot"], remote.calls)
            self.assertFalse(result["rebooted"])
        self.assertEqual(len(waited), 1)

    def test_wait_for_readyz_polls_until_ok(self):
        class Response:
            def __init__(self, status):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        answers = [OSError("refused"), Response(503), Response(200)]

        def opener(url):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "api-access.json").write_text('{"api_base_url": "https://203.0.113.5"}')
            settings = cli.Settings(private_dir=Path(temp))
            tick = iter(range(0, 10000, 15))
            result = cli.wait_for_readyz(settings, 600, sleep=lambda s: None, opener=opener,
                                         clock=lambda: next(tick))
        self.assertEqual(result, "ok")
        self.assertEqual(answers, [])

    def test_reconcile_send_runs_as_scraper_and_never_sends(self):
        with self.assertRaises(cli.OperationError):
            cli.reconcile_send(FakeRemote(), "daily_sweep_20261114_004500")
        for bad in ("../etc", "daily_sweep_x", "daily_sweep_20261114_004500;id"):
            with self.assertRaises(ValueError):
                cli.reconcile_send(FakeRemote(), bad, yes=True)
        remote = FakeRemote(reconcile=(2, 'log line\n{"status": "not_found_in_sent"}\n'))
        result = cli.reconcile_send(remote, "daily_sweep_20261114_004500", yes=True)
        self.assertEqual(result, {"status": "not_found_in_sent", "exit_code": 2})
        call = remote.calls[-1]
        self.assertEqual(call[:5], ["sudo", "runuser", "-u", "scraper", "--"])
        self.assertIn("reconcile", call)
        self.assertNotIn("send", call)
        self.assertNotIn("--mark-undelivered", call)
        remote = FakeRemote(reconcile=(0, '{"status": "marked_undelivered"}\n'))
        cli.reconcile_send(remote, "daily_sweep_20261114_004500", mark_undelivered=True, yes=True)
        self.assertEqual(remote.calls[-1][-1], "--mark-undelivered")
        with self.assertRaises(cli.OperationError):
            cli.reconcile_send(FakeRemote(reconcile=(1, "Traceback\n")),
                               "daily_sweep_20261114_004500", yes=True)


class RoutineDeployTests(unittest.TestCase):
    """routine/ ships like the collectors: staged, tested, hash-checked, changed files only."""

    def server_with_routine(self, **options):
        remote = FakeRemote(**options)
        for name in cli.routine_files():
            remote.files[f"{cli.ROUTINE_DIR}/{name}"] = cli.lf_bytes(Path(cli.REPO) / "routine" / name)
        remote.files[f"{cli.ROUTINE_DIR}/scripts/send_gmail.py"] = b"# deployed version\n"
        return remote

    def test_ships_runner_and_scripts_never_legacy(self):
        names = cli.routine_files()
        self.assertEqual(names[0], "run_once.py")
        self.assertIn("scripts/send_gmail.py", names)
        self.assertIn("scripts/mail_config.py", names)
        self.assertFalse([n for n in names if "legacy" in n])

    def test_verify_stages_all_routine_tests_and_installs_nothing(self):
        remote = FakeRemote()
        result = cli.deploy_routine(remote, test_only=True)
        self.assertEqual(result["installed"], [])
        staged = {Path(p).name for p in remote.files if "/tests/" in p}
        self.assertEqual(staged, set(cli.routine_tests()))
        self.assertIn("test_routine_mail_env.py", staged)
        self.assertEqual(remote.installs(), [])

    def test_needs_yes_mail_env_and_an_idle_server(self):
        with self.assertRaises(cli.OperationError):
            cli.deploy_routine(FakeRemote())
        for remote in (self.server_with_routine(mail_env=False),
                       self.server_with_routine(busy=(cli.BRIEF_SERVICE,))):
            with self.assertRaises(cli.OperationError):
                cli.deploy_routine(remote, yes=True)
            self.assertEqual(remote.installs(), [])

    def test_installs_only_changed_files_read_only_for_scraper(self):
        remote = self.server_with_routine()
        result = cli.deploy_routine(remote, yes=True)
        self.assertEqual(result["installed"], ["scripts/send_gmail.py"])
        install = [c for c in remote.installs() if "-d" not in c][-1]
        self.assertEqual(install[-1], f"{cli.ROUTINE_DIR}/scripts/send_gmail.py")
        self.assertEqual(install[2:8], ["-o", "root", "-g", "scraper", "-m", "0640"])
        self.assertTrue(result["backup"].startswith(cli.BACKUP_ROOT + "/routine-"))

    def test_set_mail_config_keeps_values_on_the_server(self):
        with self.assertRaises(cli.OperationError):
            cli.set_mail_config(FakeRemote())
        remote = FakeRemote()
        result = cli.set_mail_config(remote, yes=True)
        self.assertEqual(result["status"], "written")
        call = [c for c in remote.calls
                if c[0] == "sudo" and any(a.endswith("routine_mail_env.py") for a in c)][0]
        self.assertEqual(call[:2], ["sudo", cli.OPS_PYTHON])
        self.assertEqual(call[-2:], ["--write", cli.MAIL_ENV])
        self.assertNotIn("@", " ".join(" ".join(c) for c in remote.calls))
        refused = FakeRemote(mail_env_result=(3, '{"status": "differs_not_replaced"}\n'))
        self.assertEqual(cli.set_mail_config(refused, yes=True)["exit_code"], 3)


if __name__ == "__main__":
    unittest.main()

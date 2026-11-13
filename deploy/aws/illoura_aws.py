"""Illoura AWS operations: one entry point, fixed remote actions only.

Replaces the one-off helpers that used to sit beside the trial report. Every
remote action is a fixed argument list run over an SSH connection whose host key
is pinned; there is deliberately no "run this command" option.

Deploys ship repository files to a staging folder, verify their SHA-256 on the
server, run the tests there, and only then install. Deploys never change API
keys, snapshot pointers or timer enable states, and never enable a timer.

Settings (environment variables; all optional):
  ILLOURA_AWS_PROFILE   AWS CLI profile           default: illoura-trial
  ILLOURA_AWS_REGION    Lightsail region          default: ap-southeast-2
  ILLOURA_AWS_INSTANCE  Lightsail instance name   default: illoura-hosting-trial
  ILLOURA_AWS_ACCOUNT   expected AWS account ID; when set, every run checks it
  ILLOURA_PRIVATE_DIR   admin key + host-key pin  default: ~/.illoura/aws-trial

Examples:
  python deploy/aws/illoura_aws.py status
  python deploy/aws/illoura_aws.py prune --dry-run
  python deploy/aws/illoura_aws.py deploy-ops --yes
"""

import argparse
import ast
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

OPS_DIR = "/opt/illoura-ops"
OPS_PYTHON = "/opt/aus_tender/.venv/bin/python"
OPS_SCRIPT = OPS_DIR + "/illoura_cloud_ops.py"
API_DIR = "/opt/illoura-api"
API_PYTHON = API_DIR + "/.venv/bin/python"
HEALTH_ENV = "/etc/illoura-ops/health.env"
HEALTH_STATUS = "/var/lib/illoura-health/status.json"
PUBLICATION = "/opt/illoura-routine/publication.json"
BACKUP_ROOT = "/var/backups/illoura"
KEYS_FILE = "/etc/illoura-api/keys.json"
BRIEF_TIMER = "illoura-daily-brief.timer"
BRIEF_SERVICE = "illoura-daily-brief.service"
TIMERS = (
    "illoura-cloud-collect.timer",
    "illoura-cloud-health.timer",
    "illoura-cert-renew.timer",
    BRIEF_TIMER,
)

OPS_FILES = (
    "illoura_cloud_ops.py",
    "illoura_cloud_health.py",
    "illoura_cloud_collect.py",
    "illoura_daily_brief.py",
)
OPS_UNITS = (
    "illoura-cloud-collect.service",
    "illoura-cloud-collect.timer",
    "illoura-cloud-health.service",
    "illoura-cloud-health.timer",
    "illoura-daily-brief.service",
    "illoura-daily-brief.timer",
)
OPS_TESTS = (
    "test_cloud_ops.py",
    "test_cloud_retention.py",
    "test_cloud_health.py",
    "test_cloud_daily_brief.py",
    "test_cloud_collect.py",
)
# Collector code: the source runner and the spiders package only (see deploy_collectors).
COLLECTOR_DIR = "/opt/aus_tender"
COLLECTOR_TEST_PATTERN = "test_collectors_*.py"  # every collector test file is staged and run
BUSY_UNITS = ("illoura-daily-brief.service", "illoura-cloud-collect.service")
# External watch (CloudFormation stack in the project Region) and Lightsail snapshots.
WATCH_STACK = "illoura-watch"
WATCH_TEMPLATE = REPO / "deploy/aws/illoura-watch.yaml"
SNAPSHOT_TIME_UTC = "23:00"  # 10:00 AEDT / 09:00 AEST: after the 08:15 Sydney brief
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
DRY_RUN_DIR = re.compile(r"^/var/tmp/illoura-brief-dryrun-[0-9TZ]+$")

# Plain tokens only. '*' and '^' are allowed because every argument is shell-quoted
# (no globbing or expansion happens); spaces, quotes, ;, |, &, $ and ` are refused.
SAFE_ARG = re.compile(r"^[A-Za-z0-9_./=:@%+,*^-]+$")
BACKUP_PATH = re.compile(r"^/var/backups/illoura/operations-[0-9A-Za-z-]+$")
STAGE_PATH = re.compile(r"^/var/tmp/illoura-stage-[0-9TZ]+-[0-9a-f]{8}$")
KEY_NAME = re.compile(r"^[A-Za-z0-9_-]{2,50}$")


class OperationError(RuntimeError):
    """A remote step failed; the message says what was (and was not) changed."""


@dataclass
class Settings:
    profile: str = "illoura-trial"
    region: str = "ap-southeast-2"
    instance: str = "illoura-hosting-trial"
    account: str = ""
    private_dir: Path = field(
        default_factory=lambda: Path.home() / ".illoura" / "aws-trial"
    )

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        base = cls()
        return cls(
            profile=env.get("ILLOURA_AWS_PROFILE", base.profile),
            region=env.get("ILLOURA_AWS_REGION", base.region),
            instance=env.get("ILLOURA_AWS_INSTANCE", base.instance),
            account=env.get("ILLOURA_AWS_ACCOUNT", ""),
            private_dir=Path(env.get("ILLOURA_PRIVATE_DIR", str(base.private_dir))),
        )


def command_line(argv):
    """Quote a fixed argument list; reject anything that is not a plain token."""
    if not argv or not all(isinstance(a, str) and SAFE_ARG.match(a) for a in argv):
        raise ValueError("Refusing an unsafe remote argument list")
    return " ".join(shlex.quote(a) for a in argv)


def lf_bytes(path):
    """Ship text with Unix line endings regardless of the Windows checkout."""
    return Path(path).read_bytes().replace(b"\r\n", b"\n")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- AWS CLI


def aws_cli():
    found = shutil.which("aws")
    if found:
        return found
    fallback = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Amazon/AWSCLIV2/aws.exe"
    )
    if fallback.exists():
        return str(fallback)
    raise OperationError("AWS CLI not found")


def aws(settings, *args, parse=True):
    result = subprocess.run(
        [
            aws_cli(),
            *args,
            "--profile",
            settings.profile,
            "--region",
            settings.region,
            "--output",
            "json",
            "--no-cli-pager",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise OperationError("AWS CLI failed: " + result.stderr.strip()[:300])
    return json.loads(result.stdout or "{}") if parse else result.stdout


def check_account(settings):
    if settings.account:
        actual = aws(settings, "sts", "get-caller-identity")["Account"]
        if actual != settings.account:
            raise OperationError(
                "AWS profile points at a different account than ILLOURA_AWS_ACCOUNT"
            )


# --------------------------------------------------------------------------- remote


class SshRemote:
    """Pinned-host SSH. Unknown host keys are accepted only if AWS vouches for them."""

    def __init__(self, settings):
        import paramiko  # deploy-only dependency (requirements-deploy.txt)

        instance = aws(
            settings, "lightsail", "get-instance", "--instance-name", settings.instance
        )["instance"]
        host = instance["publicIpAddress"]
        details = aws(
            settings,
            "lightsail",
            "get-instance-access-details",
            "--instance-name",
            settings.instance,
            "--protocol",
            "ssh",
        )["accessDetails"]
        vouched = {
            k["publicKey"].split()[-1]
            for k in details.get("hostKeys", [])
            if k.get("publicKey")
        }
        known = settings.private_dir / "known_hosts"

        class AwsVouched(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if key.get_base64() not in vouched:
                    raise OperationError(
                        "SSH host key is neither pinned nor confirmed by AWS"
                    )
                client.get_host_keys().add(hostname, key.get_name(), key)
                client.save_host_keys(str(known))

        self.client = paramiko.SSHClient()
        if known.exists():
            self.client.load_host_keys(str(known))
        self.client.set_missing_host_key_policy(AwsVouched())
        self.client.connect(
            host,
            username="ubuntu",
            key_filename=str(settings.private_dir / "admin.pem"),
            timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
        self.host = host

    def run(self, argv, timeout=900):
        _, out, err = self.client.exec_command(command_line(argv), timeout=timeout)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace")
        return out.channel.recv_exit_status(), stdout, stderr

    def put_bytes(self, data, remote_path, mode=0o644):
        with self.client.open_sftp() as sftp:
            with sftp.open(remote_path, "wb") as handle:
                handle.write(data)
            sftp.chmod(remote_path, mode)

    def close(self):
        self.client.close()


def must(remote, argv, what, timeout=900):
    code, out, err = remote.run(argv, timeout=timeout)
    if code:
        raise OperationError(
            f"{what} failed (exit {code}): {(err or out).strip()[-400:]}"
        )
    return out


def run_tests(remote, argv, what):
    """unittest reports on stderr; return its 'Ran N tests ... OK' summary as evidence."""
    code, out, err = remote.run(argv, timeout=1800)
    text = (out + "\n" + err).strip()
    if code:
        raise OperationError(f"{what} failed (exit {code}): {text[-600:]}")
    return " ".join(
        line for line in text.splitlines()[-4:] if line.strip() and set(line) != {"-"}
    )


# --------------------------------------------------------------------------- staging


def new_stage():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"/var/tmp/illoura-stage-{stamp}-{secrets.token_hex(4)}"


def stage_files(remote, stage, files):
    """Upload {relative path: bytes}; verify every SHA-256 on the server."""
    if not STAGE_PATH.match(stage):
        raise ValueError("Unexpected staging path")
    folders = sorted(
        {str(Path(rel).parent).replace("\\", "/") for rel in files} - {"."}
    )
    must(
        remote,
        ["install", "-d", "-m", "0755", stage, *[stage + "/" + f for f in folders]],
        "create staging",
    )
    for rel, data in files.items():
        remote.put_bytes(data, stage + "/" + rel)
    out = must(
        remote,
        ["sha256sum", *[stage + "/" + rel for rel in files]],
        "hash staged files",
    )
    remote_hashes = {
        line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()
    }
    for rel, data in files.items():
        if remote_hashes.get(stage + "/" + rel) != sha256(data):
            raise OperationError(
                f"Staged file {rel} does not match its local hash; nothing installed"
            )


def remove_stage(remote, stage):
    if STAGE_PATH.match(stage):
        remote.run(["rm", "-rf", stage])


def timer_states(remote):
    states = {}
    for timer in TIMERS:
        _, out, _ = remote.run(["systemctl", "is-enabled", timer])
        states[timer] = out.strip() or "unknown"
    return states


def api_policy(app_source):
    """The publication policy is the API's own table allowlist."""
    for node in ast.parse(app_source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "ALLOWED" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("ALLOWED table policy not found in api/app.py")


# --------------------------------------------------------------------------- actions


def status(remote, settings=None):
    report = {}
    if settings is not None:
        inst = aws(
            settings, "lightsail", "get-instance", "--instance-name", settings.instance
        )["instance"]
        ports = aws(
            settings,
            "lightsail",
            "get-instance-port-states",
            "--instance-name",
            settings.instance,
        )
        report["instance"] = {
            "state": inst["state"]["name"],
            "bundle": inst["bundleId"],
            "static_ip": inst.get("isStaticIp"),
        }
        report["firewall"] = [
            f"{p['fromPort']}/{p['protocol']} {','.join(p.get('cidrs', []))}"
            for p in ports.get("portStates", [])
        ]
    _, out, _ = remote.run(["systemctl", "is-active", "illoura-api", "nginx"])
    report["services"] = dict(zip(["illoura-api", "nginx"], out.split(), strict=False))
    report["timers"] = timer_states(remote)
    code, out, _ = remote.run(["sudo", "cat", HEALTH_STATUS])
    if code == 0:
        health = json.loads(out)
        report["health"] = {
            "status": health.get("status"),
            "checked_at_utc": health.get("checked_at_utc"),
            "checks": {k: v.get("status") for k, v in health.get("checks", {}).items()},
        }
    code, out, _ = remote.run(["sudo", "cat", PUBLICATION])
    if code == 0:
        pub = json.loads(out)
        report["publication"] = {
            k: pub.get(k)
            for k in (
                "status",
                "generation_id",
                "snapshot_finished_utc",
                "retention_attention",
            )
        }
    code, out, _ = remote.run(["df", "-B1", "--output=avail,size", "/srv/illoura-data"])
    if code == 0 and len(out.split()) >= 4:
        avail, size = (int(x) for x in out.split()[-2:])
        report["disk"] = {
            "free_gb": round(avail / 1e9, 1),
            "total_gb": round(size / 1e9, 1),
        }
    return report


MUTATING = {"publish", "rollback", "prune"}


def ops_action(remote, action, *, dry_run=False, backup=None, yes=False):
    if action not in {"publish", "backup", "restore-drill", "rollback", "prune"}:
        raise ValueError("Unknown operations action")
    if action in MUTATING and not dry_run and not yes:
        raise OperationError(f"{action} changes production data; rerun with --yes")
    argv = ["sudo", OPS_PYTHON, OPS_SCRIPT, action]
    if dry_run:
        if action != "prune":
            raise ValueError("--dry-run applies to prune only")
        argv.append("--dry-run")
    if action == "restore-drill":
        if not backup or not BACKUP_PATH.match(backup):
            raise ValueError("--backup must be /var/backups/illoura/operations-<id>")
        argv += ["--backup", backup]
    code, out, err = remote.run(argv, timeout=3600)
    # Exit 1 after publish/prune can mean "published, retention needs attention":
    # the printed JSON is the source of truth, so return it with the exit code.
    return {
        "exit_code": code,
        "output": out.strip()[-4000:],
        "stderr": err.strip()[-1000:],
    }


def health_sender_configured(remote):
    code, out, _ = remote.run(
        ["sudo", "grep", "-c", "^ILLOURA_GMAIL_SENDER=.", HEALTH_ENV]
    )
    return code == 0 and out.strip() not in ("", "0")


def deploy_ops(
    remote, *, yes=False, allow_unconfigured_health=False, repo=REPO, test_only=False
):
    """Stage, verify and test operations code; install unless test_only."""
    if not yes and not test_only:
        raise OperationError("deploy-ops changes the server; rerun with --yes")
    health_source = (repo / "deploy/illoura_cloud_health.py").read_text(
        encoding="utf-8"
    )
    if (
        not test_only
        and "ILLOURA_GMAIL_SENDER" in health_source
        and not allow_unconfigured_health
        and not health_sender_configured(remote)
    ):
        raise OperationError(
            f"{HEALTH_ENV} lacks ILLOURA_GMAIL_SENDER; create it first "
            "(see docs/cloud_operations.md). Nothing was changed."
        )
    files = {
        "deploy/" + n: lf_bytes(repo / "deploy" / n) for n in OPS_FILES + OPS_UNITS
    }
    files.update({"tests/" + n: lf_bytes(repo / "tests" / n) for n in OPS_TESTS})
    policy = api_policy((repo / "api/app.py").read_text(encoding="utf-8"))
    files["deploy/policy.json"] = json.dumps(policy, indent=2).encode()
    before = timer_states(remote)
    stage = new_stage()
    try:
        stage_files(remote, stage, files)
        # The tests locate ../deploy themselves; tests/ is not a package, so no -t here.
        tests = run_tests(
            remote,
            [
                OPS_PYTHON,
                "-m",
                "unittest",
                "discover",
                "-s",
                stage + "/tests",
                "-p",
                "test_cloud_*.py",
            ],
            "staged operations tests (nothing installed)",
        )
        if test_only:
            return {"tested": True, "installed": [], "tests": tests}
        for name in OPS_FILES + ("policy.json",):
            must(
                remote,
                [
                    "sudo",
                    "install",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    "-m",
                    "0644",
                    f"{stage}/deploy/{name}",
                    f"{OPS_DIR}/{name}",
                ],
                "install " + name,
            )
        must(
            remote,
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                f"{stage}/deploy/illoura_cloud_ops.py",
                f"{OPS_DIR}/deploy/illoura_cloud_ops.py",
            ],
            "install ops test copy",
        )
        for unit in OPS_UNITS:
            must(
                remote,
                [
                    "sudo",
                    "install",
                    "-o",
                    "root",
                    "-g",
                    "root",
                    "-m",
                    "0644",
                    f"{stage}/deploy/{unit}",
                    f"/etc/systemd/system/{unit}",
                ],
                "install " + unit,
            )
        must(remote, ["sudo", "systemctl", "daemon-reload"], "systemd reload")
    finally:
        remove_stage(remote, stage)
    after = timer_states(remote)
    # A newly installed unit appears as "disabled"; anything else must be unchanged.
    changed = {t: (before.get(t), after.get(t)) for t in TIMERS
               if before.get(t) != after.get(t)
               and not (before.get(t) == "not-found" and after.get(t) == "disabled")}
    if changed:
        raise OperationError(f"Timer states changed unexpectedly: {changed}")
    remote.run(
        ["sudo", "systemctl", "start", "illoura-cloud-health.service"], timeout=180
    )
    return {
        "installed": list(OPS_FILES) + ["policy.json"] + list(OPS_UNITS),
        "timers": after,
        "hashes": {k: sha256(v) for k, v in files.items() if k.startswith("deploy/")},
    }


# The two sources run_spiders.py starts as standalone scripts (see its STANDALONE_SCRIPTS).
STANDALONE_COLLECTORS = ("austender_api.py", "nsw_tender_fetch.py")


def collector_files(repo=REPO):
    """The collector code this tool may ship: run_spiders.py, its standalone source scripts
    and spiders/*.py, nothing else."""
    spiders = sorted("spiders/" + p.name for p in (Path(repo) / "spiders").glob("*.py"))
    return ["run_spiders.py", *STANDALONE_COLLECTORS, *spiders]


def collector_tests(repo=REPO):
    """Every tests/test_collectors_*.py file, so a new collector test cannot be left behind."""
    return sorted(p.name for p in (Path(repo) / "tests").glob(COLLECTOR_TEST_PATTERN))


def deploy_collectors(remote, *, yes=False, repo=REPO, test_only=False):
    """Stage, verify and test collector code; install only the files that changed.

    Refuses while a collection or the daily brief is running. Replaced files are kept in
    /var/backups/illoura/collectors-<time>/. Never touches databases, settings or timers.
    """
    if not yes and not test_only:
        raise OperationError("deploy-collectors changes the server; rerun with --yes")
    for unit in BUSY_UNITS:
        _, out, _ = remote.run(["systemctl", "is-active", unit])
        if out.strip() in ("active", "activating", "deactivating", "reloading"):
            raise OperationError(
                f"{unit} is running; deploy collectors after it finishes. Nothing was changed."
            )
    names = collector_files(repo)
    files = {name: lf_bytes(Path(repo) / name) for name in names}
    files.update(
        {"tests/" + n: lf_bytes(Path(repo) / "tests" / n) for n in collector_tests(repo)}
    )
    stage = new_stage()
    try:
        stage_files(remote, stage, files)
        # The staged runner and spiders package come first on the path; everything else they
        # import (database_manager, pipelines, settings) is the installed project, read-only.
        tests = run_tests(
            remote,
            [
                "env",
                f"PYTHONPATH={stage}:{COLLECTOR_DIR}",
                "GSHEETS_AUTO_SYNC=False",
                OPS_PYTHON,
                "-m",
                "unittest",
                "discover",
                "-s",
                stage + "/tests",
                "-p",
                COLLECTOR_TEST_PATTERN,
            ],
            "staged collector tests (nothing installed)",
        )
        if test_only:
            return {"tested": True, "installed": [], "tests": tests}
        installed = [f"{COLLECTOR_DIR}/{n}" for n in names]
        _, out, _ = remote.run(["sudo", "sha256sum", *installed])
        current = {
            line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()
        }
        changed = [n for n in names if current.get(f"{COLLECTOR_DIR}/{n}") != sha256(files[n])]
        if not changed:
            return {"installed": [], "tests": tests, "note": "server already matches"}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = f"{BACKUP_ROOT}/collectors-{stamp}"
        must(
            remote,
            ["sudo", "install", "-d", "-o", "root", "-g", "root", "-m", "0700",
             backup, backup + "/spiders"],
            "create collector backup folder",
        )
        for name in changed:
            target = f"{COLLECTOR_DIR}/{name}"
            if target in current:
                must(remote, ["sudo", "cp", "-p", target, f"{backup}/{name}"], "back up " + name)
            must(
                remote,
                ["sudo", "install", "-o", "scraper", "-g", "scraper", "-m", "0644",
                 f"{stage}/{name}", target],
                "install " + name,
            )
        out = must(
            remote,
            ["sudo", "sha256sum", *[f"{COLLECTOR_DIR}/{n}" for n in changed]],
            "hash installed collectors",
        )
        after = {line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()}
        wrong = [n for n in changed if after.get(f"{COLLECTOR_DIR}/{n}") != sha256(files[n])]
        if wrong:
            raise OperationError(
                f"Installed collector files do not match: {wrong}; originals are in {backup}"
            )
    finally:
        remove_stage(remote, stage)
    return {
        "installed": changed,
        "backup": backup,
        "tests": tests,
        "hashes": {n: sha256(files[n]) for n in changed},
    }


def deploy_api(remote, *, yes=False, repo=REPO, wait_seconds=30, test_only=False):
    """Stage and test api/app.py as the API user; install with rollback unless test_only."""
    if not yes and not test_only:
        raise OperationError("deploy-api restarts the live API; rerun with --yes")
    files = {
        n: lf_bytes(repo / "api" / n)
        for n in ("app.py", "test_security.py", "requirements.txt")
    }
    code, out, _ = remote.run(["sudo", "cat", API_DIR + "/requirements.txt"])
    server_lines = {line.strip() for line in out.splitlines() if line.strip()}
    local_lines = {
        line.strip()
        for line in files["requirements.txt"].decode().splitlines()
        if line.strip()
    }
    if code or server_lines != local_lines:
        raise OperationError(
            "api/requirements.txt differs from the server; dependency changes need a "
            "reviewed manual install. Nothing was changed."
        )
    stage = new_stage()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{BACKUP_ROOT}/api-app-{stamp}.py"
    try:
        stage_files(remote, stage, files)
        tests = run_tests(
            remote,
            [
                "sudo",
                "-u",
                "illoura-api",
                API_PYTHON,
                "-m",
                "unittest",
                "discover",
                "-s",
                stage,
                "-p",
                "test_security.py",
                "-t",
                stage,
            ],
            "staged API tests (nothing installed)",
        )
        if test_only:
            return {"tested": True, "installed": [], "tests": tests}
        must(
            remote,
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0600",
                API_DIR + "/app.py",
                backup,
            ],
            "back up live API",
        )
        must(
            remote,
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "illoura-api",
                "-m",
                "0640",
                stage + "/app.py",
                API_DIR + "/app.py",
            ],
            "install API",
        )
    finally:
        remove_stage(remote, stage)
    must(remote, ["sudo", "systemctl", "restart", "illoura-api"], "restart API")
    deadline = time.monotonic() + wait_seconds
    while True:
        code, _, _ = remote.run(
            ["curl", "-fsS", "--max-time", "5", "http://127.0.0.1:8000/healthz"]
        )
        if code == 0:
            return {
                "installed_sha256": sha256(files["app.py"]),
                "previous_backup": backup,
            }
        if time.monotonic() >= deadline:
            remote.run(
                [
                    "sudo",
                    "install",
                    "-o",
                    "root",
                    "-g",
                    "illoura-api",
                    "-m",
                    "0640",
                    backup,
                    API_DIR + "/app.py",
                ]
            )
            remote.run(["sudo", "systemctl", "restart", "illoura-api"])
            raise OperationError(
                "New API did not become healthy; previous version restored from "
                + backup
            )
        time.sleep(2)


def api_key(remote, settings, action, name, *, host=None):
    """Create or revoke a named key. The key itself is written only to the private directory."""
    if not KEY_NAME.match(name):
        raise ValueError("Use a simple name: letters, digits, _ or -")
    keys = json.loads(must(remote, ["sudo", "cat", KEYS_FILE], "read key hashes"))
    existing = next((k for k in keys if k["name"] == name), None)
    result = {}
    if action == "create":
        if existing:
            raise OperationError("Name already exists; use a new name to rotate")
        token = "illoura_" + secrets.token_urlsafe(36)
        keys.append({"name": name, "sha256": sha256(token.encode()), "enabled": True})
        host = (
            host
            or aws(
                settings,
                "lightsail",
                "get-instance",
                "--instance-name",
                settings.instance,
            )["instance"]["publicIpAddress"]
        )
        path = settings.private_dir / f"{name}-access.json"
        path.write_text(
            json.dumps(
                {
                    "name": name,
                    "api_key": token,
                    "api_base_url": "https://" + host,
                    "mcp_url": "https://" + host + "/mcp",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        result["credential_file"] = str(path)
    elif action == "revoke":
        if not existing:
            raise OperationError("Unknown key name")
        existing["enabled"] = False
    else:
        raise ValueError("Unknown key action")
    stage = new_stage()
    try:
        must(remote, ["install", "-d", "-m", "0700", stage], "create staging")
        remote.put_bytes(json.dumps(keys).encode(), stage + "/keys.json", mode=0o600)
        must(
            remote,
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "illoura-api",
                "-m",
                "0640",
                stage + "/keys.json",
                KEYS_FILE,
            ],
            "install key hashes",
        )
    finally:
        remove_stage(remote, stage)
    result["action"] = action
    result["name"] = name
    return result


def set_health_sender(remote, email, *, yes=False):
    """Write the private expected-mailbox setting; the address is uploaded, never put in a command."""
    if not yes:
        raise OperationError("set-health-sender changes server configuration; rerun with --yes")
    if not EMAIL.match(email or ""):
        raise ValueError("Use one plain email address")
    stage = new_stage()
    try:
        must(remote, ["install", "-d", "-m", "0700", stage], "create staging")
        remote.put_bytes(
            f"ILLOURA_GMAIL_SENDER={email.strip().lower()}\n".encode(), stage + "/health.env", mode=0o600
        )
        must(remote, ["sudo", "install", "-d", "-o", "root", "-g", "root", "-m", "0755", "/etc/illoura-ops"],
             "create /etc/illoura-ops")
        must(remote, ["sudo", "install", "-o", "root", "-g", "root", "-m", "0600", stage + "/health.env",
                      HEALTH_ENV], "install health.env")
    finally:
        remove_stage(remote, stage)
    return {"configured": HEALTH_ENV, "value_printed": False}


def daily_brief_dry_run(remote):
    """Compose a copy of the latest run on the server (live listing checks, layout checks); never sends."""
    code, out, err = remote.run(["sudo", OPS_PYTHON, OPS_DIR + "/illoura_daily_brief.py", "dry-run"], timeout=3600)
    if code:
        raise OperationError("Daily brief dry run failed: " + (err or out).strip()[-800:])
    summaries = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                summaries.append(json.loads(line))
            except ValueError:
                pass
    folder = next((s["dry_run_dir"] for s in summaries if "dry_run_dir" in s), "")
    result = {"summaries": summaries, "sent": False}
    if DRY_RUN_DIR.match(folder):
        code, text, _ = remote.run(["sudo", "cat", folder + "/newsletter_preview.txt"])
        if code == 0:
            result["preview_text"] = text[:6000]
    return result


def readyz_url(settings):
    """The public /readyz URL, from the private API access file (never the repository)."""
    access = json.loads((settings.private_dir / "api-access.json").read_text(encoding="utf-8"))
    base = str(access.get("api_base_url", "")).rstrip("/")
    if not re.match(r"^https://[A-Za-z0-9.-]+(:[0-9]+)?$", base):
        raise OperationError("api-access.json has no usable https api_base_url")
    return base + "/readyz"


def alert_email(settings, email=None):
    """--email if given, else the verified Lightsail alarm contact. Never printed."""
    if email:
        if not EMAIL.match(email.strip()):
            raise ValueError("Not a plain email address")
        return email.strip()
    methods = aws(settings, "lightsail", "get-contact-methods", "--protocols", "Email")
    valid = [
        m["contactEndpoint"]
        for m in methods.get("contactMethods", [])
        if m.get("status") == "Valid" and m.get("contactEndpoint")
    ]
    if not valid:
        raise OperationError("No verified Lightsail email contact; pass --email")
    return valid[0]


def watch_status(settings):
    try:
        stack = aws(settings, "cloudformation", "describe-stacks", "--stack-name", WATCH_STACK)
    except OperationError as error:
        if "does not exist" in str(error):
            return {"stack": "not deployed"}
        raise
    stack = stack["Stacks"][0]
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
    result = {"stack": stack["StackStatus"]}
    if "AlarmName" in outputs:
        alarm = aws(settings, "cloudwatch", "describe-alarms", "--alarm-names", outputs["AlarmName"])
        alarm = (alarm.get("MetricAlarms") or [{}])[0]
        result.update(alarm=alarm.get("StateValue"), alarm_reason=alarm.get("StateReason"))
    if "TopicArn" in outputs:
        subs = aws(settings, "sns", "list-subscriptions-by-topic", "--topic-arn", outputs["TopicArn"])
        # Addresses are private: report only whether each subscription is confirmed.
        result["email_subscriptions"] = [
            "pending confirmation (click the link in the AWS email)"
            if s.get("SubscriptionArn") == "PendingConfirmation"
            else "confirmed"
            for s in subs.get("Subscriptions", [])
        ]
    return result


def watch(settings, action, *, yes=False, email=None):
    """Deploy or report the external /readyz watch (Lambda + alarm + SNS email)."""
    if action == "status":
        return watch_status(settings)
    if not yes:
        raise OperationError("watch deploy creates AWS resources; rerun with --yes")
    url, address = readyz_url(settings), alert_email(settings, email)
    aws(
        settings,
        "cloudformation",
        "deploy",
        "--template-file",
        str(WATCH_TEMPLATE),
        "--stack-name",
        WATCH_STACK,
        "--capabilities",
        "CAPABILITY_IAM",
        "--no-fail-on-empty-changeset",
        "--parameter-overrides",
        f"ReadyzUrl={url}",
        f"AlertEmail={address}",
        parse=False,
    )
    return watch_status(settings)


def snapshots(settings, action, *, yes=False):
    """Daily Lightsail automatic snapshots of the whole server (kept: newest 7)."""
    if action == "enable":
        if not yes:
            raise OperationError("snapshots enable adds a paid add-on; rerun with --yes")
        request = {
            "addOnType": "AutoSnapshot",
            "autoSnapshotAddOnRequest": {"snapshotTimeOfDay": SNAPSHOT_TIME_UTC},
        }
        aws(
            settings,
            "lightsail",
            "enable-add-on",
            "--resource-name",
            settings.instance,
            "--add-on-request",
            json.dumps(request),
        )
    instance = aws(settings, "lightsail", "get-instance", "--instance-name", settings.instance)
    add_ons = [a for a in instance["instance"].get("addOns") or [] if a.get("name") == "AutoSnapshot"]
    taken = aws(settings, "lightsail", "get-auto-snapshots", "--resource-name", settings.instance)
    return {
        "auto_snapshot": add_ons[0] if add_ons else "off",
        "snapshots": [
            {"date": s.get("date"), "status": s.get("status")}
            for s in taken.get("autoSnapshots", [])
        ],
    }


def schedule(remote, action, *, yes=False):
    """Turn the 08:15 daily brief on or off. The only action that changes a timer's enable state."""
    if not yes:
        raise OperationError("schedule changes the daily email; rerun with --yes")
    if action not in ("enable", "disable"):
        raise ValueError("Use enable or disable")
    must(remote, ["sudo", "systemctl", action, "--now", BRIEF_TIMER], f"{action} {BRIEF_TIMER}")
    stopped = False
    if action == "disable":
        # Stopping the timer does not stop a brief that already started and is waiting
        # for 08:15; "off" must mean no email today either.
        _, state, _ = remote.run(["systemctl", "is-active", BRIEF_SERVICE])
        if state.strip() in ("active", "activating"):
            must(remote, ["sudo", "systemctl", "stop", BRIEF_SERVICE], "stop the running brief")
            stopped = True
    _, listing, _ = remote.run(["systemctl", "list-timers", BRIEF_TIMER, "--no-pager"])
    return {"timer": BRIEF_TIMER, "enabled": timer_states(remote)[BRIEF_TIMER],
            "stopped_running_brief": stopped, "list_timers": listing.strip()}


# --------------------------------------------------------------------------- OS upkeep

# deploy/system/<name> -> installed path. See harden_os.
OS_FILES = {
    "needrestart-illoura.conf": "/etc/needrestart/conf.d/illoura.conf",
    "apt-daily-upgrade-illoura.conf": "/etc/systemd/system/apt-daily-upgrade.timer.d/illoura.conf",
}
# Failed states that are known and harmless: cloud-init's launch script (12 Nov) and a past
# brief run. Clearing them keeps `systemctl --failed` meaningful.
STALE_FAILED_UNITS = ("cloud-final.service", BRIEF_SERVICE)
BRIEF_WINDOW = ("0530", "0930")  # Sydney HHMM: no package upgrades or reboots in this window


def harden_os(remote, *, yes=False, repo=REPO):
    """Keep Ubuntu's own upkeep out of the daily brief's 06:15-08:15 Sydney window.

    Installs two small settings files (needrestart never restarts Illoura jobs; security
    updates run at 03:30-04:00 Sydney) and clears known stale failed states. Changes no
    Illoura timer, service, data or code.
    """
    if not yes:
        raise OperationError("harden-os changes server settings; rerun with --yes")
    files = {name: lf_bytes(Path(repo) / "deploy/system" / name) for name in OS_FILES}
    stage = new_stage()
    try:
        stage_files(remote, stage, files)
        folders = sorted({str(Path(t).parent).replace("\\", "/") for t in OS_FILES.values()})
        must(remote, ["sudo", "install", "-d", "-o", "root", "-g", "root", "-m", "0755", *folders],
             "create settings folders")
        for name, target in OS_FILES.items():
            must(remote, ["sudo", "install", "-o", "root", "-g", "root", "-m", "0644",
                          f"{stage}/{name}", target], "install " + target)
        out = must(remote, ["sudo", "sha256sum", *OS_FILES.values()], "hash installed settings")
        after = {line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()}
        wrong = [t for n, t in OS_FILES.items() if after.get(t) != sha256(files[n])]
        if wrong:
            raise OperationError("Installed settings do not match: " + ", ".join(wrong))
        # needrestart refuses to run with a broken settings file; list mode restarts nothing.
        must(remote, ["sudo", "needrestart", "-b", "-r", "l"], "needrestart reads the new setting")
        must(remote, ["sudo", "systemctl", "daemon-reload"], "reload systemd")
        must(remote, ["sudo", "systemctl", "restart", "apt-daily-upgrade.timer"],
             "reschedule security updates")
        next_run = must(remote, ["systemctl", "show", "apt-daily-upgrade.timer", "-p",
                                 "NextElapseUSecRealtime", "--value"], "read next update time")
        cleared = []
        for unit in STALE_FAILED_UNITS:
            _, state, _ = remote.run(["systemctl", "is-failed", unit])
            if state.strip() == "failed":
                must(remote, ["sudo", "systemctl", "reset-failed", unit], "clear " + unit)
                cleared.append(unit)
        return {"installed": list(OS_FILES.values()), "security_updates_next": next_run.strip(),
                "cleared_failed": cleared}
    finally:
        remove_stage(remote, stage)


def wait_for_readyz(settings, wait_seconds, *, sleep=time.sleep, opener=None, clock=time.monotonic):
    """Poll the public /readyz until it answers 200; used after a reboot."""
    import urllib.request

    url = readyz_url(settings)
    opener = opener or (lambda u: urllib.request.urlopen(u, timeout=10))
    deadline = clock() + wait_seconds
    sleep(45)  # give the server time to go down before the first poll
    while clock() < deadline:
        try:
            with opener(url) as response:
                if response.status == 200:
                    return "ok"
        except (OSError, ValueError):  # urllib errors (incl. 503) are OSError subclasses
            pass
        sleep(15)
    raise OperationError(
        f"/readyz did not answer 200 within {wait_seconds} s of the reboot; "
        "check the instance in Lightsail and run status"
    )


def os_update(remote, settings, *, yes=False, reboot=False, wait_seconds=600, waiter=wait_for_readyz):
    """Install pending Ubuntu security updates now, and reboot if one needs it.

    Refuses inside the 05:30-09:30 Sydney brief window and while a collection or brief
    is running. The API is unavailable for about a minute during a reboot.
    """
    if not yes:
        raise OperationError("os-update installs packages; rerun with --yes")
    for unit in BUSY_UNITS:
        _, out, _ = remote.run(["systemctl", "is-active", unit])
        if out.strip() in ("active", "activating", "deactivating", "reloading"):
            raise OperationError(f"{unit} is running; nothing was changed. Try after it finishes.")
    hhmm = must(remote, ["env", "TZ=Australia/Sydney", "date", "+%H%M"], "read Sydney time").strip()
    if BRIEF_WINDOW[0] <= hhmm < BRIEF_WINDOW[1]:
        raise OperationError("Inside the 05:30-09:30 Sydney brief window; nothing was changed.")
    out = must(remote, ["sudo", "unattended-upgrade", "-v"], "install security updates", timeout=1800)
    summary = [line for line in out.splitlines()
               if line.startswith(("Packages that will be upgraded", "All upgrades installed",
                                   "No packages found"))]
    code, _, _ = remote.run(["test", "-f", "/var/run/reboot-required"])
    result = {"updates": summary, "reboot_required": code == 0, "rebooted": False}
    if code == 0 and reboot:
        try:
            remote.run(["sudo", "systemctl", "reboot"], timeout=30)
        except Exception:  # the SSH session usually drops as the server goes down
            pass
        result["rebooted"] = True
        result["readyz_after_reboot"] = waiter(settings, wait_seconds)
    return result


RUN_NAME = re.compile(r"^daily_sweep_[0-9]{8}_[0-9]{6}$")


def reconcile_send(remote, run, *, mark_undelivered=False, yes=False):
    """Settle a send that may or may not have reached Gmail. Never sends anything.

    Looks the message up in the mailbox's Sent folder: found -> writes the receipt; not
    found -> exit 2, or with --mark-undelivered records that a person decided it was not
    delivered. Either way later days can collect and send again.
    """
    if not yes:
        raise OperationError("reconcile-send writes the run's send records; rerun with --yes")
    if not RUN_NAME.match(run or ""):
        raise ValueError("Expected a run folder name such as daily_sweep_20261114_004500")
    argv = ["sudo", "runuser", "-u", "scraper", "--", "env", "-C", "/opt/illoura-routine",
            OPS_PYTHON, OPS_DIR + "/illoura_daily_brief.py", "reconcile",
            "--out", f"/opt/illoura-routine/reports/{run}"]
    if mark_undelivered:
        argv.append("--mark-undelivered")
    code, out, err = remote.run(argv, timeout=300)
    lines = [line for line in out.splitlines() if line.strip().startswith("{")]
    if code not in (0, 2) or not lines:
        raise OperationError(f"reconcile failed (exit {code}): {(err or out).strip()[-400:]}")
    result = json.loads(lines[-1])
    result["exit_code"] = code
    return result


# --------------------------------------------------------------------------- newsletter routine

ROUTINE_DIR = "/opt/illoura-routine"
ROUTINE_TEST_PATTERN = "test_routine_*.py"
MAIL_ENV = "/etc/illoura-routine/mail.env"
MAIL_ENV_HELPER = "deploy/routine_mail_env.py"


def routine_files(repo=REPO):
    """run_once.py and scripts/*.py from routine/. routine/legacy/ is reference only, never shipped."""
    scripts = sorted("scripts/" + p.name for p in (Path(repo) / "routine/scripts").glob("*.py"))
    return ["run_once.py", *scripts]


def routine_tests(repo=REPO):
    return sorted(p.name for p in (Path(repo) / "tests").glob(ROUTINE_TEST_PATTERN))


def set_mail_config(remote, *, yes=False, repo=REPO):
    """Create /etc/illoura-routine/mail.env from the literals in the deployed routine scripts.

    The values stay on the server: the helper reads them there and prints only key names.
    Never replaces an existing file. Needed once before `deploy-routine`.
    """
    if not yes:
        raise OperationError("set-mail-config writes a private server file; rerun with --yes")
    stage = new_stage()
    try:
        stage_files(remote, stage, {"routine_mail_env.py": lf_bytes(Path(repo) / MAIL_ENV_HELPER)})
        code, out, err = remote.run(
            ["sudo", OPS_PYTHON, f"{stage}/routine_mail_env.py", "--write", MAIL_ENV], timeout=60
        )
        lines = [line for line in out.splitlines() if line.strip().startswith("{")]
        if code not in (0, 3) or not lines:
            raise OperationError(f"set-mail-config failed (exit {code}): {(err or out).strip()[-300:]}")
        result = json.loads(lines[-1])
        result["exit_code"] = code
        return result
    finally:
        remove_stage(remote, stage)


def deploy_routine(remote, *, yes=False, repo=REPO, test_only=False):
    """Stage, verify and test routine/ on the server; install only the files that changed.

    Installed as root:scraper 0640 (the collector can read but not change its code); originals
    kept in /var/backups/illoura/routine-<time>/. Refuses while the brief or a collection runs,
    and refuses to install before mail.env exists (the repository routine reads it).
    """
    if not yes and not test_only:
        raise OperationError("deploy-routine changes the server; rerun with --yes")
    for unit in BUSY_UNITS:
        _, out, _ = remote.run(["systemctl", "is-active", unit])
        if out.strip() in ("active", "activating", "deactivating", "reloading"):
            raise OperationError(f"{unit} is running; nothing was changed. Try after it finishes.")
    names = routine_files(repo)
    files = {"routine/" + n: lf_bytes(Path(repo) / "routine" / n) for n in names}
    files.update({"tests/" + n: lf_bytes(Path(repo) / "tests" / n) for n in routine_tests(repo)})
    files[MAIL_ENV_HELPER] = lf_bytes(Path(repo) / MAIL_ENV_HELPER)
    stage = new_stage()
    try:
        stage_files(remote, stage, files)
        tests = run_tests(
            remote,
            ["env", "GSHEETS_AUTO_SYNC=False", "PYTHONDONTWRITEBYTECODE=1", OPS_PYTHON, "-m",
             "unittest", "discover", "-s", stage + "/tests", "-p", ROUTINE_TEST_PATTERN],
            "staged routine tests (nothing installed)",
        )
        if test_only:
            return {"tested": True, "installed": [], "tests": tests}
        code, _, _ = remote.run(["sudo", "-u", "scraper", "test", "-r", MAIL_ENV])
        if code:
            raise OperationError("mail.env is missing; run set-mail-config --yes first. Nothing installed.")
        targets = {n: f"{ROUTINE_DIR}/{n}" for n in names}
        _, out, _ = remote.run(["sudo", "sha256sum", *targets.values()])
        current = {line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()}
        changed = [n for n in names if current.get(targets[n]) != sha256(files["routine/" + n])]
        if not changed:
            return {"installed": [], "tests": tests, "note": "server already matches"}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = f"{BACKUP_ROOT}/routine-{stamp}"
        must(remote, ["sudo", "install", "-d", "-o", "root", "-g", "root", "-m", "0700",
                      backup, backup + "/scripts"], "create routine backup folder")
        for name in changed:
            if targets[name] in current:
                must(remote, ["sudo", "cp", "-p", targets[name], f"{backup}/{name}"], "back up " + name)
            must(remote, ["sudo", "install", "-o", "root", "-g", "scraper", "-m", "0640",
                          f"{stage}/routine/{name}", targets[name]], "install " + name)
        out = must(remote, ["sudo", "sha256sum", *[targets[n] for n in changed]], "hash installed routine")
        after = {line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()}
        wrong = [n for n in changed if after.get(targets[n]) != sha256(files["routine/" + n])]
        if wrong:
            raise OperationError("Installed routine files do not match: " + ", ".join(wrong)
                                 + f"; originals are in {backup}")
        return {"installed": changed, "backup": backup, "tests": tests}
    finally:
        remove_stage(remote, stage)


# --------------------------------------------------------------------------- CLI


def build_parser():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("status", help="Read-only server, timer, health and disk summary")
    for name in ("publish", "backup", "rollback"):
        sub.add_parser(name).add_argument("--yes", action="store_true")
    prune = sub.add_parser("prune")
    prune.add_argument("--dry-run", action="store_true")
    prune.add_argument("--yes", action="store_true")
    sub.add_parser("restore-drill").add_argument("--backup", required=True)
    ops = sub.add_parser(
        "deploy-ops", help="Ship, test and install cloud operations code"
    )
    ops.add_argument("--yes", action="store_true")
    ops.add_argument("--allow-unconfigured-health", action="store_true")
    sub.add_parser(
        "deploy-api", help="Ship, test and install api/app.py with automatic rollback"
    ).add_argument("--yes", action="store_true")
    sub.add_parser(
        "verify-ops",
        help="Stage and run the operations tests on the server; installs nothing",
    )
    sub.add_parser(
        "verify-api", help="Stage and run the API tests on the server; installs nothing"
    )
    sub.add_parser(
        "deploy-collectors",
        help="Ship, test and install run_spiders.py and spiders/*.py (changed files only)",
    ).add_argument("--yes", action="store_true")
    sub.add_parser(
        "verify-collectors",
        help="Stage and run the collector tests on the server; installs nothing",
    )
    key = sub.add_parser("api-key")
    key.add_argument("key_action", choices=["create", "revoke"])
    key.add_argument("--name", required=True)
    sender = sub.add_parser("set-health-sender", help="Write /etc/illoura-ops/health.env (private)")
    sender.add_argument("--email", required=True)
    sender.add_argument("--yes", action="store_true")
    sub.add_parser("daily-brief-dry-run", help="Compose a copy of the latest run on the server; never sends")
    watcher = sub.add_parser(
        "watch", help="External /readyz watch with email alarm (AWS, independent of the server)"
    )
    watcher.add_argument("watch_action", choices=["deploy", "status"])
    watcher.add_argument("--email", help="Alarm address (default: the verified Lightsail contact)")
    watcher.add_argument("--yes", action="store_true")
    snaps = sub.add_parser("snapshots", help="Daily Lightsail snapshots of the whole server")
    snaps.add_argument("snapshot_action", choices=["enable", "status"])
    snaps.add_argument("--yes", action="store_true")
    timer = sub.add_parser("schedule", help="Enable or disable the 08:15 Sydney daily brief")
    timer.add_argument("timer_action", choices=["enable", "disable"])
    timer.add_argument("--yes", action="store_true")
    sub.add_parser(
        "harden-os",
        help="Keep Ubuntu updates and service restarts out of the 06:15-08:15 Sydney brief window",
    ).add_argument("--yes", action="store_true")
    upd = sub.add_parser("os-update", help="Install pending security updates now (not 05:30-09:30 Sydney)")
    upd.add_argument("--reboot", action="store_true", help="Reboot if an update needs it, then wait for /readyz")
    upd.add_argument("--yes", action="store_true")
    rec = sub.add_parser("reconcile-send", help="Settle an uncertain send by checking Gmail Sent; never sends")
    rec.add_argument("--run", required=True, help="Run folder name, e.g. daily_sweep_20261114_004500")
    rec.add_argument("--mark-undelivered", action="store_true",
                     help="Not in Sent: record that it was not delivered so later days can send")
    rec.add_argument("--yes", action="store_true")
    sub.add_parser(
        "set-mail-config",
        help="Create the routine's private mail.env from the deployed scripts (values stay on the server)",
    ).add_argument("--yes", action="store_true")
    sub.add_parser(
        "deploy-routine",
        help="Ship, test and install routine/run_once.py and routine/scripts/*.py (changed files only)",
    ).add_argument("--yes", action="store_true")
    sub.add_parser(
        "verify-routine", help="Stage and run the routine tests on the server; installs nothing"
    )
    return p


def main(argv=None, remote_factory=SshRemote):
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    check_account(settings)
    if args.action in ("watch", "snapshots"):  # AWS APIs only; no SSH session needed
        if args.action == "watch":
            result = watch(settings, args.watch_action, yes=args.yes, email=args.email)
        else:
            result = snapshots(settings, args.snapshot_action, yes=args.yes)
        print(json.dumps(result, indent=2))
        return 0
    remote = remote_factory(settings)
    try:
        if args.action == "status":
            result = status(remote, settings)
        elif args.action in ("publish", "backup", "rollback", "prune", "restore-drill"):
            result = ops_action(
                remote,
                args.action,
                dry_run=getattr(args, "dry_run", False),
                backup=getattr(args, "backup", None),
                yes=getattr(args, "yes", False),
            )
        elif args.action == "deploy-ops":
            result = deploy_ops(
                remote,
                yes=args.yes,
                allow_unconfigured_health=args.allow_unconfigured_health,
            )
        elif args.action == "deploy-api":
            result = deploy_api(remote, yes=args.yes)
        elif args.action == "verify-ops":
            result = deploy_ops(remote, test_only=True)
        elif args.action == "verify-api":
            result = deploy_api(remote, test_only=True)
        elif args.action == "deploy-collectors":
            result = deploy_collectors(remote, yes=args.yes)
        elif args.action == "verify-collectors":
            result = deploy_collectors(remote, test_only=True)
        elif args.action == "set-health-sender":
            result = set_health_sender(remote, args.email, yes=args.yes)
        elif args.action == "daily-brief-dry-run":
            result = daily_brief_dry_run(remote)
        elif args.action == "schedule":
            result = schedule(remote, args.timer_action, yes=args.yes)
        elif args.action == "harden-os":
            result = harden_os(remote, yes=args.yes)
        elif args.action == "os-update":
            result = os_update(remote, settings, yes=args.yes, reboot=args.reboot)
        elif args.action == "reconcile-send":
            result = reconcile_send(remote, args.run, mark_undelivered=args.mark_undelivered,
                                    yes=args.yes)
        elif args.action == "set-mail-config":
            result = set_mail_config(remote, yes=args.yes)
        elif args.action == "deploy-routine":
            result = deploy_routine(remote, yes=args.yes)
        elif args.action == "verify-routine":
            result = deploy_routine(remote, test_only=True)
        else:
            result = api_key(remote, settings, args.key_action, args.name)
    finally:
        remote.close()
    print(json.dumps(result, indent=2))
    return result.get("exit_code", 0) if isinstance(result, dict) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OperationError, ValueError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(2)

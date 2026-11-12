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

OPS_DIR = '/opt/illoura-ops'
OPS_PYTHON = '/opt/aus_tender/.venv/bin/python'
OPS_SCRIPT = OPS_DIR + '/illoura_cloud_ops.py'
API_DIR = '/opt/illoura-api'
API_PYTHON = API_DIR + '/.venv/bin/python'
HEALTH_ENV = '/etc/illoura-ops/health.env'
HEALTH_STATUS = '/var/lib/illoura-health/status.json'
PUBLICATION = '/opt/illoura-routine/publication.json'
BACKUP_ROOT = '/var/backups/illoura'
KEYS_FILE = '/etc/illoura-api/keys.json'
TIMERS = ('illoura-cloud-collect.timer', 'illoura-cloud-health.timer', 'illoura-cert-renew.timer')

OPS_FILES = ('illoura_cloud_ops.py', 'illoura_cloud_health.py', 'illoura_cloud_collect.py')
OPS_UNITS = ('illoura-cloud-collect.service', 'illoura-cloud-collect.timer',
             'illoura-cloud-health.service', 'illoura-cloud-health.timer')
OPS_TESTS = ('test_cloud_ops.py', 'test_cloud_retention.py', 'test_cloud_health.py')

# Plain tokens only. '*' and '^' are allowed because every argument is shell-quoted
# (no globbing or expansion happens); spaces, quotes, ;, |, &, $ and ` are refused.
SAFE_ARG = re.compile(r'^[A-Za-z0-9_./=:@%+,*^-]+$')
BACKUP_PATH = re.compile(r'^/var/backups/illoura/operations-[0-9A-Za-z-]+$')
STAGE_PATH = re.compile(r'^/var/tmp/illoura-stage-[0-9TZ]+-[0-9a-f]{8}$')
KEY_NAME = re.compile(r'^[A-Za-z0-9_-]{2,50}$')


class OperationError(RuntimeError):
    """A remote step failed; the message says what was (and was not) changed."""


@dataclass
class Settings:
    profile: str = 'illoura-trial'
    region: str = 'ap-southeast-2'
    instance: str = 'illoura-hosting-trial'
    account: str = ''
    private_dir: Path = field(default_factory=lambda: Path.home() / '.illoura' / 'aws-trial')

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        base = cls()
        return cls(profile=env.get('ILLOURA_AWS_PROFILE', base.profile),
                   region=env.get('ILLOURA_AWS_REGION', base.region),
                   instance=env.get('ILLOURA_AWS_INSTANCE', base.instance),
                   account=env.get('ILLOURA_AWS_ACCOUNT', ''),
                   private_dir=Path(env.get('ILLOURA_PRIVATE_DIR', str(base.private_dir))))


def command_line(argv):
    """Quote a fixed argument list; reject anything that is not a plain token."""
    if not argv or not all(isinstance(a, str) and SAFE_ARG.match(a) for a in argv):
        raise ValueError('Refusing an unsafe remote argument list')
    return ' '.join(shlex.quote(a) for a in argv)


def lf_bytes(path):
    """Ship text with Unix line endings regardless of the Windows checkout."""
    return Path(path).read_bytes().replace(b'\r\n', b'\n')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- AWS CLI

def aws_cli():
    found = shutil.which('aws')
    if found:
        return found
    fallback = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Amazon/AWSCLIV2/aws.exe'
    if fallback.exists():
        return str(fallback)
    raise OperationError('AWS CLI not found')


def aws(settings, *args):
    result = subprocess.run([aws_cli(), *args, '--profile', settings.profile, '--region',
                             settings.region, '--output', 'json', '--no-cli-pager'],
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise OperationError('AWS CLI failed: ' + result.stderr.strip()[:300])
    return json.loads(result.stdout or '{}')


def check_account(settings):
    if settings.account:
        actual = aws(settings, 'sts', 'get-caller-identity')['Account']
        if actual != settings.account:
            raise OperationError('AWS profile points at a different account than ILLOURA_AWS_ACCOUNT')


# --------------------------------------------------------------------------- remote

class SshRemote:
    """Pinned-host SSH. Unknown host keys are accepted only if AWS vouches for them."""

    def __init__(self, settings):
        import paramiko  # deploy-only dependency (requirements-deploy.txt)
        instance = aws(settings, 'lightsail', 'get-instance', '--instance-name', settings.instance)['instance']
        host = instance['publicIpAddress']
        details = aws(settings, 'lightsail', 'get-instance-access-details', '--instance-name',
                      settings.instance, '--protocol', 'ssh')['accessDetails']
        vouched = {k['publicKey'].split()[-1] for k in details.get('hostKeys', []) if k.get('publicKey')}
        known = settings.private_dir / 'known_hosts'

        class AwsVouched(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if key.get_base64() not in vouched:
                    raise OperationError('SSH host key is neither pinned nor confirmed by AWS')
                client.get_host_keys().add(hostname, key.get_name(), key)
                client.save_host_keys(str(known))

        self.client = paramiko.SSHClient()
        if known.exists():
            self.client.load_host_keys(str(known))
        self.client.set_missing_host_key_policy(AwsVouched())
        self.client.connect(host, username='ubuntu', key_filename=str(settings.private_dir / 'admin.pem'),
                            timeout=20, allow_agent=False, look_for_keys=False)
        self.host = host

    def run(self, argv, timeout=900):
        _, out, err = self.client.exec_command(command_line(argv), timeout=timeout)
        stdout = out.read().decode('utf-8', 'replace')
        stderr = err.read().decode('utf-8', 'replace')
        return out.channel.recv_exit_status(), stdout, stderr

    def put_bytes(self, data, remote_path, mode=0o644):
        with self.client.open_sftp() as sftp:
            with sftp.open(remote_path, 'wb') as handle:
                handle.write(data)
            sftp.chmod(remote_path, mode)

    def close(self):
        self.client.close()


def must(remote, argv, what, timeout=900):
    code, out, err = remote.run(argv, timeout=timeout)
    if code:
        raise OperationError(f'{what} failed (exit {code}): {(err or out).strip()[-400:]}')
    return out


def run_tests(remote, argv, what):
    """unittest reports on stderr; return its 'Ran N tests ... OK' summary as evidence."""
    code, out, err = remote.run(argv, timeout=1800)
    text = (out + '\n' + err).strip()
    if code:
        raise OperationError(f'{what} failed (exit {code}): {text[-600:]}')
    return ' '.join(line for line in text.splitlines()[-4:] if line.strip() and set(line) != {'-'})


# --------------------------------------------------------------------------- staging

def new_stage():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return f'/var/tmp/illoura-stage-{stamp}-{secrets.token_hex(4)}'


def stage_files(remote, stage, files):
    """Upload {relative path: bytes}; verify every SHA-256 on the server."""
    if not STAGE_PATH.match(stage):
        raise ValueError('Unexpected staging path')
    folders = sorted({str(Path(rel).parent).replace('\\', '/') for rel in files} - {'.'})
    must(remote, ['install', '-d', '-m', '0755', stage, *[stage + '/' + f for f in folders]], 'create staging')
    for rel, data in files.items():
        remote.put_bytes(data, stage + '/' + rel)
    out = must(remote, ['sha256sum', *[stage + '/' + rel for rel in files]], 'hash staged files')
    remote_hashes = {line.split()[1]: line.split()[0] for line in out.splitlines() if line.strip()}
    for rel, data in files.items():
        if remote_hashes.get(stage + '/' + rel) != sha256(data):
            raise OperationError(f'Staged file {rel} does not match its local hash; nothing installed')


def remove_stage(remote, stage):
    if STAGE_PATH.match(stage):
        remote.run(['rm', '-rf', stage])


def timer_states(remote):
    states = {}
    for timer in TIMERS:
        _, out, _ = remote.run(['systemctl', 'is-enabled', timer])
        states[timer] = out.strip() or 'unknown'
    return states


def api_policy(app_source):
    """The publication policy is the API's own table allowlist."""
    for node in ast.parse(app_source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'ALLOWED' for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('ALLOWED table policy not found in api/app.py')


# --------------------------------------------------------------------------- actions

def status(remote, settings=None):
    report = {}
    if settings is not None:
        inst = aws(settings, 'lightsail', 'get-instance', '--instance-name', settings.instance)['instance']
        ports = aws(settings, 'lightsail', 'get-instance-port-states', '--instance-name', settings.instance)
        report['instance'] = {'state': inst['state']['name'], 'bundle': inst['bundleId'],
                              'static_ip': inst.get('isStaticIp')}
        report['firewall'] = [f"{p['fromPort']}/{p['protocol']} {','.join(p.get('cidrs', []))}"
                              for p in ports.get('portStates', [])]
    _, out, _ = remote.run(['systemctl', 'is-active', 'illoura-api', 'nginx'])
    report['services'] = dict(zip(['illoura-api', 'nginx'], out.split()))
    report['timers'] = timer_states(remote)
    code, out, _ = remote.run(['sudo', 'cat', HEALTH_STATUS])
    if code == 0:
        health = json.loads(out)
        report['health'] = {'status': health.get('status'), 'checked_at_utc': health.get('checked_at_utc'),
                            'checks': {k: v.get('status') for k, v in health.get('checks', {}).items()}}
    code, out, _ = remote.run(['sudo', 'cat', PUBLICATION])
    if code == 0:
        pub = json.loads(out)
        report['publication'] = {k: pub.get(k) for k in ('status', 'generation_id', 'snapshot_finished_utc',
                                                         'retention_attention')}
    code, out, _ = remote.run(['df', '-B1', '--output=avail,size', '/srv/illoura-data'])
    if code == 0 and len(out.split()) >= 4:
        avail, size = (int(x) for x in out.split()[-2:])
        report['disk'] = {'free_gb': round(avail / 1e9, 1), 'total_gb': round(size / 1e9, 1)}
    return report


MUTATING = {'publish', 'rollback', 'prune'}


def ops_action(remote, action, *, dry_run=False, backup=None, yes=False):
    if action not in {'publish', 'backup', 'restore-drill', 'rollback', 'prune'}:
        raise ValueError('Unknown operations action')
    if action in MUTATING and not dry_run and not yes:
        raise OperationError(f'{action} changes production data; rerun with --yes')
    argv = ['sudo', OPS_PYTHON, OPS_SCRIPT, action]
    if dry_run:
        if action != 'prune':
            raise ValueError('--dry-run applies to prune only')
        argv.append('--dry-run')
    if action == 'restore-drill':
        if not backup or not BACKUP_PATH.match(backup):
            raise ValueError('--backup must be /var/backups/illoura/operations-<id>')
        argv += ['--backup', backup]
    code, out, err = remote.run(argv, timeout=3600)
    # Exit 1 after publish/prune can mean "published, retention needs attention":
    # the printed JSON is the source of truth, so return it with the exit code.
    return {'exit_code': code, 'output': out.strip()[-4000:], 'stderr': err.strip()[-1000:]}


def health_sender_configured(remote):
    code, out, _ = remote.run(['sudo', 'grep', '-c', '^ILLOURA_GMAIL_SENDER=.', HEALTH_ENV])
    return code == 0 and out.strip() not in ('', '0')


def deploy_ops(remote, *, yes=False, allow_unconfigured_health=False, repo=REPO, test_only=False):
    """Stage, verify and test operations code; install unless test_only."""
    if not yes and not test_only:
        raise OperationError('deploy-ops changes the server; rerun with --yes')
    health_source = (repo / 'deploy/illoura_cloud_health.py').read_text(encoding='utf-8')
    if (not test_only and 'ILLOURA_GMAIL_SENDER' in health_source and not allow_unconfigured_health
            and not health_sender_configured(remote)):
        raise OperationError(f'{HEALTH_ENV} lacks ILLOURA_GMAIL_SENDER; create it first '
                             '(see docs/cloud_operations.md). Nothing was changed.')
    files = {'deploy/' + n: lf_bytes(repo / 'deploy' / n) for n in OPS_FILES + OPS_UNITS}
    files.update({'tests/' + n: lf_bytes(repo / 'tests' / n) for n in OPS_TESTS})
    policy = api_policy((repo / 'api/app.py').read_text(encoding='utf-8'))
    files['deploy/policy.json'] = json.dumps(policy, indent=2).encode()
    before = timer_states(remote)
    stage = new_stage()
    try:
        stage_files(remote, stage, files)
        # The tests locate ../deploy themselves; tests/ is not a package, so no -t here.
        tests = run_tests(remote, [OPS_PYTHON, '-m', 'unittest', 'discover', '-s', stage + '/tests',
                                   '-p', 'test_cloud_*.py'], 'staged operations tests (nothing installed)')
        if test_only:
            return {'tested': True, 'installed': [], 'tests': tests}
        for name in OPS_FILES + ('policy.json',):
            must(remote, ['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0644',
                          f'{stage}/deploy/{name}', f'{OPS_DIR}/{name}'], 'install ' + name)
        must(remote, ['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0644',
                      f'{stage}/deploy/illoura_cloud_ops.py', f'{OPS_DIR}/deploy/illoura_cloud_ops.py'],
             'install ops test copy')
        for unit in OPS_UNITS:
            must(remote, ['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0644',
                          f'{stage}/deploy/{unit}', f'/etc/systemd/system/{unit}'], 'install ' + unit)
        must(remote, ['sudo', 'systemctl', 'daemon-reload'], 'systemd reload')
    finally:
        remove_stage(remote, stage)
    after = timer_states(remote)
    if after != before:
        raise OperationError(f'Timer states changed unexpectedly: {before} -> {after}')
    remote.run(['sudo', 'systemctl', 'start', 'illoura-cloud-health.service'], timeout=180)
    return {'installed': list(OPS_FILES) + ['policy.json'] + list(OPS_UNITS), 'timers': after,
            'hashes': {k: sha256(v) for k, v in files.items() if k.startswith('deploy/')}}


def deploy_api(remote, *, yes=False, repo=REPO, wait_seconds=30, test_only=False):
    """Stage and test api/app.py as the API user; install with rollback unless test_only."""
    if not yes and not test_only:
        raise OperationError('deploy-api restarts the live API; rerun with --yes')
    files = {n: lf_bytes(repo / 'api' / n) for n in ('app.py', 'test_security.py', 'requirements.txt')}
    code, out, _ = remote.run(['sudo', 'cat', API_DIR + '/requirements.txt'])
    server_lines = {line.strip() for line in out.splitlines() if line.strip()}
    local_lines = {line.strip() for line in files['requirements.txt'].decode().splitlines() if line.strip()}
    if code or server_lines != local_lines:
        raise OperationError('api/requirements.txt differs from the server; dependency changes need a '
                             'reviewed manual install. Nothing was changed.')
    stage = new_stage()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = f'{BACKUP_ROOT}/api-app-{stamp}.py'
    try:
        stage_files(remote, stage, files)
        tests = run_tests(remote, ['sudo', '-u', 'illoura-api', API_PYTHON, '-m', 'unittest', 'discover', '-s',
                                   stage, '-p', 'test_security.py', '-t', stage], 'staged API tests (nothing installed)')
        if test_only:
            return {'tested': True, 'installed': [], 'tests': tests}
        must(remote, ['sudo', 'install', '-o', 'root', '-g', 'root', '-m', '0600',
                      API_DIR + '/app.py', backup], 'back up live API')
        must(remote, ['sudo', 'install', '-o', 'root', '-g', 'illoura-api', '-m', '0640',
                      stage + '/app.py', API_DIR + '/app.py'], 'install API')
    finally:
        remove_stage(remote, stage)
    must(remote, ['sudo', 'systemctl', 'restart', 'illoura-api'], 'restart API')
    deadline = time.monotonic() + wait_seconds
    while True:
        code, _, _ = remote.run(['curl', '-fsS', '--max-time', '5', 'http://127.0.0.1:8000/healthz'])
        if code == 0:
            return {'installed_sha256': sha256(files['app.py']), 'previous_backup': backup}
        if time.monotonic() >= deadline:
            remote.run(['sudo', 'install', '-o', 'root', '-g', 'illoura-api', '-m', '0640', backup,
                        API_DIR + '/app.py'])
            remote.run(['sudo', 'systemctl', 'restart', 'illoura-api'])
            raise OperationError('New API did not become healthy; previous version restored from ' + backup)
        time.sleep(2)


def api_key(remote, settings, action, name, *, host=None):
    """Create or revoke a named key. The key itself is written only to the private directory."""
    if not KEY_NAME.match(name):
        raise ValueError('Use a simple name: letters, digits, _ or -')
    keys = json.loads(must(remote, ['sudo', 'cat', KEYS_FILE], 'read key hashes'))
    existing = next((k for k in keys if k['name'] == name), None)
    result = {}
    if action == 'create':
        if existing:
            raise OperationError('Name already exists; use a new name to rotate')
        token = 'illoura_' + secrets.token_urlsafe(36)
        keys.append({'name': name, 'sha256': sha256(token.encode()), 'enabled': True})
        host = host or aws(settings, 'lightsail', 'get-instance', '--instance-name',
                           settings.instance)['instance']['publicIpAddress']
        path = settings.private_dir / f'{name}-access.json'
        path.write_text(json.dumps({'name': name, 'api_key': token, 'api_base_url': 'https://' + host,
                                    'mcp_url': 'https://' + host + '/mcp'}, indent=2), encoding='utf-8')
        result['credential_file'] = str(path)
    elif action == 'revoke':
        if not existing:
            raise OperationError('Unknown key name')
        existing['enabled'] = False
    else:
        raise ValueError('Unknown key action')
    stage = new_stage()
    try:
        must(remote, ['install', '-d', '-m', '0700', stage], 'create staging')
        remote.put_bytes(json.dumps(keys).encode(), stage + '/keys.json', mode=0o600)
        must(remote, ['sudo', 'install', '-o', 'root', '-g', 'illoura-api', '-m', '0640',
                      stage + '/keys.json', KEYS_FILE], 'install key hashes')
    finally:
        remove_stage(remote, stage)
    result['action'] = action
    result['name'] = name
    return result


# --------------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('status', help='Read-only server, timer, health and disk summary')
    for name in ('publish', 'backup', 'rollback'):
        sub.add_parser(name).add_argument('--yes', action='store_true')
    prune = sub.add_parser('prune')
    prune.add_argument('--dry-run', action='store_true')
    prune.add_argument('--yes', action='store_true')
    sub.add_parser('restore-drill').add_argument('--backup', required=True)
    ops = sub.add_parser('deploy-ops', help='Ship, test and install cloud operations code')
    ops.add_argument('--yes', action='store_true')
    ops.add_argument('--allow-unconfigured-health', action='store_true')
    sub.add_parser('deploy-api', help='Ship, test and install api/app.py with automatic rollback'
                   ).add_argument('--yes', action='store_true')
    sub.add_parser('verify-ops', help='Stage and run the operations tests on the server; installs nothing')
    sub.add_parser('verify-api', help='Stage and run the API tests on the server; installs nothing')
    key = sub.add_parser('api-key')
    key.add_argument('key_action', choices=['create', 'revoke'])
    key.add_argument('--name', required=True)
    return p


def main(argv=None, remote_factory=SshRemote):
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    check_account(settings)
    remote = remote_factory(settings)
    try:
        if args.action == 'status':
            result = status(remote, settings)
        elif args.action in ('publish', 'backup', 'rollback', 'prune', 'restore-drill'):
            result = ops_action(remote, args.action, dry_run=getattr(args, 'dry_run', False),
                                backup=getattr(args, 'backup', None), yes=getattr(args, 'yes', False))
        elif args.action == 'deploy-ops':
            result = deploy_ops(remote, yes=args.yes, allow_unconfigured_health=args.allow_unconfigured_health)
        elif args.action == 'deploy-api':
            result = deploy_api(remote, yes=args.yes)
        elif args.action == 'verify-ops':
            result = deploy_ops(remote, test_only=True)
        elif args.action == 'verify-api':
            result = deploy_api(remote, test_only=True)
        else:
            result = api_key(remote, settings, args.key_action, args.name)
    finally:
        remote.close()
    print(json.dumps(result, indent=2))
    return result.get('exit_code', 0) if isinstance(result, dict) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OperationError, ValueError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        sys.exit(2)

"""Runtime safety tests: entirely fake Docker and PostgreSQL, with private temp files."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    'metabase_runtime', Path(__file__).resolve().parents[1] / 'deploy' / 'illoura_metabase_runtime.py',
)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def credentials():
    return {key: ('fake_' + key + '_') * 4 for key in runtime.CREDENTIAL_KEYS}


def container_for(saved):
    return {
        'Config': {
            'Env': [key + '=' + value for key, value in runtime.desired_environment(saved).items()],
            'Image': runtime.IMAGE, 'User': '2000:2000', 'Entrypoint': ['java'],
            'Cmd': list(runtime.JAVA_ARGS),
        },
        'HostConfig': {
            'NetworkMode': 'host', 'CapDrop': ['ALL'],
            'SecurityOpt': ['no-new-privileges:true'], 'Memory': 1800 * 1024 * 1024,
            'NanoCpus': 1_500_000_000,
        },
        'State': {'Running': True}, 'Image': 'sha256:fake-local-image', 'Mounts': [],
    }


class FakeHost:
    def __init__(self, container=None, saved=None):
        self.container = container
        self.saved = copy.deepcopy(saved)
        self.calls = []
        self.reader_safe = True
        self.reader_unsafe_after_roles = False
        self.repo_digests = [runtime.IMAGE]
        self.fail = None
        self.fail_after_roles = False
        self.tamper_on_create = False
        self.tamper_secret_on_create = False

    def run(self, args, content=None, env=None):
        self.calls.append((list(args), content, copy.deepcopy(env)))
        if self.fail and self.fail in args:
            raise runtime.RuntimeFailure('Runtime command failed; inspect private server diagnostics.')
        if args[:3] == ['sudo', 'docker', 'container']:
            return runtime.CONTAINER + '\n' if self.container else ''
        if args[:3] == ['sudo', 'docker', 'inspect']:
            return json.dumps([self.container])
        if args[:4] == ['sudo', 'docker', 'image', 'inspect']:
            return json.dumps([{'Id': 'sha256:fake-local-image', 'RepoDigests': self.repo_digests}])
        if args[:3] == ['sudo', 'docker', 'start']:
            self.container['State']['Running'] = True
            return ''
        if args[:3] == ['sudo', 'docker', 'stop']:
            self.container['State']['Running'] = False
            return ''
        if args[:3] == ['sudo', 'docker', 'rm']:
            self.container = None
            return ''
        if args[:3] == ['sudo', 'docker', 'pull']:
            return ''
        if args[:3] == ['sudo', 'docker', 'run']:
            variables = dict(line.split('=', 1) for line in Path(
                args[args.index('--env-file') + 1],
            ).read_text().splitlines())
            saved = dict(self.saved)
            saved.update({key: variables[name] for key, name in runtime.SECRET_ENV.items()})
            self.container = container_for(saved)
            self.container['Config']['Env'] = [key + '=' + value for key, value in variables.items()]
            if self.tamper_on_create:
                self.container['HostConfig']['CapDrop'] = []
            if self.tamper_secret_on_create:
                self.container['Config']['Env'] = [entry for entry in self.container['Config']['Env']
                                                   if not entry.startswith('MB_DB_PASS=')]
                self.container['Config']['Env'].append('MB_DB_PASS=' + 'changed_' * 8)
            return 'fake-container-id'
        if args[0] == 'psql':
            key = 'app_password' if env['PGUSER'] == 'illoura_metabase_app' else 'reader_password'
            if self.saved is None or env['PGPASSWORD'] != self.saved[key]:
                raise runtime.RuntimeFailure('Runtime command failed; inspect private server diagnostics.')
            return env['PGUSER'] + '\n'
        if args[:4] == ['sudo', '-u', 'postgres', 'psql']:
            if '-A' in args:
                return 't\n' if self.reader_safe else 'f\n'
            # Model PostgreSQL mutations before a possible lost response/interruption.
            import re
            passwords = re.findall(r"ALTER ROLE (\w+) PASSWORD '([^']+)';", content)
            if passwords:
                self.saved = self.saved or {}
                for role, password in passwords:
                    key = 'app_password' if role == 'illoura_metabase_app' else 'reader_password'
                    self.saved[key] = password
            if self.fail_after_roles:
                self.fail_after_roles = False
                raise runtime.RuntimeFailure('Runtime command failed; inspect private server diagnostics.')
            if self.reader_unsafe_after_roles:
                self.reader_safe = False
            return ''
        raise AssertionError('Unexpected fake command')

    def role_mutations(self):
        return [content for args, content, _ in self.calls
                if args[:4] == ['sudo', '-u', 'postgres', 'psql'] and '-A' not in args]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.private = Path(self.temporary.name) / 'private'
        self.saved = credentials()

    def save(self, saved=None):
        self.private.mkdir(exist_ok=True)
        (self.private / 'credentials.json').write_text(json.dumps(saved or self.saved))

    def invoke(self, host, **kwargs):
        with patch.object(runtime, 'run', host.run):
            return runtime.start(self.private, **kwargs)

    def test_existing_preserves_environment_and_runtime_credentials_before_roles(self):
        host = FakeHost(container_for(self.saved), self.saved)
        host.container['Config']['Env'].append('EXTRA_SETTING=preserve-me')
        stale = {key: 'stale_' * 8 for key in runtime.CREDENTIAL_KEYS}
        stale['reader_password'] = self.saved['reader_password']
        stale['admin_password'] = self.saved['admin_password']
        self.save(stale)
        result = self.invoke(host)
        self.assertEqual(result['status'], 'existing')
        self.assertEqual(json.loads((self.private / 'credentials.json').read_text()), self.saved)
        self.assertIn('EXTRA_SETTING=preserve-me', (self.private / 'metabase.env').read_text())
        self.assertTrue(all(' PASSWORD ' not in sql for sql in host.role_mutations()))
        commands = [args for args, _, _ in host.calls]
        self.assertEqual(commands[0][2:4], ['container', 'ls'])
        self.assertFalse(any('stop' in args or 'rm' in args or 'run' in args for args in commands))
        self.assertNotIn('listener', result)

    def test_existing_second_run_reuses_credentials(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        self.invoke(host)
        first = (self.private / 'credentials.json').read_bytes()
        self.invoke(host)
        self.assertEqual(first, (self.private / 'credentials.json').read_bytes())
        self.assertEqual(host.saved, self.saved)
        self.assertTrue(all(' PASSWORD ' not in sql for sql in host.role_mutations()))

    def test_first_creation_and_rerun(self):
        host = FakeHost()
        self.assertEqual(self.invoke(host)['status'], 'started')
        saved = json.loads((self.private / 'credentials.json').read_text())
        self.assertEqual(saved['app_password'], host.saved['app_password'])
        self.assertEqual(self.invoke(host)['status'], 'existing')
        self.assertEqual(saved, json.loads((self.private / 'credentials.json').read_text()))

    def test_interruption_after_role_change_reuses_persisted_passwords(self):
        host = FakeHost()
        host.fail_after_roles = True
        with self.assertRaises(runtime.RuntimeFailure):
            self.invoke(host)
        saved = json.loads((self.private / 'credentials.json').read_text())
        self.assertIsNone(host.container)
        self.assertEqual(saved['app_password'], host.saved['app_password'])
        self.assertEqual(self.invoke(host)['status'], 'started')
        self.assertEqual(saved, json.loads((self.private / 'credentials.json').read_text()))

    def test_interruption_after_removal_retries_with_same_credentials(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.container['Config']['Env'].append('EXTRA_SETTING=preserve-on-retry')
        host.fail = 'run'
        with self.assertRaises(runtime.RuntimeFailure):
            self.invoke(host, recreate=True)
        self.assertIsNone(host.container)
        self.assertEqual(host.saved, self.saved)
        host.fail = None
        self.invoke(host)
        self.assertIn('EXTRA_SETTING=preserve-on-retry', host.container['Config']['Env'])
        self.assertEqual(json.loads((self.private / 'credentials.json').read_text()), self.saved)
        self.assertEqual(host.saved, self.saved)

    def test_existing_role_interruption_does_not_rotate_passwords(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.fail_after_roles = True
        with self.assertRaises(runtime.RuntimeFailure):
            self.invoke(host)
        self.invoke(host)
        self.assertEqual(host.saved, self.saved)
        self.assertTrue(all(' PASSWORD ' not in sql for sql in host.role_mutations()))

    def test_stopped_container_is_verified_then_started(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.container['State']['Running'] = False
        self.invoke(host)
        self.assertTrue(host.container['State']['Running'])
        self.assertIn(['sudo', 'docker', 'start', runtime.CONTAINER], [call[0] for call in host.calls])

    def test_recreate_is_explicit_and_keeps_credentials(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.container['HostConfig']['Memory'] = 0
        self.assertEqual(self.invoke(host, recreate=True)['status'], 'recreated')
        self.assertEqual(host.saved, self.saved)
        self.assertTrue(all(' PASSWORD ' not in sql for sql in host.role_mutations()))

    def test_runtime_drift_refused_before_private_writes_or_role_changes(self):
        mutations = [
            lambda c: c['Config'].update(Image='unpinned:latest'),
            lambda c: c['Config'].update(User='0:0'),
            lambda c: c['Config'].update(Entrypoint=['sh']),
            lambda c: c['Config'].update(Cmd=['unapproved']),
            lambda c: c['HostConfig'].update(NetworkMode='bridge'),
            lambda c: c['HostConfig'].update(Privileged=True),
            lambda c: c['HostConfig'].update(CapAdd=['SYS_ADMIN']),
            lambda c: c['HostConfig'].update(CapDrop=[]),
            lambda c: c['HostConfig'].update(SecurityOpt=[]),
            lambda c: c['HostConfig'].update(Memory=0),
            lambda c: c['HostConfig'].update(NanoCpus=0),
            lambda c: c['HostConfig'].update(Binds=['unsafe']),
            lambda c: c.update(Image='sha256:wrong-image'),
            lambda c: c['Config']['Env'].append('MB_JETTY_HOST=unsafe-duplicate'),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.save()
                original = (self.private / 'credentials.json').read_bytes()
                host = FakeHost(container_for(self.saved), self.saved)
                mutate(host.container)
                with self.assertRaises(runtime.RuntimeFailure):
                    self.invoke(host)
                self.assertEqual(original, (self.private / 'credentials.json').read_bytes())
                self.assertFalse((self.private / 'metabase.env').exists())
                self.assertFalse(host.role_mutations())

    def test_missing_private_credentials_refused_without_directory_creation(self):
        host = FakeHost(container_for(self.saved), self.saved)
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'restore'):
            self.invoke(host)
        self.assertFalse(self.private.exists())
        self.assertFalse(host.role_mutations())

    def test_wrong_reader_password_refused_before_mutation(self):
        wrong = dict(self.saved, reader_password='wrong_' * 8)
        self.save(wrong)
        host = FakeHost(container_for(self.saved), self.saved)
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'authenticate'):
            self.invoke(host)
        self.assertFalse(host.role_mutations())
        self.assertFalse((self.private / 'metabase.env').exists())

    def test_unsafe_reader_refused_before_mutation(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.reader_safe = False
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'read-only'):
            self.invoke(host)
        self.assertFalse(host.role_mutations())

    def test_reader_privileges_are_rechecked_after_mutation(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.reader_unsafe_after_roles = True
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'read-only'):
            self.invoke(host)
        self.assertTrue(host.role_mutations())
        self.assertEqual(host.saved, self.saved)

    def test_unverified_image_manifest_refused_before_mutation(self):
        self.save()
        host = FakeHost(container_for(self.saved), self.saved)
        host.repo_digests = []
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'image digest'):
            self.invoke(host)
        self.assertFalse(host.role_mutations())
        self.assertFalse((self.private / 'metabase.env').exists())

    def test_failed_inspection_is_not_treated_as_absent(self):
        host = FakeHost()
        host.fail = 'ls'
        with self.assertRaises(runtime.RuntimeFailure):
            self.invoke(host)
        self.assertFalse(self.private.exists())
        self.assertFalse(host.role_mutations())

    def test_post_creation_settings_are_actually_checked(self):
        host = FakeHost()
        host.tamper_on_create = True
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'Runtime settings'):
            self.invoke(host)

    def test_post_creation_credentials_are_actually_checked(self):
        host = FakeHost()
        host.tamper_secret_on_create = True
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'Actual runtime environment'):
            self.invoke(host)

    def test_inconsistent_saved_environment_refused_after_interruption(self):
        host = FakeHost()
        host.fail = 'run'
        with self.assertRaises(runtime.RuntimeFailure):
            self.invoke(host)
        environment = self.private / 'metabase.env'
        environment.write_text(environment.read_text().replace('MB_DB_USER=illoura_metabase_app',
                                                               'MB_DB_USER=unexpected'))
        host.fail = None
        host.calls.clear()
        with self.assertRaisesRegex(runtime.RuntimeFailure, 'Saved environment'):
            self.invoke(host)
        self.assertFalse(host.role_mutations())

    def test_database_and_jvm_overrides_refused_even_with_recreate(self):
        self.save()
        for entry in ['MB_DB_CONNECTION_URI=private-value', 'JAVA_TOOL_OPTIONS=private-value',
                      'MB_DB_HOST=private-value', 'MB_DB_PASS=bad-value']:
            with self.subTest(entry=entry):
                host = FakeHost(container_for(self.saved), self.saved)
                key = entry.split('=', 1)[0]
                host.container['Config']['Env'] = [value for value in host.container['Config']['Env']
                                                   if not value.startswith(key + '=')] + [entry]
                with self.assertRaises(runtime.RuntimeFailure):
                    self.invoke(host, recreate=True)
                self.assertFalse(host.role_mutations())

    def test_cli_redacts_unexpected_exception_and_outputs_clear_safe_failures(self):
        for error in [ValueError('private-value'), runtime.RuntimeFailure('Reader verification failed.')]:
            with self.subTest(error=type(error).__name__):
                output = io.StringIO()
                with patch.object(runtime, 'start', side_effect=error), contextlib.redirect_stdout(output):
                    self.assertEqual(runtime.main(['--private-directory', str(self.private)]), 1)
                payload = json.loads(output.getvalue())
                self.assertNotIn('private-value', output.getvalue())
                self.assertEqual(payload['error_code'], type(error).__name__)
                self.assertIn('message', payload)


if __name__ == '__main__':
    unittest.main()

"""Start the pinned non-root Metabase runtime on an existing Linux PostgreSQL host.

Requires Docker, PostgreSQL and the migrated market schema. Configuration changes to
an existing container require --recreate. No firewall rules or timers are created.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

IMAGE = 'metabase/metabase@sha256:21a0c25c12a9d6334c9efb3d74db9973707baaf543ae6abb43bd6d42818e01af'
CONTAINER = 'illoura-metabase-secure'
SECRET_ENV = {
    'app_password': 'MB_DB_PASS',
    'encryption_key': 'MB_ENCRYPTION_SECRET_KEY',
    'session_secret': 'MB_SESSION_SECRET_KEY',
}
CREDENTIAL_KEYS = (*SECRET_ENV, 'reader_password', 'admin_password')


class RuntimeFailure(RuntimeError):
    """Only fixed, public diagnostic messages may be passed to this exception."""


def run(args, content=None, env=None):
    result = subprocess.run(
        args, input=content, capture_output=True, text=True, timeout=900, env=env,
    )
    if result.returncode:
        raise RuntimeFailure('Runtime command failed; inspect private server diagnostics.')
    return result.stdout


def inspect_container():
    # Listing first distinguishes an absent container from a failed Docker daemon or
    # authorization check. An arbitrary inspect failure must never trigger provisioning.
    names = run(['sudo', 'docker', 'container', 'ls', '-a', '--format', '{{.Names}}'])
    if CONTAINER not in names.splitlines():
        return None
    return json.loads(run(['sudo', 'docker', 'inspect', CONTAINER]))[0]


def desired_environment(credentials):
    return dict(
        MB_DB_TYPE='postgres', MB_DB_HOST='127.0.0.1', MB_DB_PORT='5432',
        MB_DB_DBNAME='illoura_metabase', MB_DB_USER='illoura_metabase_app',
        MB_DB_PASS=credentials['app_password'],
        MB_ENCRYPTION_SECRET_KEY=credentials['encryption_key'],
        MB_SESSION_SECRET_KEY=credentials['session_secret'],
        MB_JETTY_HOST='127.0.0.1', MB_JETTY_PORT='3000',
        MB_PLUGINS_DIR='/tmp/metabase-plugins', MB_ANON_TRACKING_ENABLED='false',
        MB_CHECK_FOR_UPDATES='false', MB_AI_FEATURES_ENABLED='false',
        MB_LOAD_SAMPLE_CONTENT='false', MB_APPLICATION_DB_MAX_CONNECTION_POOL_SIZE='4',
        MB_JDBC_DATA_WAREHOUSE_MAX_CONNECTION_POOL_SIZE='3',
    )


def container_environment(container):
    variables = {}
    for entry in container['Config']['Env']:
        key, separator, value = entry.partition('=')
        if (not separator or key in variables or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                or '\n' in value or '\r' in value):
            raise RuntimeFailure('Existing container environment cannot be safely preserved.')
        variables[key] = value
    # These can override the individual database or listener settings, including via
    # JVM flags. Refuse ambiguous effective configuration instead of guessing.
    if any(key in variables for key in (
        'MB_DB_CONNECTION_URI', 'MB_JETTY_SSL', 'JAVA_TOOL_OPTIONS',
        'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS', 'JAVA_OPTS', 'MB_CONFIG_FILE_PATH',
    )):
        raise RuntimeFailure('Existing container has unsupported runtime overrides.')
    return variables


def runtime_settings_match(container, variables):
    config, host = container['Config'], container['HostConfig']
    return (
        config.get('Image') == IMAGE
        and config.get('User') == '2000:2000'
        and config.get('Entrypoint') == ['java']
        and config.get('Cmd') == JAVA_ARGS
        and host.get('NetworkMode') == 'host'
        and not host.get('Privileged', False)
        and not host.get('CapAdd')
        and 'ALL' in [cap.upper() for cap in host.get('CapDrop') or []]
        and any(option in ('no-new-privileges', 'no-new-privileges:true')
                for option in host.get('SecurityOpt') or [])
        and host.get('Memory') == 1800 * 1024 * 1024
        and host.get('NanoCpus') == 1_500_000_000
        and not host.get('PidMode')
        and not host.get('Binds')
        and not container.get('Mounts')
        and all(variables.get(key) == value for key, value in desired_environment({
            name: variables.get(env_name) for name, env_name in SECRET_ENV.items()
        }).items())
    )


def verify_container(container, variables, running=True):
    if not runtime_settings_match(container, variables):
        raise RuntimeFailure('Runtime settings differ from the approved configuration; plan --recreate.')
    image = json.loads(run(['sudo', 'docker', 'image', 'inspect', IMAGE]))[0]
    if container.get('Image') != image.get('Id') or IMAGE not in image.get('RepoDigests', []):
        raise RuntimeFailure('Runtime image digest verification failed; plan --recreate.')
    if running and not container.get('State', {}).get('Running'):
        raise RuntimeFailure('Container did not reach the running state.')


JAVA_ARGS = ['-Xms256m', '-Xmx1024m', '-Duser.timezone=Asia/Kolkata', '-jar', '/app/metabase.jar']


def verify_credentials(credentials):
    # Passwords travel only in the child environment, never command arguments/output.
    for role, database, key in (
        ('illoura_metabase_app', 'illoura_metabase', 'app_password'),
        ('illoura_market_reader', 'illoura_market', 'reader_password'),
    ):
        env = dict(os.environ)
        # Do not inherit libpq overrides (service files, options, alternate endpoints).
        for name in list(env):
            if name.startswith('PG'):
                del env[name]
        env.update(PGPASSWORD=credentials[key], PGHOST='127.0.0.1', PGPORT='5432',
                   PGUSER=role, PGDATABASE=database, PGCONNECT_TIMEOUT='10')
        try:
            result = run(['psql', '-X', '-A', '-t', '-w', '-v', 'ON_ERROR_STOP=1'],
                         'SELECT current_user;\n', env=env)
        except RuntimeFailure:
            raise RuntimeFailure('Saved runtime credentials do not authenticate; reconcile privately.') from None
        if result.strip() != role:
            raise RuntimeFailure('Runtime database identity verification failed.')


def verify_reader():
    # Check inherited/owner write access as well as the default transaction setting.
    sql = """
SELECT EXISTS (
 SELECT FROM pg_roles WHERE rolname='illoura_market_reader'
 AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
 AND rolcanlogin AND 'default_transaction_read_only=on'=ANY(rolconfig)
 AND NOT EXISTS (SELECT FROM pg_auth_members WHERE member=pg_roles.oid)
 AND NOT EXISTS (SELECT FROM pg_db_role_setting
                 WHERE setrole=pg_roles.oid AND setdatabase<>0)
)
AND NOT has_database_privilege('illoura_market_reader', current_database(), 'CREATE')
AND NOT EXISTS (
 SELECT FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname<>'information_schema'
 AND has_schema_privilege('illoura_market_reader', oid, 'CREATE')
)
AND NOT EXISTS (
 SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
 AND c.relkind IN ('r','p','v','m','f')
 AND (has_table_privilege('illoura_market_reader', c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      OR has_any_column_privilege('illoura_market_reader', c.oid, 'INSERT,UPDATE,REFERENCES'))
)
AND NOT EXISTS (
 SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname !~ '^pg_' AND c.relkind='S'
 AND has_sequence_privilege('illoura_market_reader', c.oid, 'USAGE,UPDATE')
);
"""
    if run(['sudo', '-u', 'postgres', 'psql', '-X', '-A', '-t', '-v',
            'ON_ERROR_STOP=1', '-d', 'illoura_market'], sql).strip() != 't':
        raise RuntimeFailure('Reader role read-only verification failed; reconcile privileges privately.')


def write_private(path, content):
    # Atomic replacement keeps a complete credential set across interruption/retry.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.runtime-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure_roles(credentials, existing):
    sql = ''
    for role, key in [('illoura_metabase_app', 'app_password'),
                      ('illoura_market_reader', 'reader_password')]:
        if not existing:
            sql += f"DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION; END IF; END $$;\n"
            sql += f"ALTER ROLE {role} PASSWORD '{credentials[key]}';\n"
    sql += "SELECT 'CREATE DATABASE illoura_metabase OWNER illoura_metabase_app' WHERE NOT EXISTS(SELECT FROM pg_database WHERE datname='illoura_metabase')\n\\gexec\n"
    sql += 'REVOKE ALL ON DATABASE illoura_metabase FROM PUBLIC;\n'
    sql += "ALTER ROLE illoura_market_reader SET default_transaction_read_only=on;\nALTER ROLE illoura_market_reader SET statement_timeout='30s';\n"
    sql += 'GRANT CONNECT ON DATABASE illoura_market TO illoura_market_reader;\n\\connect illoura_market\n'
    sql += 'GRANT USAGE ON SCHEMA market TO illoura_market_reader;\nGRANT SELECT ON ALL TABLES IN SCHEMA market TO illoura_market_reader;\n'
    sql += 'ALTER DEFAULT PRIVILEGES FOR ROLE illoura_market IN SCHEMA market GRANT SELECT ON TABLES TO illoura_market_reader;\n'
    run(['sudo', '-u', 'postgres', 'psql', '-X', '-v', 'ON_ERROR_STOP=1'], sql)


def start(private, recreate=False):
    # Inspect before creating files, generating credentials, or touching PostgreSQL.
    container = inspect_container()
    variables = container_environment(container) if container else None
    private = Path(private)
    credentials_path = private / 'credentials.json'
    credentials = json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
    if container:
        if any(key not in credentials for key in ('reader_password', 'admin_password')):
            raise RuntimeFailure('Existing runtime requires its saved private credentials; restore them first.')
        for key, env_name in SECRET_ENV.items():
            if not variables.get(env_name):
                raise RuntimeFailure('Existing runtime is missing required credentials; reconcile privately.')
            credentials[key] = variables[env_name]
    for key in CREDENTIAL_KEYS:
        if key not in credentials:
            credentials[key] = secrets.token_urlsafe(48)
        if not isinstance(credentials[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{24,}', credentials[key]):
            raise RuntimeFailure('Unsupported credential encoding; reconcile privately.')
    desired = desired_environment(credentials)
    if container:
        if any(variables.get(key) != desired[key] for key in (
            'MB_DB_TYPE', 'MB_DB_HOST', 'MB_DB_PORT', 'MB_DB_DBNAME', 'MB_DB_USER',
        )):
            raise RuntimeFailure('Existing database configuration is unsupported; reconcile privately.')
        if not recreate:
            verify_container(container, variables, running=False)
        verify_credentials(credentials)
        verify_reader()
        if recreate:
            variables.update(desired)
    else:
        environment = private / 'metabase.env'
        if environment.exists():
            # A replacement may have been interrupted after removing the old
            # container. Keep its preserved environment on that retry as well.
            variables = container_environment({'Config': {'Env': environment.read_text().splitlines()}})
            if any(variables.get(key) != value for key, value in desired.items()):
                raise RuntimeFailure('Saved environment and credentials disagree; reconcile privately.')
        else:
            variables = desired
    os.umask(0o077)
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_private(credentials_path, json.dumps(credentials, indent=2))
    environment = private / 'metabase.env'
    write_private(environment, '\n'.join(key + '=' + value for key, value in variables.items()) + '\n')
    # No password changes are made while any existing container holds credentials.
    configure_roles(credentials, existing=container is not None)
    verify_credentials(credentials)
    verify_reader()
    if container and not recreate:
        if not container.get('State', {}).get('Running'):
            run(['sudo', 'docker', 'start', CONTAINER])
        status = 'existing'
    else:
        # Pull before stopping the old runtime; replacement is an explicit CLI action.
        run(['sudo', 'docker', 'pull', IMAGE])
        if container:
            run(['sudo', 'docker', 'stop', CONTAINER])
            run(['sudo', 'docker', 'rm', CONTAINER])
        run(['sudo', 'docker', 'run', '-d', '--name', CONTAINER, '--restart', 'unless-stopped',
             '--network', 'host', '--user', '2000:2000', '--memory', '1800m', '--cpus', '1.5',
             '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
             '--env-file', str(environment), '--entrypoint', 'java', IMAGE, *JAVA_ARGS])
        status = 'recreated' if container else 'started'
    actual = inspect_container()
    if actual is None:
        raise RuntimeFailure('Runtime container is missing after provisioning.')
    actual_variables = container_environment(actual)
    if any(actual_variables.get(key) != value for key, value in variables.items()):
        raise RuntimeFailure('Actual runtime environment differs from the preserved configuration.')
    verify_container(actual, actual_variables)
    verify_credentials(credentials)
    verify_reader()
    return dict(status=status, container=CONTAINER, image=IMAGE, verified=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-directory', required=True)
    parser.add_argument('--recreate', action='store_true',
                        help='Explicitly replace the existing container with approved runtime settings.')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(start(args.private_directory, recreate=args.recreate)))
        return 0
    except Exception as exc:
        result = {'status': 'failed', 'error_code': type(exc).__name__}
        # Never serialize subprocess output, paths, configuration values or raw errors.
        result['message'] = str(exc) if isinstance(exc, RuntimeFailure) else 'Runtime operation failed.'
        print(json.dumps(result))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

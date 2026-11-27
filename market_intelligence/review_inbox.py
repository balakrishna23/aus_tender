"""Offline, fail-closed review inbox application. Never fetches evidence URLs."""
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from sqlalchemy import text
from api import review_support as wire
from .review import decide
from .watchlist import mark_watchlist, set_profile


def atomic_status(path, data):
    fd, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o640)
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name == 'posix':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class BoundConnection:
    """Existing mutators share the validation transaction and use savepoints."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execution_options(self, **kwargs):
        return self

    def begin(self):
        return self.connection.begin_nested()

    def execute(self, *args, **kwargs):
        return self.connection.execute(*args, **kwargs)


class BoundEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return BoundConnection(self.connection)

    def begin(self):
        from contextlib import contextmanager
        @contextmanager
        def transaction():
            with self.connection.begin_nested():
                yield self.connection
        return transaction()


def check_current(kind, data, row):
    if not row or wire.fingerprint(kind, row) != data['expected']:
        raise ValueError('Stale decision: current evidence or marking changed; refresh and review again')
    if kind == 'buyer':
        if row['review_status'] not in ('unreviewed', 'needs_recheck') or row['register_context_stale']:
            raise ValueError('Buyer is no longer reviewable; research owner must refresh proposals')
        candidates = wire.packet(kind, row)['candidate_ids'] or []
        if data['decision'] == 'approved' and data['organisation_id'] not in candidates:
            raise ValueError('Chosen body is not in the current candidate evidence')


def apply(engine, data):
    kind = data['kind']
    # Locks serialize with existing review CLI / watchlist seeding and marking.
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        lock = 'market-review:' + data['target'] if kind == 'buyer' else 'market:watchlist'
        conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': lock})
        table = wire.TABLES[kind]
        if kind != 'buyer':
            conn.execute(text('SELECT ' + wire.IDENTIFIERS[kind] + ' FROM market.' + table +
                ' WHERE ' + wire.IDENTIFIERS[kind] + '=:key FOR UPDATE'), {'key': data['target']})
        row = conn.execute(text('SELECT ' + ','.join(wire.COLUMNS[kind]) + ' FROM market.' + table +
            ' WHERE ' + wire.IDENTIFIERS[kind] + '=:key'), {'key': data['target']}).mappings().first()
        check_current(kind, data, row)
        bound = BoundEngine(conn)
        if kind == 'buyer':
            decide(bound, data['target'], data['decision'], data['organisation_id'],
                   data['reviewer'], data['evidence_url'], data['notes'])
        elif kind == 'profile':
            set_profile(bound, data['target'], data['value'], data['evidence_url'], data['reviewer'])
            # Existing profile event schema has no note; retain it on the fact and in the inbox ledger.
            conn.execute(text('UPDATE market.illoura_profile SET note=:note WHERE key=:key'),
                         {'note': data['notes'], 'key': data['target']})
        else:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'mark.csv'
                with path.open('w', newline='', encoding='utf-8') as stream:
                    writer = csv.DictWriter(stream, fieldnames=['son_id', 'marked_relevant', 'illoura_status', 'notes'])
                    writer.writeheader()
                    writer.writerow({'son_id': data['target'], 'marked_relevant':
                        '' if data['relevant'] is None else 'yes' if data['relevant'] else 'no',
                        'illoura_status': data['illoura_status'], 'notes': data['notes']})
                mark_watchlist(bound, path, data['reviewer'])


def import_inbox(engine, inbox, ledger):
    inbox, ledger = Path(inbox), Path(ledger)
    if inbox.is_symlink() or ledger.is_symlink():
        raise ValueError('Inbox and ledger must be real directories')
    ledger.mkdir(parents=True, mode=0o750, exist_ok=True)
    totals = {'applied': 0, 'rejected': 0, 'already_processed': 0}
    # The root wrapper takes the shared research lock; the DB advisory lock also
    # prevents two direct CLI importers processing the same ledger simultaneously.
    with engine.connect() as guard:
        guard.execute(text('SELECT pg_advisory_lock(191127002)'))
        try:
            for path in sorted(inbox.glob('*.jsonl')):
                if path.is_symlink() or not path.is_file() or path.stat().st_size > wire.BODY_LIMIT + 1024:
                    raise ValueError('Unsafe inbox file')
                data = json.loads(path.read_text(encoding='utf-8'))
                identifier = data.get('id')
                if not isinstance(identifier, str) or not re.fullmatch('[a-f0-9]{32}', identifier):
                    raise ValueError('Invalid decision identity')
                destination = ledger / (identifier + '.json')
                if destination.is_symlink():
                    raise ValueError('Unsafe ledger file')
                if destination.exists():
                    previous = json.loads(destination.read_text(encoding='utf-8'))
                    if previous['status'] == 'applying':
                        previous.update(status='rejected', reason='Interrupted application: operator must reconcile database audit before resubmission',
                                        processed_at=datetime.now(timezone.utc).isoformat())
                        atomic_status(destination, previous)
                        totals['rejected'] += 1
                    else:
                        totals['already_processed'] += 1
                    continue
                wire.clean(data.get('reviewer'), 100)
                stamp = datetime.fromisoformat(data['submitted_at'])
                if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                    raise ValueError('UTC receipt time required')
                record = dict(data, status='applying')
                # A durable intent precedes database writes. After a crash it is
                # never replayed automatically, including a commit/status-file gap.
                atomic_status(destination, record)
                try:
                    wire.validate({k: v for k, v in data.items() if k not in ('id', 'reviewer', 'submitted_at')})
                    apply(engine, data)
                    record.update(status='applied')
                except ValueError as exc:
                    record.update(status='rejected', reason=str(exc))
                except Exception:
                    # Avoid leaking private database errors. A transport failure
                    # during commit may be uncertain and must never be retried.
                    record.update(status='rejected', reason='Application failed or commit uncertain; operator must inspect database audit before resubmission')
                record['processed_at'] = datetime.now(timezone.utc).isoformat()
                atomic_status(destination, record)
                totals[record['status']] += 1
        finally:
            guard.execute(text('SELECT pg_advisory_unlock(191127002)'))
    return totals


def main(argv=None):
    """Private wrapper entry point: loads the DB URL from a file, never argv."""
    import argparse
    from .db import engine
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', required=True)
    parser.add_argument('--inbox', required=True)
    parser.add_argument('--ledger', required=True)
    args = parser.parse_args(argv)
    db = None
    try:
        found = False
        for line in Path(args.environment).read_text(encoding='utf-8').splitlines():
            if line.startswith('ILLOURA_MARKET_DATABASE_URL='):
                value = line.split('=', 1)[1].strip().strip('\"').strip("'")
                if not value:
                    raise ValueError('Private database environment is empty')
                os.environ['ILLOURA_MARKET_DATABASE_URL'] = value
                found = True
        if not found:
            raise ValueError('Private database environment is missing')
        db = engine()
        print(json.dumps(import_inbox(db, args.inbox, args.ledger)))
        return 0
    except Exception as exc:
        # PostgreSQL connection exceptions can contain private hosts or secrets.
        print(json.dumps({'status': 'failed', 'error_code': type(exc).__name__}))
        return 1
    finally:
        if db is not None:
            db.dispose()


if __name__ == '__main__':
    raise SystemExit(main())

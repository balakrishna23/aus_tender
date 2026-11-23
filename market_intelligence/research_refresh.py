"""Isolated, resumable publisher refresh. No mail, send state or model calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid
import zipfile

from sqlalchemy import text
from . import freshness
from .acquire_common import AcquisitionError
from .ingest import file_hash


def event(db, run, source, phase, details):
    with db.begin() as conn:
        conn.execute(text('''INSERT INTO market.research_refresh_attempts(run_id,source,phase,details)
            VALUES(:run,:source,:phase,CAST(:details AS jsonb))'''),
            dict(run=run, source=source, phase=phase, details=json.dumps(details, default=str)))


def save_state(db, source, status, details, now, success=False):
    if status not in freshness.STATUSES:
        raise ValueError('Unknown freshness state')
    spec = freshness.SOURCES[source]
    details = dict(details, automation=spec.automation, check_interval_days=spec.cadence_days)
    with db.begin() as conn:
        conn.execute(text('''INSERT INTO market.research_freshness(source,checked_at,last_success_at,status,details)
            VALUES(:source,:now,:success,:status,CAST(:details AS jsonb)) ON CONFLICT(source) DO UPDATE SET
            checked_at=EXCLUDED.checked_at,last_success_at=COALESCE(EXCLUDED.last_success_at,market.research_freshness.last_success_at),
            status=EXCLUDED.status,details=EXCLUDED.details'''),
            dict(source=source, now=now, success=now if success else None, status=status, details=json.dumps(details, default=str)))


def validate_inbox_file(path, source):
    from .registers import SUFFIX
    suffix = '.xml' if source == 'government_directory' else SUFFIX.get(source, '.csv')
    if path.is_symlink() or not path.is_file() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 ._()\-]{0,180}', path.name):
        raise ValueError('Invalid inbox filename or type')
    if path.suffix.lower() != suffix or not 0 < path.stat().st_size <= 1_000_000_000:
        raise ValueError('Invalid inbox extension or size')
    if suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 20_000 or sum(e.file_size for e in entries) > 8_000_000_000:
                raise ValueError('Inbox ZIP expansion exceeds bounds')
            for entry in entries:
                if entry.flag_bits & 1 or entry.filename.startswith(('/', '\\')) or '..' in Path(entry.filename.replace('\\', '/')).parts:
                    raise ValueError('Unsafe inbox ZIP member')
            if archive.testzip() is not None:
                raise ValueError('Inbox ZIP checksum failed')
    else:
        with path.open('rb') as stream:
            prefix = stream.read(4096).lstrip().lower()
        if b'\x00' in prefix or b'<html' in prefix or b'<!doctype' in prefix:
            raise ValueError('Inbox file is not an approved text export')
        if (suffix == '.csv' and b',' not in prefix) or (suffix == '.xml' and b'<response' not in prefix):
            raise ValueError('Inbox file does not match parser transport')


def inbox_status(root):
    rows = []
    for source in freshness.MANUAL:
        folder = Path(root) / 'inbox' / source
        files = list(folder.iterdir()) if folder.is_dir() else []
        rows.append(dict(source=source, files=sum(p.is_file() for p in files),
                         ready=(folder / 'READY').is_file(), manual_only=True))
    return dict(sources=rows, note='READY is required for ABN multipart releases; no files are deleted')


def manual_acquire(db, source, root, output, now):
    """First observation is the receipt time; bytes are copied, never moved/deleted."""
    folder = Path(root) / 'inbox' / source
    if not folder.exists():
        return None
    if (Path(root) / 'inbox').is_symlink() or folder.is_symlink() or not folder.resolve().is_relative_to((Path(root) / 'inbox').resolve()):
        raise ValueError('Inbox directory escaped private root')
    files = sorted(p for p in folder.iterdir() if p.name != 'READY')
    if not files:
        return None
    if len(files) > 100:
        raise ValueError('Inbox file count exceeds bounds')
    for path in files:
        validate_inbox_file(path, source)
    if sum(p.stat().st_size for p in files) > 4_000_000_000:
        raise ValueError('Inbox release exceeds size bounds')
    with db.connect() as conn:
        previous = list(conn.execute(text("SELECT details FROM market.research_refresh_attempts WHERE source=:source AND phase='finished'"),
                                     {'source': source}).scalars())
    processed = {digest for result in previous if result.get('status') == 'latest_available' for digest in result.get('processed_hashes', [])}
    fresh = [p for p in files if file_hash(p) not in processed]
    if not fresh:
        return None
    if source == 'abn':
        marker = folder / 'READY'
        if not marker.is_file() or marker.is_symlink():
            raise AcquisitionError('Manual multipart release needs READY marker')
        # READY lists the exact names and hashes of the complete human download.
        expected = json.loads(marker.read_text(encoding='utf-8'))
        actual = {p.name: file_hash(p) for p in fresh}
        if expected != actual:
            raise ValueError('READY does not describe the complete ABN release')
    elif len(fresh) != 1:
        raise ValueError('Submit one unprocessed release per manual source')
    catalogue = json.loads((Path(__file__).resolve().parents[1] / 'sources.yaml').read_text(encoding='utf-8'))['sources'][source]
    run = Path(output) / uuid.uuid4().hex
    run.mkdir(parents=True, mode=0o700)
    entries = []
    for path in fresh:
        digest = file_hash(path)
        # Retain an append-only receipt before import, including across crashes.
        with db.connect() as conn:
            received = conn.execute(text("""SELECT observed_at FROM market.research_refresh_attempts
                WHERE source=:source AND phase='received' AND details->>'sha256'=:sha ORDER BY id LIMIT 1"""),
                dict(source=source, sha=digest)).scalar()
        if received is None:
            event(db, uuid.uuid4(), source, 'received', dict(sha256=digest))
            received = now
        target = run / path.name
        shutil.copyfile(path, target)
        os.chmod(target, 0o600)
        if file_hash(target) != digest or file_hash(path) != digest:
            raise ValueError('Inbox file changed during receipt')
        licence = catalogue['licence']
        entries.append(dict(dataset=source, status='complete', path=str(target.resolve()), name=path.name,
            sha256=digest, bytes=target.stat().st_size, retrieved_at=received.isoformat(), source_url=catalogue['source_url'],
            licence=licence, licence_url='https://creativecommons.org/licenses/by/' + ('3.0/au/' if '3.0' in licence else '4.0/')))
    manifest = run / 'manifest.json'
    manifest.write_text(json.dumps(dict(status='complete', datasets=[dict(key=source, source_url=catalogue['source_url'])], files=entries)), encoding='utf-8')
    return manifest


def acquire(db, source, root, output, now):
    adapter = freshness.SOURCES[source].adapter
    if adapter == 'inbox':
        return manual_acquire(db, source, root, output, now)
    if adapter == 'jsa':
        from .acquire_jsa import acquire as fetch
        return fetch(output, [source])
    if adapter == 'aps':
        from .acquire_aps import acquire as fetch
        return fetch(output, year=None)
    if adapter == 'health':
        from .acquire_health import acquire as fetch
        return fetch(output)
    if adapter == 'wa':
        from .acquire_wa import acquire as fetch
        return fetch(output)
    if adapter == 'rap':
        reviewers = [s.strip() for s in os.environ.get('ILLOURA_RAP_DIRECTORY_REVIEWER_IDS', '').split(',') if s.strip()]
        if len(reviewers) != 1:
            raise AcquisitionError('RAP directory reviewer configuration required')
        from .acquire_rap import acquire as fetch
        from .rap_review import review, verify
        run = Path(output) / uuid.uuid4().hex
        collection = run / 'collection'
        fetch(collection)
        bundle = run / 'research.json'
        result = review(db, collection, bundle, reviewers[0])
        if result['status'] != 'reviewed':
            raise AcquisitionError('RAP directory review blocked promotion')
        verify(db, collection, bundle)
        manifest = run / 'manifest.json'
        manifest.write_text(json.dumps(dict(status='complete', bundle=str(bundle), collection=str(collection),
            bundle_sha256=file_hash(bundle), bundle_bytes=bundle.stat().st_size,
            files=[dict(path=str(bundle), sha256=file_hash(bundle), bytes=bundle.stat().st_size)])), encoding='utf-8')
        return manifest
    raise ValueError('Unsupported automatic adapter')


def promote(db, source, manifest):
    if source in freshness.MANUAL:
        if source in ('agor', 'government_directory'):
            from .ingest import import_snapshot
            return import_snapshot(db, manifest, source)
        from .registers import import_register
        return import_register(db, manifest, source)
    if source.startswith('jsa_'):
        from .demand import import_demand
        return import_demand(db, manifest, source)
    if source == 'aps_employment':
        from .aps import import_aps
        return import_aps(db, manifest)
    if source == 'aged_care_star_ratings':
        from .aged_care import import_aged_care
        return import_aged_care(db, manifest)
    from .research import import_research
    data = json.loads(Path(manifest).read_text(encoding='utf-8'))
    return import_research(db, data['bundle'], data.get('collection'))


def inspect_manifest(manifest, raw_root):
    path = Path(manifest).resolve(strict=True)
    if not path.is_relative_to(Path(raw_root).resolve()):
        raise ValueError('Recovery manifest escaped raw root')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('status') != 'complete' or not data.get('files'):
        raise ValueError('Incomplete acquisition')
    for entry in data['files']:
        resource = Path(entry['path'])
        resource = (path.parent / resource).resolve(strict=True) if not resource.is_absolute() else resource.resolve(strict=True)
        if not resource.is_relative_to(path.parent) or resource.stat().st_size != entry['bytes'] or file_hash(resource) != entry['sha256']:
            raise ValueError('Retained acquisition checksum or path invalid')
    if data.get('bundle'):
        bundle = Path(data['bundle']).resolve(strict=True)
        if not bundle.is_relative_to(path.parent):
            raise ValueError('Research bundle escaped raw run')
        if bundle.stat().st_size != data['bundle_bytes'] or file_hash(bundle) != data['bundle_sha256']:
            raise ValueError('Retained research bundle checksum invalid')
    if data.get('collection') and not Path(data['collection']).resolve(strict=True).is_relative_to(path.parent):
        raise ValueError('Retained RAP collection escaped raw run')
    return data


def resume_manifest(db, source):
    with db.connect() as conn:
        value = conn.execute(text("""SELECT a.details->>'manifest' AS manifest,a.run_id FROM market.research_refresh_attempts a
            WHERE a.source=:source AND a.phase='acquired' AND NOT EXISTS
            (SELECT 1 FROM market.research_refresh_attempts f WHERE f.run_id=a.run_id AND f.source=a.source AND f.phase='finished')
            ORDER BY a.id DESC LIMIT 1"""), {'source': source}).mappings().first()
    return dict(value) if value else None


def refresh(db, root, sources=None, now=None, force=False):
    now = now or datetime.now(timezone.utc)
    root = Path(root).resolve()
    raw = root / 'raw' / 'research-refresh'
    raw.mkdir(parents=True, mode=0o700, exist_ok=True)
    selected = list(sources) if sources is not None else list(freshness.SOURCES)
    if any(k not in freshness.SOURCES for k in selected):
        raise ValueError('Unknown refresh source')
    outcomes = []
    # Session lock also protects direct CLI invocations. Root wrapper holds flock
    # for coordination with the separate morning exporter.
    with db.connect() as lock:
        held = lock.execute(text('SELECT pg_try_advisory_lock(191126023)')).scalar()
        lock.commit()
        if not held:
            return dict(status='busy', sources=[])
        try:
            for source in selected:
                spec = freshness.SOURCES[source]
                (raw / source).mkdir(mode=0o700, exist_ok=True)
                if (raw / source).is_symlink() or not (raw / source).resolve().is_relative_to(raw):
                    raise ValueError('Raw source directory escaped acquisition root')
                with db.connect() as conn:
                    state = conn.execute(text('SELECT * FROM market.research_freshness WHERE source=:source'), {'source': source}).mappings().first()
                    current = freshness.imported(conn, source)
                if state is None:
                    state = freshness.manual_baseline(source, current)
                    if state:
                        save_state(db, source, state['status'], state['details'], state['last_success_at'], success=True)
                recovery = resume_manifest(db, source)
                if not force and not recovery and spec.automation != 'manual' and not freshness.due(source, state, now):
                    continue
                run = uuid.uuid4()
                event(db, run, source, 'started', {})
                details = dict(imported_edition=str(current['edition']) if current else None)
                did_import = False
                try:
                    if spec.adapter == 'monitor':
                        from datetime import timedelta
                        status = 'latest_available' if current and now - current['edition'] <= timedelta(days=2) else 'check_overdue'
                        details = dict(reason='refreshed_by_morning_helper', imported_edition=str(current['edition']) if current else None)
                    else:
                        manifest = recovery['manifest'] if recovery else acquire(db, source, root, raw / source, now)
                        if manifest is None:
                            status = 'manual_refresh_needed' if freshness.due(source, state, now) else state['status']
                            details = dict(state['details']) if state else {}
                            details['reason'] = 'person_download_required' if status == 'manual_refresh_needed' else 'no_new_inbox_files'
                        else:
                            data = inspect_manifest(manifest, raw)
                            event(db, run, source, 'acquired', dict(manifest=str(manifest)))
                            hashes = [f['sha256'] for f in data['files']]
                            details = dict(sha256=hashlib.sha256('\n'.join(hashes).encode()).hexdigest(),
                                imported_edition=str(current['edition']) if current else None,
                                available_edition=data.get('reference_period') or next((f.get('reference_period') or f.get('reference_month') or f.get('reference_date') for f in data['files']), None))
                            save_state(db, source, 'new_edition_pending', details, now)
                            # Repeated downloads retain a new raw run but do not
                            # create research snapshots merely because observed_at changed.
                            same = (not force and state and state['status'] == 'latest_available'
                                    and state['details'].get('sha256') == details['sha256']
                                    and current and str(current['id']) == state['details'].get('snapshot_id'))
                            result = dict(status='unchanged', snapshot_id=str(current['id'])) if same else promote(db, source, manifest)
                            if result.get('status') not in ('complete', 'unchanged', 'imported'):
                                raise ValueError('Importer did not accept acquisition')
                            with db.connect() as conn:
                                promoted = freshness.imported(conn, source)
                            if not promoted or str(promoted['id']) != str(result['snapshot_id']):
                                raise AcquisitionError('Current snapshot differs from acquired edition')
                            did_import = True
                            details.update(imported_edition=str(promoted['edition']), snapshot_id=str(promoted['id']),
                                           processed_hashes=hashes if source in freshness.MANUAL else [])
                            details['available_edition'] = details.get('available_edition') or str(promoted['edition'])
                            status = 'latest_available'
                    save_state(db, source, status, details, now,
                               success=status == 'latest_available' and (spec.automation != 'manual' or did_import))
                except AcquisitionError as exc:
                    reason = str(exc).lower()
                    code = 'robots' if 'robots' in reason else 'waf' if 'waf' in reason else 'publisher_review_required'
                    status, details = 'blocked', dict(details, reason=code)
                    save_state(db, source, status, details, now)
                except Exception as exc:
                    status, details = 'failed', dict(details, reason=type(exc).__name__)
                    save_state(db, source, status, details, now)
                event(db, run, source, 'finished', dict(details, status=status))
                if recovery:
                    event(db, recovery['run_id'], source, 'finished', dict(details, status=status, recovered_by=str(run)))
                outcomes.append(dict(source=source, status=status))
            summary = freshness.report(db, now)
            output = root / 'reports' / 'research-refresh' / str(uuid.uuid4())
            output.mkdir(parents=True, mode=0o700)
            (output / 'freshness.json').write_text(json.dumps(summary, default=str, indent=2), encoding='utf-8')
            return dict(status='complete', sources=outcomes, freshness=summary)
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(191126023)'))
            lock.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--source', choices=sorted(freshness.SOURCES), action='append')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    db = None
    try:
        for line in args.environment.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                if key in ('ILLOURA_MARKET_DATABASE_URL', 'ILLOURA_RAP_DIRECTORY_REVIEWER_IDS'):
                    os.environ[key] = value
        from .db import engine
        db = engine()
        result = refresh(db, args.data_root, args.source, force=args.force)
        print(json.dumps(result, default=str))
        return 0 if result['status'] == 'complete' else 1
    except Exception as exc:
        print(json.dumps(dict(status='failed', error_code=type(exc).__name__)))
        return 1
    finally:
        if db is not None:
            db.dispose()


if __name__ == '__main__':
    raise SystemExit(main())

"""Manifest-verified, atomic register imports retaining last successful snapshots."""
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
import re
import uuid

from sqlalchemy import text

from .parsers import SourceError, name_key, parse_agor, parse_directory

PARSERS = {'agor': parse_agor, 'government_directory': parse_directory}
PARSER_REVISION = '3'


def verified_resource(manifest, source):
    manifest = Path(manifest).resolve(strict=True)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('status') != 'complete':
        raise SourceError('Acquisition manifest is not complete')
    entries = [f for f in data['files'] if f['dataset'] == source and f.get('status') == 'complete']
    if len(entries) != 1:
        raise SourceError('Expected one complete source resource')
    item = entries[0]
    path = Path(item['path']).resolve(strict=True)
    if not path.is_relative_to(manifest.parent):
        raise SourceError('Resource is outside acquisition directory')
    if item.get('licence') not in ['Creative Commons Attribution 3.0 Australia',
                                   'Creative Commons Attribution 4.0 International']:
        raise SourceError('Resource licence is outside approved scope')
    digest = file_hash(path)
    if path.stat().st_size != item['bytes'] or digest != item['sha256']:
        raise SourceError('Resource checksum/size mismatch')
    retrieved = datetime.fromisoformat(item['retrieved_at'])
    if retrieved.tzinfo is None:
        raise SourceError('Retrieval timestamp must include timezone')
    if source == 'agor':
        match = re.search(r'\b(20\d\d-\d\d-\d\d)\b', item['name'])
        if not match:
            raise SourceError('AGOR snapshot effective date missing')
        effective, basis = date.fromisoformat(match[1]), 'publisher_snapshot_date'
    else:
        effective, basis = retrieved.date(), 'retrieval_date; source is a rolling export'
    dataset = next(d for d in data['datasets'] if d['key'] == source)
    return path, dict(source=source, sha256=digest, retrieved_at=retrieved,
                      effective_date=effective, effective_date_basis=basis,
                      source_url=dataset['source_url'], licence=item['licence'], licence_url=item['licence_url'])


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def import_snapshot(engine, manifest, source):
    if source not in PARSERS:
        raise ValueError('Unsupported source')
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,:source,'running')"),
                     {'id': run_id, 'source': source})
    try:
        path, metadata = verified_resource(manifest, source)
        metadata['parser_revision'] = PARSER_REVISION
        records, metrics = PARSERS[source](path)
        if file_hash(path) != metadata['sha256']:
            raise SourceError('Resource changed while parsing')
        metrics.update(accepted_rows=len(records), missing_abn=sum(r.abn is None for r in records),
                       invalid_abn=sum('invalid_abn' in r.issues for r in records),
                       placeholder_rows=sum('publisher_placeholder' in r.issues for r in records))
        with engine.begin() as conn:
            conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:source))'), {'source':'market:'+source})
            existing = conn.execute(text('SELECT id FROM market.snapshots WHERE source=:source AND sha256=:sha256 AND parser_revision=:parser_revision'), metadata).scalar()
            if existing:
                status, snapshot_id = 'unchanged', existing
            else:
                status, snapshot_id = 'complete', uuid.uuid4()
                conn.execute(text('''INSERT INTO market.snapshots(id,source,sha256,retrieved_at,effective_date,
                    effective_date_basis,source_url,licence,licence_url,row_count,parser_revision)
                    VALUES(:id,:source,:sha256,:retrieved_at,:effective_date,:effective_date_basis,
                    :source_url,:licence,:licence_url,:row_count,:parser_revision)'''), dict(metadata,id=snapshot_id,row_count=len(records)))
                conn.execute(text('''INSERT INTO market.observations(snapshot_id,publisher_id,name,name_key,kind,abn,payload,issues)
                    VALUES(:snapshot_id,:publisher_id,:name,:name_key,:kind,:abn,CAST(:payload AS jsonb),CAST(:issues AS jsonb))'''),
                    [dict(snapshot_id=snapshot_id,publisher_id=r.publisher_id,name=r.name,name_key=name_key(r.name),
                          kind=r.kind,abn=r.abn,payload=json.dumps(r.payload),issues=json.dumps(r.issues)) for r in records])
                # Never roll the current pointer backwards when an older snapshot is imported.
                conn.execute(text('''INSERT INTO market.current_snapshots(source,snapshot_id) VALUES(:source,:id)
                    ON CONFLICT(source) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
                    WHERE (SELECT (effective_date,retrieved_at,imported_at) FROM market.snapshots WHERE id=EXCLUDED.snapshot_id)
                       > (SELECT (effective_date,retrieved_at,imported_at) FROM market.snapshots WHERE id=market.current_snapshots.snapshot_id)'''),
                    {'source':source,'id':snapshot_id})
            conn.execute(text('''UPDATE market.pipeline_runs SET finished_at=now(),status=:status,
                snapshot_id=:snapshot,metrics=CAST(:metrics AS jsonb) WHERE id=:id'''),
                dict(status=status,snapshot=snapshot_id,metrics=json.dumps(metrics),id=run_id))
        return dict(run_id=str(run_id),snapshot_id=str(snapshot_id),status=status,metrics=metrics)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.pipeline_runs SET finished_at=now(),status='failed',error_code=:error WHERE id=:id"),
                         {'id':run_id,'error':type(exc).__name__})
        raise

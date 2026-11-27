"""National business registers: every active ABN, ASIC company and business name (spec Phase 1).

The procurement-scoped import (registers.import_register) keeps only ABNs that appear in contracts, so
partners, look-alikes and RAP holders that never sold to government cannot be matched. This national mode
keeps every ACTIVE record of the ABN bulk extract, the ASIC company register and ASIC business names, plus
every procurement ABN whatever its status.

Privacy rules are those of registers.py: individuals keep no names (their trading names are never stored);
business names are kept only for holders the current ABN register shows as non-individual; no addresses or
contact fields. Records stream through PostgreSQL COPY into one temporary staging table, so memory stays
bounded whatever the file size; duplicates are resolved in SQL; the snapshot and its current pointer change
in one transaction, and minimum-row guards refuse to promote a truncated or misparsed file.
"""
import codecs
import csv
import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime

from defusedxml.ElementTree import iterparse
from sqlalchemy import text

from .ingest import file_hash
from .parsers import SourceError, legal_key, name_key
from .registers import (ASIC_COLUMNS, BUSINESS_NAME_COLUMNS, PARSER_REVISION, _abn, _abn_record, _clean, _dmy,
                        _effective, _require, procurement_scope, verified_resources)

NATIONAL_REVISION = PARSER_REVISION + '-national'
NATIONAL_SOURCES = ('abn', 'asic_companies', 'asic_business_names')
ABN_ACTIVE = 'ACT'
# Still-registered ASIC companies: registered, strike-off action in progress, external administration.
ASIC_ACTIVE = ('REGD', 'SOFF', 'EXAD')
BN_ACTIVE = 'Registered'
# A national file that yields fewer rows than this is truncated or misparsed; never promote it.
MIN_ROWS = {'abn': 5_000_000, 'asic_companies': 1_000_000, 'asic_business_names': 300_000}

STAGE_COLUMNS = ('kind', 'record_id', 'abn', 'acn', 'name', 'name_key', 'legal_key', 'is_individual', 'status',
                 'status_from', 'registered_on', 'deregistered_on', 'entity_type', 'state', 'postcode', 'attributes',
                 'issues', 'updated', 'is_current', 'company_type', 'company_class', 'name_type')
STAGE = '''CREATE TEMP TABLE register_stage (
 kind text NOT NULL, record_id text NOT NULL, abn text, acn text, name text, name_key text, legal_key text,
 is_individual boolean, status text, status_from date, registered_on date, deregistered_on date,
 entity_type text, state text, postcode text, attributes jsonb, issues jsonb, updated text,
 is_current boolean, company_type text, company_class text, name_type text) ON COMMIT DROP'''
ENTITY_COLUMNS = ('snapshot_id,record_id,abn,acn,icn,name,name_key,legal_key,is_individual,status,status_from,'
                  'registered_on,deregistered_on,entity_type,state,postcode,attributes,issues')


def _row(**values):
    name = values.get('name')
    if name and 'name_key' not in values:
        values.update(name_key=name_key(name), legal_key=legal_key(name))
    return tuple(values.get(c) for c in STAGE_COLUMNS)


# ---------------------------------------------------------------- streaming producers

def abn_rows(paths, scope, metrics, extract_times):
    """Active ABR records plus procurement ABNs of any status; other names of organisations only."""
    def keep(abn, status):
        active = status == ABN_ACTIVE
        metrics['active_read'] += active
        return active or abn in scope
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            members = sorted(n for n in archive.namelist() if n.lower().endswith('.xml'))
            if not members:
                raise SourceError('ABN archive contains no XML members')
            for member in members:
                metrics['members'] += 1
                with archive.open(member) as stream:
                    root = None
                    for event, elem in iterparse(stream, events=('start', 'end'), forbid_dtd=True):
                        if event == 'start':
                            if root is None:
                                root = elem
                                if root.tag != 'Transfer':
                                    raise SourceError('Unexpected ABN root element')
                            continue
                        if elem.tag == 'ExtractTime':
                            extract_times.append(datetime.fromisoformat(elem.text.strip()).date())
                        elif elem.tag == 'ABR':
                            metrics['records_read'] += 1
                            entity = _abn_record(elem, None, metrics, keep)
                            if entity:
                                metrics['inactive_procurement_kept'] += entity.status != ABN_ACTIVE
                                yield _row(kind='e', record_id=entity.record_id, abn=entity.abn, acn=entity.acn,
                                           name=entity.name, is_individual=entity.is_individual, status=entity.status,
                                           status_from=entity.status_from, entity_type=entity.entity_type,
                                           state=entity.state, postcode=entity.postcode,
                                           attributes=json.dumps(entity.attributes, default=str),
                                           issues=json.dumps(entity.issues),
                                           updated=entity.attributes.get('record_last_updated') or '')
                                if not entity.is_individual:
                                    for name_type, other in sorted(entity.other_names):
                                        yield _row(kind='n', record_id=entity.record_id, name=other, name_type=name_type)
                            root.clear()


def asic_rows(reader, metrics):
    """Every ASIC company row (current and former names); activity and scope are applied in SQL."""
    for row in reader:
        metrics['rows_read'] += 1
        acn = (row['ACN'] or '').strip()
        if not acn.isdigit() or len(acn) != 9:
            raise SourceError('ASIC row with invalid ACN')
        issues = []
        abn = _abn(row['ABN'], issues)
        metrics['invalid_abn'] += 'invalid_abn' in issues
        yield _row(kind='a', record_id=acn, abn=abn, acn=acn, name=_clean(row['Company Name']),
                   status=_clean(row['Status']), registered_on=_dmy(row['Date of Registration'], issues),
                   deregistered_on=_dmy(row['Date of Deregistration'], issues),
                   is_current=(row['Current Name Indicator'] or '').strip().upper() == 'Y',
                   company_type=_clean(row['Type']), company_class=_clean(row['Class']), issues=json.dumps(issues))


def business_name_rows(reader, metrics):
    """Every business name row with an ABN; holder type, activity and scope are applied in SQL."""
    for row in reader:
        metrics['rows_read'] += 1
        issues = []
        abn = _abn(row['BN_ABN'], issues)
        name = _clean(row['BN_NAME'])
        if not abn or not name:
            metrics['skipped_without_abn_or_name'] += 1
            continue
        registered = _dmy(row['BN_REG_DT'], issues)
        record_id = 'BN-' + hashlib.sha1('|'.join([abn, name.casefold(), str(registered)]).encode()).hexdigest()[:20]
        yield _row(kind='b', record_id=record_id, abn=abn, name=name, status=_clean(row['BN_STATUS']),
                   registered_on=registered, deregistered_on=_dmy(row['BN_CANCEL_DT'], issues),
                   state=_clean(row['BN_STATE_OF_REG']), issues=json.dumps(issues))


def table_encoding(opener):
    """UTF-8 (BOM allowed) if the whole member decodes strictly, else strictly Windows-1252.

    Decided by a full pass before any row is staged, so a late decoding error cannot leave half a file
    staged in the wrong encoding (registers._scan restarts instead, which needs everything in memory)."""
    for encoding in ('utf-8-sig', 'cp1252'):
        decoder = codecs.getincrementaldecoder(encoding)(errors='strict')
        try:
            with opener() as raw:
                for chunk in iter(lambda: raw.read(1 << 20), b''):
                    decoder.decode(chunk)
                decoder.decode(b'', final=True)
        except UnicodeDecodeError:
            continue
        return encoding
    raise SourceError('Source is neither UTF-8 nor Windows-1252')


def producer(source, files, scope, metrics, extract_times):
    paths = [f['path'] for f in files]
    if source == 'abn':
        yield from abn_rows(paths, scope, metrics, extract_times)
        return
    with zipfile.ZipFile(paths[0]) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(('.csv', '.txt'))]
        if len(members) != 1:
            raise SourceError('Expected one table in the ' + source + ' archive')
        metrics['encoding'] = encoding = table_encoding(lambda: archive.open(members[0]))
        with archive.open(members[0]) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding=encoding, errors='strict', newline=''), delimiter='\t')
            _require(reader, ASIC_COLUMNS if source == 'asic_companies' else BUSINESS_NAME_COLUMNS)
            yield from (asic_rows if source == 'asic_companies' else business_name_rows)(reader, metrics)


# ---------------------------------------------------------------- import

def _copy(conn, statement, rows):
    """COPY rows on the transaction's own connection; returns the row count."""
    count = 0
    cursor = conn.connection.driver_connection.cursor()
    with cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(row)
            count += 1
    return count


def _promote(conn, source, snapshot_id, metrics):
    """Staged rows -> register_entities / register_names of the new snapshot. Returns accepted entity rows."""
    p = dict(sid=snapshot_id)
    if source == 'abn':
        inserted = conn.execute(text('INSERT INTO market.register_entities(' + ENTITY_COLUMNS + ''')
            SELECT DISTINCT ON (record_id) :sid,record_id,abn,acn,NULL,name,name_key,legal_key,is_individual,status,
                   status_from,NULL,NULL,entity_type,state,postcode,attributes,issues
            FROM register_stage WHERE kind='e' ORDER BY record_id, updated DESC NULLS LAST'''), p).rowcount
        metrics['duplicates'] = conn.execute(text("SELECT count(*) FROM register_stage WHERE kind='e'")).scalar() - inserted
    elif source == 'asic_companies':
        inserted = conn.execute(text('INSERT INTO market.register_entities(' + ENTITY_COLUMNS + ''')
            SELECT DISTINCT ON (s.record_id) :sid,s.record_id,s.abn,s.acn,NULL,s.name,s.name_key,s.legal_key,false,s.status,
                   NULL,s.registered_on,s.deregistered_on,NULLIF(concat_ws(' / ',s.company_type,s.company_class),''),NULL,NULL,
                   jsonb_build_object('type',s.company_type,'company_class',s.company_class),s.issues
            FROM register_stage s WHERE s.kind='a' AND s.is_current
              AND (s.status=ANY(:active) OR s.abn IN (SELECT abn FROM register_scope_stage))
            ORDER BY s.record_id, s.registered_on DESC NULLS LAST, s.name'''), dict(p, active=list(ASIC_ACTIVE))).rowcount
        metrics['acn_with_several_current_names'] = conn.execute(text('''SELECT count(*) FROM (SELECT record_id FROM register_stage
            WHERE kind='a' AND is_current GROUP BY record_id HAVING count(*)>1) x''')).scalar()
        # Former names become other names of kept companies (below). The stage was analysed before these rows
        # existed: re-analyse, or the planner expects ~0 name rows and compares millions x millions (27 Nov:
        # the national ASIC import ran 4 hours on the other-names insert and was cancelled).
        conn.execute(text('''INSERT INTO register_stage(kind,record_id,name,name_key,legal_key,name_type)
            SELECT 'n',record_id,name,name_key,legal_key,'asic_former' FROM register_stage
            WHERE kind='a' AND NOT is_current AND name IS NOT NULL'''))
        conn.execute(text('ANALYZE register_stage'))
    else:
        current = conn.execute(text('''SELECT s.scope_kind FROM market.current_register_snapshots c
            JOIN market.register_snapshots s ON s.id=c.snapshot_id WHERE c.source='abn' ''')).scalar()
        if current != 'national_active':
            raise SourceError('Load the national ABN register before national business names (holder type is needed)')
        # Organisation ABNs of the national register as an indexed, analysed set (millions of rows): the join
        # below must not depend on estimates for the large current-register view.
        conn.execute(text('''CREATE TEMP TABLE register_org_abns ON COMMIT DROP AS
            SELECT DISTINCT e.abn FROM market.current_register_entities e
            WHERE e.source='abn' AND NOT e.is_individual AND e.abn IS NOT NULL'''))
        conn.execute(text('ALTER TABLE register_org_abns ADD PRIMARY KEY (abn)'))
        conn.execute(text('ANALYZE register_org_abns'))
        inserted = conn.execute(text('INSERT INTO market.register_entities(' + ENTITY_COLUMNS + ''')
            SELECT DISTINCT ON (s.record_id) :sid,s.record_id,s.abn,NULL,NULL,s.name,s.name_key,s.legal_key,false,s.status,
                   NULL,s.registered_on,s.deregistered_on,'Business name',s.state,NULL,'{}'::jsonb,s.issues
            FROM register_stage s JOIN register_org_abns o ON o.abn=s.abn WHERE s.kind='b'
              AND (s.status=:active OR s.abn IN (SELECT abn FROM register_scope_stage))
            ORDER BY s.record_id'''), dict(p, active=BN_ACTIVE)).rowcount
        metrics['skipped_individual_unknown_or_inactive'] = conn.execute(text(
            "SELECT count(DISTINCT record_id) FROM register_stage WHERE kind='b'")).scalar() - inserted
    # Other names only for organisations kept in this snapshot; never for an individual. The kept organisations
    # are an indexed, analysed set (rows inserted in this transaction have no statistics yet).
    conn.execute(text('''CREATE TEMP TABLE register_kept ON COMMIT DROP AS
        SELECT record_id FROM market.register_entities WHERE snapshot_id=:sid AND NOT is_individual'''), p)
    conn.execute(text('ALTER TABLE register_kept ADD PRIMARY KEY (record_id)'))
    conn.execute(text('ANALYZE register_kept'))
    metrics['other_names'] = conn.execute(text('''INSERT INTO market.register_names(snapshot_id,record_id,name_type,name,name_key,legal_key)
        SELECT DISTINCT :sid,s.record_id,s.name_type,s.name,s.name_key,s.legal_key
        FROM register_stage s JOIN register_kept k ON k.record_id=s.record_id
        WHERE s.kind='n' AND s.name IS NOT NULL
        ON CONFLICT DO NOTHING'''), p).rowcount
    return inserted


def import_national(engine, manifest, source, min_rows=None):
    """Import a whole national register file (see module docstring). Idempotent per file and parser revision."""
    if source not in NATIONAL_SOURCES:
        raise ValueError('National import supports abn, asic_companies and asic_business_names only')
    minimum = MIN_ROWS[source] if min_rows is None else min_rows
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,:source,'running')"),
                     {'id': run_id, 'source': 'register_national:' + source})
    metrics = dict(records_read=0, rows_read=0, active_read=0, invalid_abn=0, out_of_scope=0, individuals=0,
                   inactive_procurement_kept=0, members=0, skipped_without_abn_or_name=0, staged_rows=0)
    try:
        files, metadata = verified_resources(manifest, source)
        with engine.connect() as conn:
            scope = procurement_scope(conn)
        if not scope:
            raise SourceError('No procurement snapshot ABNs (procurement ABNs are always kept)')
        metadata.update(parser_revision=NATIONAL_REVISION, scope_kind='national_active', scope_count=len(scope),
                        scope_sha256=hashlib.sha256('\n'.join(sorted(scope)).encode()).hexdigest())
        with engine.begin() as conn:
            conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'market:register:' + source})
            existing = conn.execute(text('''SELECT id FROM market.register_snapshots WHERE source=:source AND sha256=:sha256
                AND parser_revision=:parser_revision AND scope_sha256=:scope_sha256'''), metadata).scalar()
            if existing:
                status, snapshot_id = 'unchanged', existing
            else:
                status, snapshot_id = 'complete', uuid.uuid4()
                conn.execute(text("SET LOCAL statement_timeout = '4h'"))
                conn.execute(text("SET LOCAL work_mem = '256MB'"))
                conn.execute(text(STAGE))
                conn.execute(text('CREATE TEMP TABLE register_scope_stage(abn text PRIMARY KEY) ON COMMIT DROP'))
                _copy(conn, 'COPY register_scope_stage(abn) FROM STDIN', ((a,) for a in sorted(scope)))
                extract_times = []
                metrics['staged_rows'] = _copy(conn, 'COPY register_stage(' + ','.join(STAGE_COLUMNS) + ') FROM STDIN',
                                               producer(source, files, scope, metrics, extract_times))
                for f in files:
                    if file_hash(f['path']) != f['sha256']:
                        raise SourceError('Resource changed while parsing')
                if source == 'abn' and not extract_times:
                    raise SourceError('ABN extract time missing')
                conn.execute(text('ANALYZE register_stage'))
                effective, basis = _effective(source, files, min(extract_times) if extract_times else None)
                conn.execute(text('''INSERT INTO market.register_snapshots(id,source,sha256,retrieved_at,effective_date,
                    effective_date_basis,source_url,licence,licence_url,parser_revision,scope_kind,scope_sha256,scope_count,
                    row_count,metrics) VALUES(:id,:source,:sha256,:retrieved_at,:effective_date,:effective_date_basis,:source_url,
                    :licence,:licence_url,:parser_revision,:scope_kind,:scope_sha256,:scope_count,1,'{}'::jsonb)'''),
                    dict(metadata, id=snapshot_id, effective_date=effective, effective_date_basis=basis))
                conn.execute(text('INSERT INTO market.register_scope(snapshot_id,abn) SELECT :sid,abn FROM register_scope_stage'),
                             dict(sid=snapshot_id))
                accepted = _promote(conn, source, snapshot_id, metrics)
                if accepted < minimum:
                    raise SourceError(f'National {source} import accepted only {accepted} rows; refusing to promote it')
                metrics['accepted_rows'] = accepted
                if source == 'abn' and metrics['active_read']:
                    loaded = conn.execute(text('''SELECT count(*) FROM market.register_entities
                        WHERE snapshot_id=:sid AND status=:active'''), dict(sid=snapshot_id, active=ABN_ACTIVE)).scalar()
                    metrics['active_abns_loaded_share'] = round(loaded / metrics['active_read'], 6)
                conn.execute(text('''UPDATE market.register_snapshots SET row_count=:rows,metrics=CAST(:metrics AS jsonb)
                    WHERE id=:sid'''), dict(rows=accepted, metrics=json.dumps(metrics, default=str), sid=snapshot_id))
                # Never roll the current pointer backwards when an older file is imported later.
                conn.execute(text('''INSERT INTO market.current_register_snapshots(source,snapshot_id) VALUES(:source,:id)
                    ON CONFLICT(source) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
                    WHERE (SELECT (effective_date,retrieved_at,imported_at) FROM market.register_snapshots WHERE id=EXCLUDED.snapshot_id)
                       > (SELECT (effective_date,retrieved_at,imported_at) FROM market.register_snapshots
                          WHERE id=market.current_register_snapshots.snapshot_id)'''), {'source': source, 'id': snapshot_id})
            conn.execute(text('''UPDATE market.pipeline_runs SET finished_at=now(),status=:status,
                snapshot_id=:snapshot,metrics=CAST(:metrics AS jsonb) WHERE id=:id'''),
                dict(status=status, snapshot=snapshot_id, metrics=json.dumps(metrics, default=str), id=run_id))
        return dict(run_id=str(run_id), snapshot_id=str(snapshot_id), status=status, metrics=metrics)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.pipeline_runs SET finished_at=now(),status='failed',error_code=:error WHERE id=:id"),
                         {'id': run_id, 'error': type(exc).__name__})
        raise

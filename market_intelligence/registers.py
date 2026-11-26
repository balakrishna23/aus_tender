"""National business registers (ABN, ASIC companies, ACNC, ORIC) for supplier identity checks.

Privacy rules applied before any database write:
- Individuals (sole traders) keep only ABN status, entity type, state and postcode. Their
  names, including trading names that may contain a person's name, are dropped.
- Street addresses, towns, contact fields and people are never stored. Websites pass the
  same contact filter as government register websites.
The ABR and ASIC files are imported only for ABNs that appear in the current procurement
snapshot (suppliers and buyers); the scope list is stored so absence can be explained.
"""
import csv
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import io
import json
from pathlib import Path
import re
import uuid
import zipfile

from defusedxml.ElementTree import iterparse
from sqlalchemy import text

from .ingest import file_hash
from .parsers import SourceError, legal_key, name_key, valid_abn, website

PARSER_REVISION = '1'
SOURCES = ('abn', 'asic_companies', 'asic_business_names', 'acnc', 'oric')
SCOPED = ('abn', 'asic_companies', 'asic_business_names')
SUFFIX = {'abn': '.zip', 'asic_companies': '.zip', 'asic_business_names': '.zip', 'acnc': '.csv', 'oric': '.csv'}
LICENCES = ['Creative Commons Attribution 3.0 Australia', 'Creative Commons Attribution 4.0 International']
MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
                                       'september', 'october', 'november', 'december'], 1)}
BATCH = 5000


@dataclass
class Entity:
    record_id: str
    name: str | None = None
    abn: str | None = None
    acn: str | None = None
    icn: str | None = None
    is_individual: bool = False
    status: str | None = None
    status_from: date | None = None
    registered_on: date | None = None
    deregistered_on: date | None = None
    entity_type: str | None = None
    state: str | None = None
    postcode: str | None = None
    attributes: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)
    other_names: set = field(default_factory=set)


def _ymd(value, issues):
    """ABR dates; one malformed date in twenty million records is an issue, not a failed import."""
    value = (value or '').strip()
    if not value or set(value) == {'0'}:
        return None
    try:
        return datetime.strptime(value, '%Y%m%d').date()
    except ValueError:
        issues.append('invalid_date')
        return None


def _dmy(value, issues):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%d/%m/%Y').date()
    except ValueError:
        issues.append('invalid_date')
        return None


def _abn(value, issues):
    """Valid ABN, or None. Blank and ASIC's '0' mean 'no ABN', not an invalid one."""
    if (value or '').strip() in ('', '0'):
        return None
    try:
        return valid_abn(value)
    except SourceError:
        issues.append('invalid_abn')
        return None


def _clean(value):
    value = re.sub(r'\s+', ' ', value or '').strip()
    return value or None


def _scan(opener, delimiter, consume):
    """Stream a text table through consume(reader, encoding): strictly UTF-8 (BOM allowed), else
    strictly Windows-1252. A decoding error part-way restarts the whole pass in the next encoding,
    so nothing from a wrongly decoded pass survives."""
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            with opener() as raw:
                stream = io.TextIOWrapper(raw, encoding=encoding, errors='strict', newline='')
                return consume(csv.DictReader(stream, delimiter=delimiter), encoding)
        except UnicodeDecodeError:
            continue
    raise SourceError('Source is neither UTF-8 nor Windows-1252')


def _require(reader, columns):
    missing = [c for c in columns if c not in (reader.fieldnames or [])]
    if missing:
        raise SourceError('Required columns missing: ' + ', '.join(missing))


def _nonempty(metrics):
    if not metrics['rows_read']:
        raise SourceError('Source contains no data rows')


# ---------------------------------------------------------------- ABN bulk extract (XML in ZIP)

def parse_abn(paths, scope):
    """Stream every XML member of the bulk-extract ZIPs, keeping only ABNs in scope."""
    if not scope:
        raise SourceError('ABN import requires a non-empty procurement ABN scope')
    found, metrics, extract_times = {}, dict(records_read=0, invalid_abn=0, out_of_scope=0,
                                             individuals=0, duplicates=0, members=0), []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            members = sorted(n for n in archive.namelist() if n.lower().endswith('.xml'))
            if not members:
                raise SourceError('ABN archive contains no XML members')
            for member in members:
                metrics['members'] += 1
                with archive.open(member) as stream:
                    _parse_abn_stream(stream, scope, found, metrics, extract_times)
    if not extract_times:
        raise SourceError('ABN extract time missing')
    return list(found.values()), metrics, min(extract_times)


def _parse_abn_stream(stream, scope, found, metrics, extract_times):
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
            entity = _abn_record(elem, scope, metrics)
            if entity:
                previous = found.get(entity.record_id)
                if previous:
                    metrics['duplicates'] += 1
                    if previous.attributes.get('record_last_updated', '') >= entity.attributes.get('record_last_updated', ''):
                        entity = previous
                found[entity.record_id] = entity
            root.clear()


def _abn_record(elem, scope, metrics, keep=None):
    """One ABR record, or None. `keep(abn, status)` replaces the procurement-scope test (national mode)."""
    abn_el = elem.find('ABN')
    if abn_el is None or not (abn_el.text or '').strip():
        raise SourceError('ABN record without ABN')
    issues = []
    abn = _abn(abn_el.text, issues)
    if abn is None:
        metrics['invalid_abn'] += 1
        return None
    if not (keep(abn, abn_el.get('status')) if keep else abn in scope):
        metrics['out_of_scope'] += 1
        return None
    entity_type = elem.findtext('EntityType/EntityTypeInd')
    individual = entity_type == 'IND' or elem.find('LegalEntity/IndividualName') is not None
    metrics['individuals'] += individual
    holder = elem.find('MainEntity')
    if holder is None:
        holder = elem.find('LegalEntity')
    state = postcode = None
    if holder is not None:
        state = _clean(holder.findtext('BusinessAddress/AddressDetails/State'))
        postcode = _clean(holder.findtext('BusinessAddress/AddressDetails/Postcode'))
    acn = _clean(elem.findtext('ASICNumber'))
    gst = elem.find('GST')
    gst_from = _ymd(gst.get('GSTStatusFromDate'), issues) if gst is not None else None
    entity = Entity(
        record_id=abn, abn=abn, acn=acn if acn and re.fullmatch(r'[0-9]{9}', acn) else None,
        is_individual=individual, status=abn_el.get('status'), status_from=_ymd(abn_el.get('ABNStatusFromDate'), issues),
        entity_type=_clean(elem.findtext('EntityType/EntityTypeText')) or entity_type, state=state, postcode=postcode,
        attributes=dict(entity_type_code=entity_type, record_last_updated=elem.get('recordLastUpdatedDate') or '',
                        replaced=elem.get('replaced'), gst_status=gst.get('status') if gst is not None else None,
                        gst_from=str(gst_from) if gst_from else None, dgr_endorsed=elem.find('DGR') is not None),
        issues=issues)
    if not individual:
        entity.name = _clean(elem.findtext('MainEntity/NonIndividualName/NonIndividualNameText'))
        if not entity.name:
            entity.issues.append('missing_registered_name')
        for other in elem.findall('OtherEntity/NonIndividualName'):
            other_name = _clean(other.findtext('NonIndividualNameText'))
            if other_name:
                entity.other_names.add(('abr_' + (other.get('type') or 'other').lower(), other_name))
    return entity


# ---------------------------------------------------------------- ASIC companies (tab CSV in ZIP)

ASIC_COLUMNS = ['Company Name', 'ACN', 'Type', 'Class', 'Status', 'Date of Registration',
                'Date of Deregistration', 'Current Name Indicator', 'ABN']


def parse_asic_companies(path, scope):
    if not scope:
        raise SourceError('ASIC import requires a non-empty procurement ABN scope')
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(('.csv', '.txt'))]
        if len(members) != 1:
            raise SourceError('Expected one ASIC company table')
        return _scan(lambda: archive.open(members[0]), '\t', lambda reader, enc: _asic_entities(reader, scope, enc))


def _asic_entities(reader, scope, encoding):
    _require(reader, ASIC_COLUMNS)
    companies, former, metrics = {}, {}, dict(rows_read=0, encoding=encoding, out_of_scope=0, invalid_abn=0)
    for row in reader:
        metrics['rows_read'] += 1
        acn = (row['ACN'] or '').strip()
        if not re.fullmatch(r'[0-9]{9}', acn):
            raise SourceError('ASIC row with invalid ACN')
        issues = []
        abn = _abn(row['ABN'], issues)
        metrics['invalid_abn'] += 'invalid_abn' in issues
        if abn not in scope:
            metrics['out_of_scope'] += 1
            continue
        name = _clean(row['Company Name'])
        if (row['Current Name Indicator'] or '').strip().upper() == 'Y':
            if acn in companies:
                raise SourceError('ASIC company has two current names')
            companies[acn] = Entity(
                record_id=acn, acn=acn, abn=abn, name=name, status=_clean(row['Status']),
                registered_on=_dmy(row['Date of Registration'], issues), deregistered_on=_dmy(row['Date of Deregistration'], issues),
                entity_type=' / '.join(x for x in [_clean(row['Type']), _clean(row['Class'])] if x),
                attributes=dict(type=_clean(row['Type']), company_class=_clean(row['Class'])), issues=issues)
        elif name:
            former.setdefault(acn, set()).add(('asic_former', name))
    _nonempty(metrics)
    for acn, names in former.items():
        if acn in companies:
            companies[acn].other_names |= names
    metrics['former_names_without_current_row'] = sum(acn not in companies for acn in former)
    return list(companies.values()), metrics


# ---------------------------------------------------------------- ASIC business names (tab CSV in ZIP)

BUSINESS_NAME_COLUMNS = ['BN_NAME', 'BN_STATUS', 'BN_REG_DT', 'BN_CANCEL_DT', 'BN_STATE_OF_REG', 'BN_ABN']


def parse_asic_business_names(path, organisations):
    """Business names held by ABNs in `organisations`: in the procurement scope AND known from the
    current ABN register not to be individuals. Names held by sole traders often contain a person's
    name, so a holder that is an individual, or whose status is unknown, is skipped."""
    if not organisations:
        raise SourceError('Load the ABN register before business names (holder type is needed)')
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith(('.csv', '.txt'))]
        if len(members) != 1:
            raise SourceError('Expected one ASIC business names table')
        return _scan(lambda: archive.open(members[0]), '\t', lambda reader, enc: _business_names(reader, organisations, enc))


def _business_names(reader, organisations, encoding):
    _require(reader, BUSINESS_NAME_COLUMNS)
    found, metrics = {}, dict(rows_read=0, encoding=encoding, skipped_not_organisation_in_scope=0, duplicates=0)
    for row in reader:
        metrics['rows_read'] += 1
        issues = []
        abn = _abn(row['BN_ABN'], issues)
        name = _clean(row['BN_NAME'])
        if abn not in organisations or not name:
            metrics['skipped_not_organisation_in_scope'] += 1
            continue
        registered = _dmy(row['BN_REG_DT'], issues)
        record_id = 'BN-' + hashlib.sha1('|'.join([abn, name.casefold(), str(registered)]).encode()).hexdigest()[:20]
        if record_id in found:
            metrics['duplicates'] += 1
            continue
        found[record_id] = Entity(record_id=record_id, abn=abn, name=name, status=_clean(row['BN_STATUS']),
                                  registered_on=registered, deregistered_on=_dmy(row['BN_CANCEL_DT'], issues),
                                  entity_type='Business name', state=_clean(row['BN_STATE_OF_REG']), issues=issues)
    _nonempty(metrics)
    return list(found.values()), metrics


# ---------------------------------------------------------------- ACNC charities (CSV)

ACNC_COLUMNS = ['ABN', 'Charity_Legal_Name', 'Other_Organisation_Names', 'State', 'Postcode',
                'Charity_Website', 'Registration_Date', 'Date_Organisation_Established', 'Charity_Size', 'PBI', 'HPC',
                'Aboriginal_or_TSI', 'Advancing_Health']
ACNC_STATES = ['ACT', 'NSW', 'NT', 'QLD', 'SA', 'TAS', 'VIC', 'WA']


def parse_acnc(path):
    return _scan(lambda: open(path, 'rb'), ',', _acnc_entities)


def _flag(value):
    return (value or '').strip().upper() in ('Y', 'YES', 'TRUE')


def _acnc_entities(reader, encoding):
    _require(reader, ACNC_COLUMNS)
    found, metrics = {}, dict(rows_read=0, encoding=encoding, invalid_abn=0, missing_abn=0)
    for row in reader:
        metrics['rows_read'] += 1
        issues = []
        abn = _abn(row['ABN'], issues)
        if abn is None:
            metrics['invalid_abn' if issues else 'missing_abn'] += 1
            continue
        if abn in found:
            raise SourceError('Duplicate ACNC ABN')
        site = (row['Charity_Website'] or '').strip()
        if site and '://' not in site and '@' not in site:
            site = 'http://' + site  # most charity websites are published without a scheme
        entity = Entity(
            record_id=abn, abn=abn, name=_clean(row['Charity_Legal_Name']), status='Registered',
            registered_on=_dmy(row['Registration_Date'], issues), state=_clean(row['State']), postcode=_clean(row['Postcode']),
            entity_type='Registered charity',
            attributes=dict(size=_clean(row['Charity_Size']), pbi=_flag(row['PBI']), hpc=_flag(row['HPC']),
                            established=str(_dmy(row['Date_Organisation_Established'], issues) or '') or None,
                            serves_aboriginal_or_tsi=_flag(row['Aboriginal_or_TSI']),
                            advancing_health=_flag(row['Advancing_Health']),
                            operates_in=[s for s in ACNC_STATES if _flag(row.get('Operates_in_' + s))],
                            website=website(site, issues) or None),
            issues=issues)
        if not entity.name:
            entity.issues.append('missing_registered_name')
        # Commas occur inside names; only semicolons and pipes separate several names.
        for other in re.split(r'[;|]', row['Other_Organisation_Names'] or ''):
            if _clean(other):
                entity.other_names.add(('acnc_other', _clean(other)))
        found[abn] = entity
    _nonempty(metrics)
    return list(found.values()), metrics


# ---------------------------------------------------------------- ORIC corporations (CSV)

ORIC_COLUMNS = ['ICN', 'Corporation Name', 'Status Reason', 'Registered On', 'Deregistered On', 'Corporation Size',
                'ABN', 'State/Territory (Main place of business) (Address)', 'Postcode (Main place of business) (Address)',
                'Industry Sector(s)', 'Corporation registered with ACNC?']


def parse_oric(path):
    return _scan(lambda: open(path, 'rb'), ',', _oric_entities)


def _latest_band(row, measure):
    """Most recent non-empty yearly band, e.g. '2025 Total Income'."""
    years = sorted({m[1] for k in row for m in [re.fullmatch(r'(20[0-9]{2}) ' + measure, k or '')] if m}, reverse=True)
    for year in years:
        value = _clean(row.get(year + ' ' + measure))
        if value:
            return dict(year=int(year), band=value)
    return None


def _oric_entities(reader, encoding):
    _require(reader, ORIC_COLUMNS)
    found, metrics = {}, dict(rows_read=0, encoding=encoding, invalid_abn=0, missing_abn=0)
    for row in reader:
        metrics['rows_read'] += 1
        icn = (row['ICN'] or '').strip()
        if not re.fullmatch(r'[0-9]+', icn):
            raise SourceError('ORIC row with invalid ICN')
        if icn in found:
            raise SourceError('Duplicate ORIC ICN')
        issues = []
        abn = _abn(row['ABN'], issues)
        metrics['invalid_abn'] += 'invalid_abn' in issues
        metrics['missing_abn'] += abn is None and 'invalid_abn' not in issues
        entity = Entity(
            record_id='ICN-' + icn, icn=icn, abn=abn, name=_clean(row['Corporation Name']),
            status=_clean(row['Status Reason']), registered_on=_dmy(row['Registered On'], issues),
            deregistered_on=_dmy(row['Deregistered On'], issues), entity_type='Aboriginal and Torres Strait Islander corporation',
            state=_clean(row['State/Territory (Main place of business) (Address)']),
            postcode=_clean(row['Postcode (Main place of business) (Address)']),
            attributes=dict(size=_clean(row['Corporation Size']), industry=_clean(row['Industry Sector(s)']),
                            acnc_registered=_flag(row['Corporation registered with ACNC?']),
                            income=_latest_band(row, 'Total Income'), employees=_latest_band(row, 'Number of Employees')),
            issues=issues)
        if not entity.name:
            entity.issues.append('missing_registered_name')
        found[icn] = entity
    _nonempty(metrics)
    return list(found.values()), metrics


# ---------------------------------------------------------------- manifest verification and import

def verified_resources(manifest, source):
    if source not in SOURCES:
        raise ValueError('Unsupported register source')
    manifest = Path(manifest).resolve(strict=True)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('status') != 'complete':
        raise SourceError('Acquisition manifest is not complete')
    entries = [f for f in data['files'] if f['dataset'] == source and f.get('status') == 'complete'
               and Path(f['path']).suffix.lower() == SUFFIX[source]]
    if not entries or (source != 'abn' and len(entries) != 1):
        raise SourceError('Expected ' + ('at least one' if source == 'abn' else 'exactly one') + ' data resource')
    files = []
    for item in sorted(entries, key=lambda f: f['path']):
        path = Path(item['path']).resolve(strict=True)
        if not path.is_relative_to(manifest.parent):
            raise SourceError('Resource is outside acquisition directory')
        if item.get('licence') not in LICENCES:
            raise SourceError('Resource licence is outside approved scope')
        if path.stat().st_size != item['bytes'] or file_hash(path) != item['sha256']:
            raise SourceError('Resource checksum/size mismatch')
        retrieved = datetime.fromisoformat(item['retrieved_at'])
        if retrieved.tzinfo is None:
            raise SourceError('Retrieval timestamp must include timezone')
        files.append(dict(path=path, sha256=item['sha256'], name=item.get('name') or path.name, retrieved_at=retrieved,
                          licence=item['licence'], licence_url=item['licence_url']))
    if len({f['licence'] for f in files}) != 1:
        raise SourceError('Resources of one source carry different licences')
    dataset = next(d for d in data['datasets'] if d['key'] == source)
    combined = files[0]['sha256'] if len(files) == 1 else hashlib.sha256(
        '\n'.join(f['sha256'] for f in files).encode()).hexdigest()
    return files, dict(source=source, sha256=combined, retrieved_at=max(f['retrieved_at'] for f in files),
                       source_url=dataset['source_url'], licence=files[0]['licence'], licence_url=files[0]['licence_url'])


def _effective(source, files, extract_date):
    if source == 'abn':
        return extract_date, 'publisher_extract_time'
    if source == 'oric':
        match = re.search(r'\b([0-9]{1,2}) ([A-Za-z]+) (20[0-9]{2})\b', files[0]['name'])
        if match and match[2].lower() in MONTHS:
            return date(int(match[3]), MONTHS[match[2].lower()], int(match[1])), 'publisher_snapshot_date'
        raise SourceError('ORIC snapshot date missing from resource name')
    return files[0]['retrieved_at'].date(), 'retrieval_date; publisher file is a rolling export'


def procurement_scope(conn):
    """Valid supplier and buyer ABNs in the current procurement snapshot."""
    values = conn.execute(text('''SELECT supplier_abn FROM market.current_contracts WHERE supplier_abn IS NOT NULL
        UNION SELECT buyer_abn FROM market.current_contracts WHERE buyer_abn IS NOT NULL''')).scalars()
    scope = set()
    for value in values:
        try:
            abn = valid_abn(value)
        except SourceError:
            continue
        if abn:
            scope.add(abn)
    return scope


def parse_source(source, files, scope, organisations=None):
    paths = [f['path'] for f in files]
    if source == 'abn':
        return parse_abn(paths, scope)
    if source == 'asic_business_names':
        return (*parse_asic_business_names(paths[0], organisations), None)
    if source == 'asic_companies':
        return (*parse_asic_companies(paths[0], scope), None)
    if source == 'acnc':
        return (*parse_acnc(paths[0]), None)
    return (*parse_oric(paths[0]), None)


def import_register(engine, manifest, source):
    if source not in SOURCES:
        raise ValueError('Unsupported register source')
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,:source,'running')"),
                     {'id': run_id, 'source': 'register:' + source})
    try:
        files, metadata = verified_resources(manifest, source)
        scope = None
        if source in SCOPED:
            with engine.connect() as conn:
                scope = procurement_scope(conn)
            if not scope:
                raise SourceError('No procurement snapshot ABNs to scope the import')
        organisations = None
        if source == 'asic_business_names':
            with engine.connect() as conn:
                organisations = scope & set(conn.execute(text("""SELECT abn FROM market.current_register_entities
                    WHERE source='abn' AND NOT is_individual AND abn IS NOT NULL""")).scalars())
        records, metrics, extract_date = parse_source(source, files, scope, organisations)
        if not records:
            raise SourceError('No register records accepted')
        for f in files:
            if file_hash(f['path']) != f['sha256']:
                raise SourceError('Resource changed while parsing')
        effective, basis = _effective(source, files, extract_date)
        scope_sha = hashlib.sha256('\n'.join(sorted(scope)).encode()).hexdigest() if scope else ''
        metrics.update(accepted_rows=len(records), individuals_kept_without_names=sum(r.is_individual for r in records))
        metadata.update(parser_revision=PARSER_REVISION, effective_date=effective, effective_date_basis=basis,
                        scope_kind='procurement_abns' if scope else 'complete', scope_sha256=scope_sha,
                        scope_count=len(scope) if scope else None, row_count=len(records))
        with engine.begin() as conn:
            conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'market:register:' + source})
            existing = conn.execute(text('''SELECT id FROM market.register_snapshots WHERE source=:source AND sha256=:sha256
                AND parser_revision=:parser_revision AND scope_sha256=:scope_sha256'''), metadata).scalar()
            if existing:
                status, snapshot_id = 'unchanged', existing
            else:
                status, snapshot_id = 'complete', uuid.uuid4()
                _write_snapshot(conn, snapshot_id, metadata, metrics, records, scope)
            conn.execute(text('''UPDATE market.pipeline_runs SET finished_at=now(),status=:status,
                snapshot_id=:snapshot,metrics=CAST(:metrics AS jsonb) WHERE id=:id'''),
                dict(status=status, snapshot=snapshot_id, metrics=json.dumps(metrics, default=str), id=run_id))
        return dict(run_id=str(run_id), snapshot_id=str(snapshot_id), status=status, metrics=metrics)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.pipeline_runs SET finished_at=now(),status='failed',error_code=:error WHERE id=:id"),
                         {'id': run_id, 'error': type(exc).__name__})
        raise


def _write_snapshot(conn, snapshot_id, metadata, metrics, records, scope):
    conn.execute(text('''INSERT INTO market.register_snapshots(id,source,sha256,retrieved_at,effective_date,
        effective_date_basis,source_url,licence,licence_url,parser_revision,scope_kind,scope_sha256,scope_count,
        row_count,metrics) VALUES(:id,:source,:sha256,:retrieved_at,:effective_date,:effective_date_basis,:source_url,
        :licence,:licence_url,:parser_revision,:scope_kind,:scope_sha256,:scope_count,:row_count,CAST(:metrics AS jsonb))'''),
        dict(metadata, id=snapshot_id, metrics=json.dumps(metrics, default=str)))
    if scope:
        ordered = sorted(scope)
        for i in range(0, len(ordered), BATCH):
            conn.execute(text('INSERT INTO market.register_scope(snapshot_id,abn) VALUES(:s,:abn)'),
                         [dict(s=snapshot_id, abn=a) for a in ordered[i:i + BATCH]])
    rows = [dict(snapshot_id=snapshot_id, record_id=r.record_id, abn=r.abn, acn=r.acn, icn=r.icn, name=r.name,
                 name_key=name_key(r.name) if r.name else None, legal_key=legal_key(r.name) if r.name else None,
                 is_individual=r.is_individual, status=r.status,
                 status_from=r.status_from, registered_on=r.registered_on, deregistered_on=r.deregistered_on,
                 entity_type=r.entity_type, state=r.state, postcode=r.postcode,
                 attributes=json.dumps(r.attributes, default=str), issues=json.dumps(r.issues)) for r in records]
    for i in range(0, len(rows), BATCH):
        conn.execute(text('''INSERT INTO market.register_entities(snapshot_id,record_id,abn,acn,icn,name,name_key,legal_key,
            is_individual,status,status_from,registered_on,deregistered_on,entity_type,state,postcode,attributes,issues)
            VALUES(:snapshot_id,:record_id,:abn,:acn,:icn,:name,:name_key,:legal_key,:is_individual,:status,:status_from,
            :registered_on,:deregistered_on,:entity_type,:state,:postcode,CAST(:attributes AS jsonb),CAST(:issues AS jsonb))'''),
            rows[i:i + BATCH])
    names = [dict(snapshot_id=snapshot_id, record_id=r.record_id, name_type=t, name=n, name_key=name_key(n), legal_key=legal_key(n))
             for r in records if not r.is_individual for t, n in sorted(r.other_names)]
    for i in range(0, len(names), BATCH):
        conn.execute(text('''INSERT INTO market.register_names(snapshot_id,record_id,name_type,name,name_key,legal_key)
            VALUES(:snapshot_id,:record_id,:name_type,:name,:name_key,:legal_key)'''), names[i:i + BATCH])
    # Never roll the current pointer backwards when an older file is imported later.
    conn.execute(text('''INSERT INTO market.current_register_snapshots(source,snapshot_id) VALUES(:source,:id)
        ON CONFLICT(source) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
        WHERE (SELECT (effective_date,retrieved_at,imported_at) FROM market.register_snapshots WHERE id=EXCLUDED.snapshot_id)
           > (SELECT (effective_date,retrieved_at,imported_at) FROM market.register_snapshots
              WHERE id=market.current_register_snapshots.snapshot_id)'''), {'source': metadata['source'], 'id': snapshot_id})

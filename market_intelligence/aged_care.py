"""Strict quarterly aged-care evidence, with no workforce-demand inference."""
from collections import Counter
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

from .parsers import EMAIL_PATTERN, SourceError, name_key
from .acquire_health import (DATASET, ORIGIN, COPYRIGHT, PERMISSION, MONTHS,
                             reference_from_title, discover_workbook, discover_publication)
from .acquire_common import AcquisitionError, official_url, check_workbook

PARSER_REVISION = 'aged-care-1'
BASE = ['Reporting Period', 'Service Name', 'Provider Name', 'Service Suburb', 'Purpose',
        'Aged Care Planning Region', 'State/Territory', 'MMM Region', 'MMM Code', 'Size']
RATINGS = ['Overall Star Rating', "Residents' Experience rating", 'Compliance rating',
           'Staffing rating', 'Quality Measures rating']
STAR_HEADERS = BASE + RATINGS
MINUTES = ['[S] Registered Nurse Care Minutes - Target', '[S] Registered Nurse Care Minutes - Actual',
           '[S] Total Care Minutes - Target', '[S] Total Care Minutes - Actual']
DATES = ['[C] Date Decision Applied', '[C] Date Decision Ends', '[C] Date Audit Closed']
STANDARDS = ['[C] Standard ' + str(i) + ': ' + label for i, label in enumerate([
    'The individual', 'The organisation', 'The care and services', 'The environment',
    'Clinical care', 'Food and nutrition', 'The residential community'], 1)]
DETAILED_HEADERS = (BASE + RATINGS[:2] + ['[RE] Interview Year'] + [
    '[RE] ' + subject + ' - ' + frequency
    for subject in ['Food', 'Safety', 'Operation', 'Care Need', 'Competent', 'Independent',
                    'Explain', 'Respect', 'Follow Up', 'Caring', 'Voice', 'Home']
    for frequency in ['Always', 'Most of the time', 'Some of the time', 'Never']]
    + [RATINGS[2], '[C] Decision type', DATES[0], DATES[1], '[C] Compliance Rating Reason', DATES[2]]
    + STANDARDS + [RATINGS[3]] + MINUTES + [RATINGS[4]] + ['[QM] ' + label for label in [
        'Pressure injuries*', 'Restrictive practices', 'Unplanned weight loss*',
        'Falls and major injury - falls*', 'Falls and major injury - major injury from a fall*',
        'Medication management - polypharmacy', 'Medication management - antipsychotic']])
REGIONS = dict(zip(['MM' + str(i) for i in range(1, 8)], ['Metropolitan', 'Regional centres',
    'Large rural towns', 'Medium rural towns', 'Small rural towns', 'Remote communities', 'Very remote communities']))
DECISIONS = {'Acceptance of an enforceable undertaking', 'Compliance notice (information suggesting non-compliance)',
    'Conditions of registration', 'Directions - Complaints/Provider Responsibility Information',
    'Directions - To revise Plans for Continuous Improvements (PCI)',
    'Incident management compliance notice (IMCN) Type A', 'Notice of Decision to impose Sanction (Sanction)',
    'Notice of Non-Compliance (NCN)', 'Notice of Requirement to Agree', 'Notice to Remedy (NTR)',
    'Restrictive practices Compliance Notice (RPCN) Type A'}
REASONS = {'2-Star regulatory decision', 'Conformance without exceeding grade', 'Major non-conformance',
    'Minor non-conformance', 'Resolved non-conformance',
    'No Regulatory decisions, and no Quality Standards grades recorded whilst operating continuously for over one year',
    'No current regulatory decisions and no Quality Standards grades recorded, whilst operating continuously for over 3 years',
    'Regulatory decision became non-current within the past 3 years, and no Quality Standards grades recorded',
    'Regulatory decision became non-current within the past year, and no Quality Standards grades recorded'}
PHONE = re.compile(r'(?<!\d)(?:(?:\+61[\s()-]*[23478]|0[23478])(?:[\s()-]*\d){8}|1[38]00(?:[\s()-]*\d){6}|13(?:[\s()-]*\d){4})(?!\d)')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def screen(value):
    if isinstance(value, str) and (EMAIL_PATTERN.search(value) or PHONE.search(value)):
        raise SourceError('Contact data found in workbook text; import refused')


def number(value, minimum=0, maximum=None, integer=False):
    if value is None:
        return None
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < minimum or (maximum is not None and value > maximum)
            or (integer and int(value) != value)):
        raise SourceError('Workbook numeric domain changed')
    return int(value) if integer else value


def validate(row, detailed):
    for value in row.values():
        screen(value)
    for field in BASE:
        if not isinstance(row[field], str) or not row[field].strip():
            raise SourceError('Missing required published key')
    domains = {'Purpose': {'Not for Profit', 'Private for Profit', 'Government'},
               'State/Territory': {'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT', 'ACT'},
               'Size': {'Small', 'Medium', 'Large'}, 'MMM Code': set(REGIONS)}
    if any(row[k] not in values for k, values in domains.items()) or REGIONS[row['MMM Code']] != row['MMM Region']:
        raise SourceError('Published categorical domain changed')
    for field in RATINGS:
        row[field] = number(row[field], 1, 5, True)
    if detailed:
        for field in MINUTES:
            row[field] = number(row[field])
        row['[RE] Interview Year'] = number(row['[RE] Interview Year'], 2000, 2100, True)
        for field in DETAILED_HEADERS:
            if field.startswith(('[RE] ', '[QM] ')) and field != '[RE] Interview Year':
                row[field] = number(row[field], 0, 100)
        for field, allowed in [('[C] Decision type', DECISIONS), ('[C] Compliance Rating Reason', REASONS)] + [
                (s, {'Conformance', 'Major non-conformance', 'Minor non-conformance', 'Resolved'}) for s in STANDARDS]:
            if row[field] is not None and row[field] not in allowed:
                raise SourceError('Published compliance domain changed')
        for field in DATES:
            value = row[field]
            if value is not None:
                if not isinstance(value, (date, datetime)):
                    raise SourceError('Published compliance date domain changed')
                row[field] = value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return row


def parse_aged_care(path, title=None):
    import openpyxl
    try:
        expected_period = reference_from_title(title or Path(path).stem)
    except (ValueError, AcquisitionError):
        raise SourceError('Quarterly publication title required') from None
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        if set(workbook.sheetnames) != {'Notes', 'Star Ratings', 'Detailed data'}:
            raise SourceError('Workbook sheet layout changed')
        notes_title = workbook['Notes'].cell(1, 1).value
        if notes_title != 'Star Ratings quarterly data extract':
            raise SourceError('Workbook Notes title changed')
        sheets = {}
        for sheet, headers in [('Star Ratings', STAR_HEADERS), ('Detailed data', DETAILED_HEADERS)]:
            iterator = workbook[sheet].iter_rows(values_only=True)
            if list(next(iterator, ())) != headers:
                raise SourceError('Workbook headers changed: ' + sheet)
            rows = []
            for cells in iterator:
                if not any(v is not None for v in cells):
                    raise SourceError('Blank data row; row accounting refused')
                rows.append(validate(dict(zip(headers, cells)), sheet == 'Detailed data'))
            if not rows:
                raise SourceError('Empty quarterly data sheet')
            sheets[sheet] = rows
        stars, detailed = sheets['Star Ratings'], sheets['Detailed data']
        shared = lambda row: tuple(row[h] for h in STAR_HEADERS)
        if Counter(map(shared, stars)) != Counter(map(shared, detailed)):
            raise SourceError('Star Ratings and Detailed data shared rows differ')
        period_label = MONTHS[int(expected_period[5:]) - 1].title() + ' ' + expected_period[:4]
        if {r['Reporting Period'] for r in stars} != {period_label}:
            raise SourceError('Data reference period differs from publication title')
        ordinals, triples = Counter(), Counter()
        observations = []
        for index, row in enumerate(detailed, 2):
            key = tuple(row[h] for h in BASE[1:])
            ordinals[key] += 1
            triples[tuple(row[h] for h in BASE[1:4])] += 1
            payload = {h: row[h] for h in STAR_HEADERS + MINUTES + ['[C] Decision type'] + DATES}
            observations.append(dict(row_identity=digest([key, ordinals[key]]), key_ordinal=ordinals[key],
                content_sha256=digest(row), source_row=index, provider_name=row['Provider Name'],
                provider_name_key=name_key(row['Provider Name']), payload=payload))
        return dict(reference_period=expected_period, rows=observations, metrics=dict(
            star_rows=len(stars), detailed_rows=len(detailed), imported_rows=len(observations),
            duplicate_service_provider_suburb_groups=sum(v > 1 for v in triples.values()),
            duplicate_full_key_groups=sum(v > 1 for v in ordinals.values()),
            duplicate_full_key_extra_rows=sum(v - 1 for v in ordinals.values()),
            blank_ratings={h: sum(r[h] is None for r in stars) for h in RATINGS}))
    finally:
        workbook.close()


def verified_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve(strict=True)
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    if data.get('status') != 'complete' or len(data.get('files', [])) != 1:
        raise SourceError('Complete single-workbook manifest required')
    item = data['files'][0]
    if item.get('dataset') != DATASET or item.get('status') != 'complete' or item.get('commercial_reuse') != PERMISSION:
        raise SourceError('Verified dataset and project-owner permission required')

    def checked(entry):
        path = (manifest_path.parent / entry['path']).resolve(strict=True)
        if not path.is_relative_to(manifest_path.parent):
            raise SourceError('Manifest resource outside immutable run')
        if path.stat().st_size != entry['bytes'] or entry['bytes'] > 20_000_000:
            raise SourceError('Manifest resource size mismatch')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise SourceError('Manifest resource checksum mismatch')
        official_url(entry['url'], ORIGIN)
        return path, content

    path, payload = checked(item)
    check_workbook(payload)
    for field in ['copyright_evidence', 'collection_evidence', 'publication_evidence']:
        checked(item[field])
    if item['copyright_page'] != COPYRIGHT or item['copyright_evidence']['url'] != COPYRIGHT:
        raise SourceError('Publisher copyright evidence required')
    publication, reference = discover_publication(checked(item['collection_evidence'])[1], item['collection_evidence']['url'])
    url, upload = discover_workbook(checked(item['publication_evidence'])[1], publication, reference)
    if (publication != item['publication_page'] or url != item['url'] or reference != item['reference_period']
            or upload != item['upload_month'] or item['publication_evidence']['url'] != publication):
        raise SourceError('Manifest publication provenance differs')
    official_url(item['resolved_url'], ORIGIN)
    if item['resolved_url'] != item['url']:
        raise SourceError('Workbook resolved URL differs')
    datetime.fromisoformat(item['retrieved_at'])
    return path, item


def import_aged_care(engine, manifest):
    from sqlalchemy import text
    path, meta = verified_manifest(manifest)
    parsed = parse_aged_care(path, meta['publication_page'].rsplit('/', 1)[-1])
    if parsed['reference_period'] != meta['reference_period']:
        raise SourceError('Workbook reference differs from manifest')
    snapshot = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(191126022)'))
        previous = conn.execute(text('''SELECT id FROM market.aged_care_snapshots
            WHERE sha256=:sha AND parser_revision=:revision'''), dict(sha=meta['sha256'], revision=PARSER_REVISION)).scalar()
        if previous:
            return dict(status='unchanged', snapshot_id=str(previous), **parsed['metrics'])
        conn.execute(text('''INSERT INTO market.aged_care_snapshots
            (id,sha256,parser_revision,reference_period,retrieved_at,provenance,metrics)
            VALUES (:id,:sha,:revision,:period,:retrieved,CAST(:provenance AS jsonb),CAST(:metrics AS jsonb))'''),
            dict(id=snapshot, sha=meta['sha256'], revision=PARSER_REVISION, period=parsed['reference_period'],
                 retrieved=meta['retrieved_at'], provenance=json.dumps(meta), metrics=json.dumps(parsed['metrics'])))
        conn.execute(text('''INSERT INTO market.aged_care_observations
            (snapshot_id,row_identity,key_ordinal,content_sha256,source_row,provider_name,provider_name_key,payload)
            VALUES (:snapshot_id,:row_identity,:key_ordinal,:content_sha256,:source_row,:provider_name,
                    :provider_name_key,CAST(:payload AS jsonb))'''),
            [dict(r, snapshot_id=snapshot, payload=json.dumps(r['payload'])) for r in parsed['rows']])
        conn.execute(text('''INSERT INTO market.current_aged_care_snapshot(singleton,snapshot_id) VALUES (true,:id)
            ON CONFLICT(singleton) DO UPDATE SET snapshot_id=excluded.snapshot_id'''), dict(id=snapshot))
    return dict(status='imported', snapshot_id=snapshot, **parsed['metrics'])


LIMITATIONS = [
    'Published Star Ratings are quality evidence for the stated reference period.',
    'Care-minutes shortfall means published target minus published actual, when positive; missing values remain unknown.',
    'These figures do not establish buying intent, vacancies, staffing demand or willingness to engage suppliers.',
    'No publisher service identifier exists. Published-key ordinals distinguish rows within an extract; cross-quarter home continuity is unverified.',
    'Register name matches are proposals only; ambiguous candidates remain visible and require review.',
    'Project-owner reuse permission is recorded separately from publisher copyright; no CC BY licence is asserted.']


def build_aged_care_report(engine):
    from sqlalchemy import text
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        snapshots = [dict(r) for r in conn.execute(text('''SELECT s.* FROM market.aged_care_snapshots s
            JOIN market.current_aged_care_snapshot c ON c.snapshot_id=s.id''')).mappings()]
        rows = [dict(r) for r in conn.execute(text('SELECT * FROM market.v_aged_care_quality ORDER BY source_row')).mappings()]
        proposals = [dict(r) for r in conn.execute(text('SELECT * FROM market.v_aged_care_provider_proposals')).mappings()]
    return dict(status='published_quality_evidence', snapshots=snapshots, rows=rows,
                provider_match_proposals=proposals, limitations=LIMITATIONS)


def write_aged_care_report(report, output):
    from .reporting import write_report_files
    return write_report_files(report, output, 'aged_care', report['rows'], [
        'reference_period', 'service_name', 'provider_name', 'service_suburb', 'state', 'overall_rating',
        'residents_experience_rating', 'compliance_rating', 'staffing_rating', 'quality_measures_rating',
        'rn_target', 'rn_actual', 'rn_shortfall', 'total_target', 'total_actual', 'total_shortfall',
        'decision_type', 'decision_applied', 'decision_ends', 'audit_closed', 'row_identity'], 'Published aged-care quality evidence')

"""APS agency headcount from the APSC 'APS Employment Release Tables' workbook (spec phase 2).

Three tables are read:
  Table 2   all employees by agency and employment category, for two years (headcount, growth, non-ongoing)
  Table 13  all employees by agency and location (state or territory)
  Table 88  all employees by agency and job family ('ICT and Digital Solutions' is one family)
A row starting with '- ' is an agency inside the portfolio of the department row above it; '(NC)' marks an
agency that no longer exists. '.' means nothing was published and is stored as NULL, never as zero.
Every table must add up to its own Total row, or the import stops and nothing is written.
The figures are counts of APS employees only: contractors and labour hire are not in them.
"""
from collections import defaultdict
import csv
from datetime import date, datetime, timedelta
import html
import json
from pathlib import Path
import re
import uuid

from sqlalchemy import text

from .ingest import file_hash
from .parsers import SourceError, name_key

PARSER_REVISION = '1'
DATASET = 'aps_employment'
LICENCES = ['Creative Commons Attribution 4.0 International']
TABLES = {'headcount': 'Table 2', 'location': 'Table 13', 'job_family': 'Table 88'}
ICT_FAMILY = 'ICT and Digital Solutions'
NO_DATA = 'No Data'
OUR_TAGS = ('ict_labour', 'ict_services', 'recruitment', 'labour_hire_general', 'healthcare_staffing', 'nursing_agency')


def verified_workbook(manifest):
    manifest = Path(manifest).resolve(strict=True)
    data = json.loads(manifest.read_text(encoding='utf-8'))
    if data.get('status') != 'complete':
        raise SourceError('Acquisition manifest is not complete')
    entries = [f for f in data['files'] if f['dataset'] == DATASET and f.get('status') == 'complete'
               and Path(f['path']).suffix.lower() == '.xlsx']
    if len(entries) != 1:
        raise SourceError('Expected exactly one APS workbook')
    item = entries[0]
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
    dataset = next(d for d in data['datasets'] if d['key'] == DATASET)
    return path, dict(sha256=item['sha256'], retrieved_at=retrieved, source_url=dataset['source_url'],
                      download_url=item.get('download_url') or dataset['source_url'],
                      licence=item['licence'], licence_url=item['licence_url'])


def _number(value, where):
    if value is None or (isinstance(value, str) and value.strip() in ('', '.')):
        return None
    if isinstance(value, bool):
        raise SourceError('Unexpected value in ' + where)
    if isinstance(value, (int, float)) and value < 0:
        raise SourceError('Negative count in ' + where)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r'[0-9][0-9,]*', value.strip()):
        return int(value.strip().replace(',', ''))
    raise SourceError('Unexpected value in ' + where)


def _table(workbook, key):
    """Return (title, column labels, [(agency, values)], total values) for one agency table."""
    name = TABLES[key]
    if name not in workbook.sheetnames:
        raise SourceError(name + ' is missing')
    rows = [list(r) for r in workbook[name].iter_rows(values_only=True)]
    title = str(rows[0][0] if rows and rows[0] else '')
    if not title.startswith(name + ':') or 'agency' not in title.lower():
        raise SourceError(name + ' title changed')
    header = next((i for i, r in enumerate(rows[:10]) if r and str(r[0] or '').strip() == 'Agency'), None)
    if header is None or header + 1 >= len(rows):
        raise SourceError(name + ' has no Agency header')
    top, sub = rows[header], rows[header + 1]
    width = max(len(top), len(sub))
    top, sub = top + [None] * (width - len(top)), sub + [None] * (width - len(sub))
    labels, group = [], ''
    for i in range(1, width):
        if top[i] not in (None, ''):
            group = str(top[i]).strip()
        labels.append((group, '' if sub[i] is None else str(sub[i]).strip()))
    while labels and labels[-1] == ('', ''):
        labels.pop()
    data, total = [], None
    for r in rows[header + 2:]:
        first = '' if not r or r[0] is None else str(r[0]).strip()
        if not first:
            break
        cells = (list(r[1:]) + [None] * len(labels))[:len(labels)]
        values = [_number(v, name + ' row ' + first) for v in cells]
        if first == 'Total':
            total = values
            break
        if any(agency == first for agency, _ in data):
            raise SourceError(name + ' has duplicate agency rows')
        data.append((first, values))
    if total is None or not data:
        raise SourceError(name + ' has no Total row')
    for column, label in enumerate(labels):
        added = sum(v[column] or 0 for _, v in data)
        if total[column] is not None and added != total[column]:
            raise SourceError(name + ' does not add up to its Total row in column ' + ' '.join(label).strip())
    return title, labels, data, total


def parse_aps(path):
    import openpyxl
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        title, labels, data, total = _table(workbook, 'headcount')
        years = re.search(r'30 June (\d{4}) and 30 June (\d{4})', title)
        if not years or int(years[2]) != int(years[1]) + 1:
            raise SourceError('Table 2 years not found in its title')
        prev, year = years[1], years[2]
        expected = [('Ongoing', prev), ('Ongoing', year), ('Non-ongoing', prev), ('Non-ongoing', year), ('Total', prev), ('Total', year)]
        if labels != expected:
            raise SourceError('Table 2 columns changed')
        extra = {}
        for key in ('location', 'job_family'):
            t, lab, rows, _ = _table(workbook, key)
            if lab[-1] != ('Total', '') or year not in t:
                raise SourceError(TABLES[key] + ' layout or year changed')
            if key == 'job_family' and ICT_FAMILY not in [l[1] for l in lab]:
                raise SourceError('Table 88 no longer has the ' + ICT_FAMILY + ' job family')
            extra[key] = {agency: (dict(zip([l[1] for l in lab[:-1]], values[:-1])), values[-1]) for agency, values in rows}
    finally:
        workbook.close()
    records, portfolio, metrics = [], None, defaultdict(int)
    known = {agency for agency, _ in data}
    for key in extra:
        if set(extra[key]) - known:
            raise SourceError(TABLES[key] + ' names agencies that Table 2 does not')
    for number, (agency, values) in enumerate(data, 1):
        ceased = agency.endswith('(NC)')
        is_sub = agency.startswith('- ')
        clean = re.sub(r'\s*\(NC\)$', '', agency[2:].strip() if is_sub else agency).strip()
        if not is_sub:
            portfolio = None if ceased else clean
        if is_sub and portfolio is None:
            raise SourceError('Table 2 lists an agency before any department row')
        issues = []
        location, location_total = extra['location'].get(agency, ({}, None))
        families, family_total = extra['job_family'].get(agency, ({}, None))
        for label, check in (('location', location_total), ('job_family', family_total)):
            if check is not None and values[5] is not None and check != values[5]:
                issues.append(label + '_total_differs_from_headcount')
        if not ceased and agency not in extra['location']:
            issues.append('missing_from_location_table')
        if not ceased and agency not in extra['job_family']:
            issues.append('missing_from_job_family_table')
        metrics['agencies'] += 1
        metrics['ceased'] += ceased
        metrics['issues'] += len(issues)
        records.append(dict(row_number=number, agency=clean, agency_key=name_key(clean), portfolio=portfolio,
                            is_department_row=not is_sub and not ceased, ceased=ceased,
                            **dict(zip(['ongoing_prev', 'ongoing', 'non_ongoing_prev', 'non_ongoing', 'total_prev', 'total'], values)),
                            by_location=location, by_job_family=families, issues=issues))
    metrics.update(total_prev=total[4], total=total[5])
    return records, dict(metrics), date(int(year), 6, 30), date(int(prev), 6, 30)


def _match(conn, records):
    """Propose the AGOR body for each APS agency; only a single exact match is kept."""
    index = defaultdict(set)
    for row in conn.execute(text("SELECT organisation_id, name_key FROM market.organisation_observations WHERE source='agor' AND kind='body'")):
        index[row.name_key].add(row.organisation_id)
    for r in records:
        found, basis = index.get(r['agency_key'], set()), 'exact_name'
        if r['is_department_row'] and len(found) != 1:
            keys = [name_key(f) for f in ('Department of ' + r['agency'], 'Department of the ' + r['agency'], r['agency'] + ' Department')]
            found, basis = set().union(*(index.get(k, set()) for k in keys)), 'department_name'
        if len(found) == 1:
            r.update(organisation_id=next(iter(found)), match_basis=basis)
        else:
            r.update(organisation_id=None, match_basis='ambiguous' if found else 'unmatched')


def import_aps(engine, manifest):
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,'aps','running')"), {'id': run_id})
    try:
        path, meta = verified_workbook(manifest)
        records, metrics, as_at, prev_as_at = parse_aps(path)
        if file_hash(path) != meta['sha256']:
            raise SourceError('Resource changed while parsing')
        with engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('market:aps'))"))
            existing = conn.execute(text('SELECT id FROM market.aps_snapshots WHERE sha256=:sha256 AND parser_revision=:revision'),
                                    {'sha256': meta['sha256'], 'revision': PARSER_REVISION}).scalar()
            if existing:
                status, snapshot_id = 'unchanged', existing
            else:
                status, snapshot_id = 'complete', uuid.uuid4()
                _match(conn, records)
                metrics.update(matched=sum(r['organisation_id'] is not None for r in records))
                conn.execute(text('''INSERT INTO market.aps_snapshots(id,sha256,parser_revision,as_at,prev_as_at,source_url,download_url,
                    licence,licence_url,retrieved_at,row_count,metrics) VALUES(:id,:sha256,:revision,:as_at,:prev,:source_url,:download_url,
                    :licence,:licence_url,:retrieved_at,:rows,CAST(:metrics AS jsonb))'''),
                    dict(meta, id=snapshot_id, revision=PARSER_REVISION, as_at=as_at, prev=prev_as_at, rows=len(records), metrics=json.dumps(metrics)))
                conn.execute(text('''INSERT INTO market.aps_agencies(snapshot_id,row_number,agency,agency_key,portfolio,is_department_row,ceased,
                    ongoing_prev,ongoing,non_ongoing_prev,non_ongoing,total_prev,total,by_location,by_job_family,organisation_id,match_basis,issues)
                    VALUES(:snapshot_id,:row_number,:agency,:agency_key,:portfolio,:is_department_row,:ceased,:ongoing_prev,:ongoing,
                    :non_ongoing_prev,:non_ongoing,:total_prev,:total,CAST(:by_location AS jsonb),CAST(:by_job_family AS jsonb),
                    :organisation_id,:match_basis,CAST(:issues AS jsonb))'''),
                    [dict(r, snapshot_id=snapshot_id, by_location=json.dumps(r['by_location']), by_job_family=json.dumps(r['by_job_family']),
                          issues=json.dumps(r['issues'])) for r in records])
                # Never roll the current pointer back to an older release imported later.
                conn.execute(text('''INSERT INTO market.current_aps_snapshot(snapshot_id) VALUES(:id)
                    ON CONFLICT(singleton) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
                    WHERE (SELECT (as_at,retrieved_at,imported_at) FROM market.aps_snapshots WHERE id=EXCLUDED.snapshot_id)
                        > (SELECT (as_at,retrieved_at,imported_at) FROM market.aps_snapshots WHERE id=market.current_aps_snapshot.snapshot_id)'''),
                    {'id': snapshot_id})
            conn.execute(text('''UPDATE market.pipeline_runs SET finished_at=now(),status=:status,snapshot_id=:snapshot,
                metrics=CAST(:metrics AS jsonb) WHERE id=:id'''), dict(status=status, snapshot=snapshot_id, metrics=json.dumps(metrics), id=run_id))
        return dict(run_id=str(run_id), snapshot_id=str(snapshot_id), status=status, as_at=str(as_at), metrics=metrics)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.pipeline_runs SET finished_at=now(),status='failed',error_code=:error WHERE id=:id"),
                         {'id': run_id, 'error': type(exc).__name__})
        raise


def build_aps_report(engine, as_of):
    """Each APS agency's size, growth, ICT workforce and what it spends on our categories."""
    if not isinstance(as_of, date):
        raise ValueError('Explicit as-of date required')
    d12 = as_of - timedelta(days=365)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        agencies = [dict(r) for r in conn.execute(text('''SELECT * FROM market.current_aps_agencies WHERE NOT ceased ORDER BY row_number''')).mappings()]
        snapshot = conn.execute(text('''SELECT s.* FROM market.aps_snapshots s
            JOIN market.current_aps_snapshot c ON c.snapshot_id=s.id''')).mappings().first()
        spend = {r['organisation_id']: dict(r) for r in conn.execute(text('''
            SELECT COALESCE(r.confirmed_organisation_id, r.organisation_id) AS organisation_id, count(*) AS contracts,
                   sum(c.original_value) AS value, bool_and(r.review_status = 'approved') AS reviewed
            FROM market.current_analytical_contracts c JOIN market.buyer_resolution r ON r.buyer_key=c.buyer_key
            WHERE c.first_published > :d12 AND c.first_published <= :asof AND c.payload->>'category_tag' = ANY(:tags)
              AND r.review_status NOT IN ('rejected','needs_recheck') AND NOT r.register_context_stale
            GROUP BY 1'''), {'d12': d12, 'asof': as_of, 'tags': list(OUR_TAGS)}).mappings()}
    if not agencies:
        raise ValueError('No APS snapshot imported')
    out = []
    for a in agencies:
        fam = a['by_job_family'] or {}
        known = sum(v or 0 for k, v in fam.items() if k != NO_DATA)
        ict = fam.get(ICT_FAMILY)
        s = spend.get(a['organisation_id']) if a['organisation_id'] else None
        value = float(s['value']) if s and s['value'] is not None else None
        out.append(dict(
            agency=a['agency'], portfolio=a['portfolio'], department=a['is_department_row'], organisation_id=a['organisation_id'],
            match_basis=a['match_basis'], headcount=a['total'], headcount_prev=a['total_prev'],
            growth=round(a['total'] / a['total_prev'] - 1, 4) if a['total'] is not None and a['total_prev'] else None,
            non_ongoing_share=round(a['non_ongoing'] / a['total'], 4) if a['total'] and a['non_ongoing'] is not None else None,
            ict_digital=ict, job_family_coverage=round(known / a['total'], 4) if a['total'] else None,
            ict_share_of_known=round(ict / known, 4) if ict is not None and known else None,
            largest_location=max(((k, v) for k, v in (a['by_location'] or {}).items() if v), key=lambda kv: kv[1], default=(None, None))[0],
            our_category_contracts_12m=s['contracts'] if s else None, our_category_value_12m=value,
            our_category_value_per_employee=round(value / a['total']) if value is not None and a['total'] else None,
            identity_reviewed=bool(s and s['reviewed']), issues=a['issues']))
    out.sort(key=lambda r: -(r['headcount'] or 0))
    as_at = agencies[0]['as_at']
    return dict(as_of=as_of, status='draft_for_review', headcount_as_at=as_at, comparison_as_at=agencies[0]['prev_as_at'],
                provenance={k: snapshot[k] for k in ('id','sha256','parser_revision','source_url','download_url','licence','licence_url','retrieved_at')},
                headcount_age_days=(as_of-as_at).days,
                agencies=out,
                summary=dict(agencies=len(out), matched_to_register=sum(r['organisation_id'] is not None for r in out),
                             with_our_category_spend=sum(r['our_category_value_12m'] is not None for r in out),
                             headcount=sum(r['headcount'] or 0 for r in out)),
                limitations=['Headcounts are APS employees on ' + str(as_at) + ' (APSC); contractors and labour hire are not counted.',
                             "Job family is not reported for every employee ('No Data'); ICT shares use the employees with a job family.",
                             'Spend joins APS agencies to AusTender buyers through machine-proposed identities until people approve them.',
                             'A department row covers the department only; agencies in its portfolio are listed separately.'])


def write_aps_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'aps_agencies.json').write_text(json.dumps(report, default=str, indent=2), encoding='utf-8')
    fields = ['agency', 'portfolio', 'headcount', 'headcount_prev', 'growth', 'non_ongoing_share', 'ict_digital', 'ict_share_of_known',
              'job_family_coverage', 'largest_location', 'our_category_contracts_12m', 'our_category_value_12m', 'our_category_value_per_employee',
              'match_basis']
    with open(directory/'aps_agencies.csv', 'w', newline='', encoding='utf-8') as stream:
        w = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(report['agencies'])
    esc = lambda v: html.escape(str(v if v is not None else 'Unknown'))
    pct = lambda v: 'Unknown' if v is None else '{:.0%}'.format(v)
    money = lambda v: 'None found' if v is None else '${:,.0f}'.format(v)
    rows = ''.join('<tr><td>' + esc(r['agency']) + '</td><td>' + esc(r['portfolio']) + '</td><td>' + esc(r['headcount']) + '</td><td>'
                   + pct(r['growth']) + '</td><td>' + pct(r['non_ongoing_share']) + '</td><td>' + esc(r['ict_digital']) + ' (' + pct(r['job_family_coverage'])
                   + ' coverage)</td><td>' + money(r['our_category_value_12m']) + '</td><td>' + money(r['our_category_value_per_employee']) + '</td></tr>'
                   for r in report['agencies'])
    (directory/'aps_agencies.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>APS agencies</title><style>body{font:15px/1.5 system-ui;max-width:1200px;margin:32px auto;padding:0 20px;color:#163144;'
        'background:#f4f6f8;overflow-wrap:anywhere}.table-wrap{overflow-x:auto}table{border-collapse:collapse;background:white;width:100%;min-width:1100px}th,td{min-width:110px;padding:6px 10px;'
        'border:1px solid #d9e0e5;text-align:left;vertical-align:top}</style><h1>APS agencies: size and our-category spend</h1><p>Draft for internal review · As of '
        + esc(report['as_of']) + ' · headcount at ' + esc(report['headcount_as_at']) + '</p><p>' + esc(' '.join(report['limitations'])) + '</p>'
        '<p>On smaller screens, scroll the table horizontally to read every column.</p><div class="table-wrap"><table><tr><th>Agency</th><th>Portfolio</th><th>APS staff</th><th>Change in a year</th><th>Non-ongoing</th><th>ICT and digital staff</th>'
        '<th>Our categories, 12 months</th><th>Per APS employee</th></tr>' + rows + '</table></div></html>', encoding='utf-8')
    return {'agencies': len(report['agencies']), 'json': str(directory/'aps_agencies.json'), 'html': str(directory/'aps_agencies.html')}

"""Official JSA workbook parsers; no business role mapping is assumed."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import io
import re
import uuid
import zipfile

from sqlalchemy import text

from .ingest import file_hash
from .parsers import SourceError
from .resources import verified_resource

REVISION = '1'
SOURCES = ('jsa_ivi', 'jsa_shortages', 'jsa_projections', 'jsa_reos', 'jsa_tnv')
REGIONS = {'AUST','NSW','VIC','QLD','SA','WA','TAS','NT','ACT'}


def number(value):
    if value is None or str(value).strip() in ('', '.', '-', 'np', 'n.p.'):
        return None
    try:
        result = Decimal(str(value).replace(',', ''))
    except InvalidOperation:
        raise SourceError('Unexpected numeric value') from None
    if not result.is_finite():
        raise SourceError('Non-finite numeric value')
    return result


def code(value, digits=4):
    if isinstance(value, (int,float)) and not isinstance(value,bool):
        value = str(int(value)) if value == int(value) else str(value)
    value = str(value or '').strip()
    return value if re.fullmatch(r'\d{' + str(digits) + '}', value) else None


def observation(classification, occupation, title, region, period, measure, value=None, rating=None, **payload):
    identity = [classification, occupation, title, region, str(period), measure]
    return dict(observation_key=hashlib.sha256(json.dumps(identity).encode()).hexdigest(), classification=classification,
                occupation_code=occupation, occupation_title=title, region=region, period=period, measure=measure,
                value=value, rating=rating, payload=payload)


def parse_ivi(wb):
    sheet = wb['4 digit 3 month average']
    iterator = sheet.iter_rows(values_only=True)
    header = next(iterator)
    if tuple(header[:3]) != ('ANZSCO_CODE','ANZSCO_TITLE','state'):
        raise SourceError('IVI columns changed')
    periods = [v.date() if isinstance(v,datetime) else v for v in header[3:] if v is not None]
    if len(periods)<13 or not all(isinstance(d,date) and d.day==1 for d in periods):
        raise SourceError('IVI monthly date columns missing')
    if periods != sorted(set(periods)):
        raise SourceError('IVI monthly columns duplicate or unordered')
    # The complete published history stays in the immutable workbook. Keep 13 months for an exact year-on-year comparison.
    first = len(periods)-13
    out=[]
    for row_number,row in enumerate(iterator,2):
        if not any(v is not None for v in row): continue
        occupation = code(row[0])
        if not occupation and str(row[0]) not in ('.','0'): raise SourceError('Unexpected IVI occupation code')
        if str(row[0])=='0': continue  # national/state totals are not additive with occupations
        title,region=str(row[1] or '').strip(),str(row[2] or '').strip()
        if not title or region not in REGIONS: raise SourceError('Unexpected IVI title or region')
        for i in range(first,len(periods)):
            val=number(row[i+3] if i+3<len(row) else None)
            if val is not None and val<0: raise SourceError('Negative job advertisements')
            out.append(observation('ANZSCO (IVI published series)',occupation,title,region,periods[i],
                                   'online_ads_3m_average',val, sheet=sheet.title,row=row_number,
                                   classification_edition='not asserted'))
    return out,periods[-1]


def parse_shortages(wb):
    sheets=[s for s in wb if re.fullmatch(r'\d{4} Unit group Shortage List',s.title)]
    if len(sheets)!=1: raise SourceError('Shortage unit-group sheet changed')
    sheet=sheets[0];year=int(sheet.title[:4]); rows=list(sheet.iter_rows(values_only=True))
    start=next((i for i,r in enumerate(rows[:15]) if r and r[0]=='Unit group Code'),None)
    if start is None or 'ANZSCO 2022' not in ' '.join(str(r[0]) for r in rows[:start] if r):
        raise SourceError('Shortage classification/header changed')
    out=[]
    for i,row in enumerate(rows[start+1:],start+2):
        if not any(v is not None for v in row): continue
        occupation=code(row[0])
        if not occupation: raise SourceError('Unexpected shortage occupation code')
        rating=str(row[2] or '').strip()
        if rating not in ('NS','S','R','M'): raise SourceError('Unknown shortage rating')
        out.append(observation('ANZSCO 2022',occupation,str(row[1]).strip(),'AUST',date(year,1,1),'shortage_rating',
                               rating=rating, sheet=sheet.title,row=i,period_basis='annual assessment; not a January observation',
                               shortage_driver=row[3]))
    return out,date(year,1,1)


def parse_projections(wb):
    sheet=wb['Table_6 Occupation Unit Group'];rows=list(sheet.iter_rows(values_only=True))
    start=next((i for i,r in enumerate(rows[:15]) if r and r[0]=='Occupation Level'),None)
    if start is None or list(rows[start][2:8])!=['ANZSCO Code','Occupation','Skill level 1','Baseline','Projected','Projected']:
        raise SourceError('Employment projection columns changed')
    labels=rows[start+1]
    periods=[]
    for value in labels[5:8]:
        m=re.fullmatch(r"May (\d{4}) \('000\)",str(value))
        if not m: raise SourceError('Projection period/units changed')
        periods.append(date(int(m[1]),5,1))
    if not periods[0]<periods[1]<periods[2]: raise SourceError('Projection years out of order')
    out=[]
    for i,row in enumerate(rows[start+2:],start+3):
        if str(row[0])!='4': continue  # aggregate occupation groups would double count
        occupation=code(row[2])
        if not occupation: raise SourceError('Unexpected projection occupation code')
        for j,period in enumerate(periods):
            val=number(row[j+5])
            if val is not None and val<0: raise SourceError('Negative employment')
            out.append(observation('ANZSCO (JSA projections)',occupation,str(row[3]).strip(),'AUST',period,
                                   'employment_baseline' if j==0 else 'employment_projection',
                                   val*1000 if val is not None else None,sheet=sheet.title,row=i,
                                   baseline=str(periods[0]),source_units='thousands; converted to persons',nfd=row[1]=='Y'))
    return out,periods[0]  # latest release is determined by baseline, not its future horizon


def parse_reos(wb):
    specs={'1.1 Recruitment Rate (M)':('recruitment_rate','All employers'),
           '2.1 Increased Staff':('increased_staff_rate','All employers'),
           '2.2 Decreased Staff':('decreased_staff_rate','All employers'),
           '3.1 Recruitment Difficulty (M)':('recruitment_difficulty_rate','Recruiting employers'),
           '4.1 Expected Increase (M)':('expected_staff_increase_rate','All employers'),
           '4.3 Expected Decrease (M)':('expected_staff_decrease_rate','All employers')}
    out=[];latest=None
    for title,(measure,denominator) in specs.items():
        sheet=wb[title];rows=list(sheet.iter_rows(values_only=True,max_col=6,max_row=500))
        start=next((i for i,r in enumerate(rows[:12]) if r[0]=='Month'),None)
        if start is None:raise SourceError('REOS monthly header missing')
        headers=list(rows[start]);columns=range(1,6)
        if denominator=='Recruiting employers':
            if list(rows[start-1][2:4])!=['Capital Cities','Rest of State areas']:raise SourceError('REOS recruiting strata changed')
            headers[2:4]=rows[start-1][2:4];columns=range(1,4)
        elif list(headers[2:6])!=['Capital Cities','Rest of State areas','5 to 19 employees','20 employees or more']:
            raise SourceError('REOS monthly strata changed')
        dated=[(i,r) for i,r in enumerate(rows[start+1:],start+2) if isinstance(r[0],(date,datetime))]
        if not dated:raise SourceError('REOS monthly observations missing')
        dates=[r[0].date() if isinstance(r[0],datetime) else r[0] for _,r in dated]
        if dates!=sorted(set(dates)):raise SourceError('REOS months duplicate or unordered')
        for (row_number,row),period in list(zip(dated,dates))[-13:]:
            latest=max(latest or period,period)
            for col in columns:
                raw=row[col];warning=isinstance(raw,str) and '*' in raw
                if isinstance(raw,str) and '%' in raw:
                    if not re.fullmatch(r'\d+(?:\.\d+)?%\*?',raw.strip()):raise SourceError('Unknown REOS warning format')
                    value=number(raw.strip().rstrip('*%'))/100
                else:value=number(raw)
                if value is not None and not 0<=value<=1:raise SourceError('REOS proportion outside 0..1')
                out.append(observation('REOS published aggregate strata',None,denominator,str(headers[col]),period,measure,value,
                    sheet=title,row=row_number,source_units='proportion',denominator=denominator,
                    low_sample_warning=warning,suppressed=raw=='n.p.',
                    scope='Employers with five or more employees; many government organisations excluded. Strata overlap; do not sum.'))
    return out,latest


def parse_tnv(wb):
    out=[];latest=None
    for title,header in [('Data_1',['region_level','region_code','region_title','date','tnv']),
                         ('Data_2',['date','occupation_code','occupation_level','occupation_title','tnv'])]:
        sheet=wb[title];rows=list(sheet.iter_rows(values_only=True,max_col=5,max_row=20000))
        start=next((i for i,r in enumerate(rows[:15]) if list(r)==header),None)
        if start is None:raise SourceError('TNV long-format columns changed')
        for i,row in enumerate(rows[start+1:],start+2):
            if not any(v is not None for v in row):continue
            period=row[3] if title=='Data_1' else row[0]
            if isinstance(period,datetime):period=period.date()
            if not isinstance(period,date) or period.day!=1:raise SourceError('TNV reference date changed')
            value=number(row[4])
            if value is not None and value<0:raise SourceError('Negative TNV vacancies')
            latest=max(latest or period,period)
            if title=='Data_1':
                level,region,title_value=str(row[0]),str(row[1]),str(row[2]);occupation=None
                classification='TNV published regional aggregate'
            else:
                occupation,level,title_value=str(row[1]),str(row[2]),str(row[3]);region='AUST'
                classification='TNV published '+level
            out.append(observation(classification,occupation,title_value,region,period,'total_new_vacancies',value,
                sheet=title,row=i,aggregation_level=level,source_units='vacancies',
                formatting_normalization='Invalid fractional baseColWidth changed in memory; cell data unchanged',
                scope='Overlapping occupation and region totals; do not sum across levels or treat as online advertisements.'))
    return out,latest


def parse_demand(path,source):
    import openpyxl
    parsers={'jsa_ivi':parse_ivi,'jsa_shortages':parse_shortages,'jsa_projections':parse_projections,'jsa_reos':parse_reos,'jsa_tnv':parse_tnv}
    if source not in parsers: raise ValueError('Unknown demand source')
    resource=path
    if source=='jsa_tnv':
        # The published workbook uses baseColWidth="8.43", invalid for openpyxl's integer field.
        # Repair only this cosmetic XML attribute in memory; retain and hash the unmodified download.
        resource=io.BytesIO()
        with zipfile.ZipFile(path) as original,zipfile.ZipFile(resource,'w',zipfile.ZIP_DEFLATED) as normalized:
            if sum(f.file_size for f in original.infolist())>100_000_000:raise SourceError('Workbook expansion limit')
            for item in original.infolist():
                body=original.read(item.filename)
                if item.filename.startswith('xl/worksheets/') and item.filename.endswith('.xml'):
                    body=body.replace(b'baseColWidth="8.43"',b'baseColWidth="8"')
                normalized.writestr(item.filename,body)
        resource.seek(0)
    wb=openpyxl.load_workbook(resource,read_only=True,data_only=True)
    try: records,period=parsers[source](wb)
    except (KeyError,IndexError,StopIteration) as exc: raise SourceError('Workbook structure changed') from exc
    finally: wb.close()
    if not records: raise SourceError('Source returned zero observations')
    if len({r['observation_key'] for r in records})!=len(records): raise SourceError('Duplicate demand observations')
    return records,period


def import_demand(engine,manifest,source):
    selectors={'jsa_ivi':lambda f:'anzsco4_occupations_states_and_territories' in f['path'],
               'jsa_shortages':lambda f:'4_digit_anzsco' in f['path'],
               'jsa_projections':lambda f:f['path'].endswith('.xlsx'),
               'jsa_reos':lambda f:f['path'].endswith('.xlsx'),'jsa_tnv':lambda f:f['path'].endswith('.xlsx')}
    if source not in selectors: raise ValueError('Unknown demand source')
    run=uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,:source,'running')"),{'id':run,'source':source})
    try:
        path,meta=verified_resource(manifest,source,selectors[source])
        rows,period=parse_demand(path,source)
        if file_hash(path)!=meta['sha256']: raise SourceError('Source changed while parsing')
        metrics={'observations':len(rows),'unknown_values':sum(r['value'] is None and r['rating'] is None for r in rows),
                 'occupations':len({r['occupation_title'] for r in rows}),'regions':sorted({r['region'] for r in rows})}
        with engine.begin() as conn:
            conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'market:'+source})
            existing=conn.execute(text('SELECT id FROM market.demand_snapshots WHERE source=:source AND sha256=:sha256 AND parser_revision=:revision'),dict(meta,revision=REVISION)).scalar()
            snapshot=existing or uuid.uuid4();status='unchanged' if existing else 'complete'
            if not existing:
                conn.execute(text('''INSERT INTO market.demand_snapshots(id,source,sha256,parser_revision,source_url,download_url,licence,
                    licence_url,retrieved_at,period_end,row_count,metrics) VALUES(:id,:source,:sha256,:revision,:source_url,:download_url,
                    :licence,:licence_url,:retrieved_at,:period,:count,CAST(:metrics AS jsonb))'''),
                    dict(meta,id=snapshot,revision=REVISION,period=period,count=len(rows),metrics=json.dumps(metrics)))
                for start in range(0,len(rows),4000):
                    conn.execute(text('''INSERT INTO market.demand_observations(snapshot_id,observation_key,classification,occupation_code,
                        occupation_title,region,period,measure,value,rating,payload) VALUES(:snapshot_id,:observation_key,:classification,
                        :occupation_code,:occupation_title,:region,:period,:measure,:value,:rating,CAST(:payload AS jsonb))'''),
                        [dict(r,snapshot_id=snapshot,payload=json.dumps(r['payload'])) for r in rows[start:start+4000]])
                conn.execute(text('''INSERT INTO market.current_demand_snapshots(source,snapshot_id) VALUES(:source,:snapshot)
                    ON CONFLICT(source) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
                    WHERE (SELECT (period_end,retrieved_at,imported_at) FROM market.demand_snapshots WHERE id=EXCLUDED.snapshot_id)
                        > (SELECT (period_end,retrieved_at,imported_at) FROM market.demand_snapshots WHERE id=market.current_demand_snapshots.snapshot_id)'''),
                    {'source':source,'snapshot':snapshot})
            conn.execute(text('''UPDATE market.pipeline_runs SET status=:status,finished_at=now(),snapshot_id=:snapshot,
                metrics=CAST(:metrics AS jsonb) WHERE id=:id'''),dict(status=status,snapshot=snapshot,metrics=json.dumps(metrics),id=run))
        return dict(status=status,snapshot_id=str(snapshot),metrics=metrics,period_end=str(period))
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.pipeline_runs SET status='failed',finished_at=now(),error_code=:e WHERE id=:id"),{'id':run,'e':type(exc).__name__})
        raise


def build_demand(engine,as_of):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        snapshots=[dict(r) for r in conn.execute(text('''SELECT s.* FROM market.demand_snapshots s
            JOIN market.current_demand_snapshots c ON c.snapshot_id=s.id''')).mappings()]
        rows=[dict(r) for r in conn.execute(text('SELECT * FROM market.current_demand')).mappings()]
    if not rows: raise ValueError('No demand data imported')
    ivi={}
    for r in rows:
        if r['source']=='jsa_ivi' and r['period']<=as_of:
            ivi.setdefault((r['occupation_code'],r['occupation_title'],r['region']),[]).append(r)
    trends=[]
    for (occupation,title,region),items in ivi.items():
        items.sort(key=lambda r:r['period']);latest=items[-1]
        prev=next((r for r in items if r['period']==date(latest['period'].year-1,latest['period'].month,1)),None)
        val,old=latest['value'],prev['value'] if prev else None
        trends.append(dict(occupation_code=occupation,occupation_title=title,region=region,period=latest['period'],
                           ads_3m_average=val,ads_year_earlier=old,year_on_year=float(val/old-1) if val is not None and old else None,
                           snapshot_id=str(latest['snapshot_id']),source_url=latest['source_url'],role_family='unmapped'))
    trends.sort(key=lambda r:-(r['ads_3m_average'] or 0))
    return dict(as_of=as_of,status='research; role mappings awaiting review',snapshots=snapshots,vacancy_trends=trends,
                shortage_ratings=[r for r in rows if r['source']=='jsa_shortages' and r['period']<=as_of],
                employment_projections=[r for r in rows if r['source']=='jsa_projections'],
                recruitment_survey=[r for r in rows if r['source']=='jsa_reos' and r['period']<=as_of],
                total_new_vacancies=[r for r in rows if r['source']=='jsa_tnv' and r['period']<=as_of],
                limitations=['IVI four-digit figures are three-month average online advertisements, not vacancies, placements or all labour demand.',
                             'Suppressed/missing values remain unknown; the latest 13 published months are retained for year-on-year comparison.',
                             'National and state observations overlap and must not be added together.',
                             'Shortage ratings are national annual assessments, not state-specific ratings.',
                             'Occupation classifications are kept separate; no unverified cross-edition code joins or role mappings.',
                             'Employment projections are indicative scenarios; baseline and future observations are labelled separately.',
                             'REOS covers employers with five or more employees and excludes many government organisations; proportions retain denominators and suppression.',
                             'TNV combines IVI and REOS evidence; do not add it to IVI. Published occupation and regional aggregates overlap. Prefer annual comparisons.',
                             'Illoura bench availability and clearance fit are unknown until an aggregate bench export is imported.'])


def write_demand(report,directory):
    from .reporting import write_report_files
    for key,title in [('recruitment_survey','Recruitment survey proportions'),('total_new_vacancies','Total new vacancies')]:
        write_report_files(dict(as_of=report['as_of'],limitations=report['limitations'],observations=report[key]),directory,key,report[key],
            ['source','classification','occupation_title','region','period','measure','value','payload'],title)
    return write_report_files(report,directory,'demand',report['vacancy_trends'],
                              ['occupation_title','region','period','ads_3m_average','year_on_year','role_family'],
                              'Workforce demand indicators')

"""Load existing ABS boundaries without inventing postcode or remoteness correspondences."""
from contextlib import contextmanager
from array import array
from collections import Counter, defaultdict
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
from pathlib import Path
import uuid
import zipfile

from sqlalchemy import text

from .ingest import file_hash
from .parsers import SourceError
from .resources import verified_resource
from .reporting import write_report_files

LAYERS=('SA4','LGA','POA')


@contextmanager
def boundary_reader(path,layer):
    import shapefile
    if layer not in LAYERS:raise ValueError('Unknown ABS boundary layer')
    with zipfile.ZipFile(path) as archive:
        def member(suffix):
            matches=[n for n in archive.namelist() if n.lower().endswith(suffix)]
            if len(matches)!=1:raise SourceError('Expected one boundary '+suffix+' member')
            return archive.read(matches[0])
        prj=member('.prj').decode('utf-8-sig')
        if not prj.startswith('GEOGCS["GDA2020"'):raise SourceError('Boundary coordinate system changed')
        cpgs=[n for n in archive.namelist() if n.lower().endswith('.cpg')]
        encoding=archive.read(cpgs[0]).decode().strip() if len(cpgs)==1 else 'utf-8'
        if encoding.casefold() not in ('utf-8','utf8','1252','cp1252'):raise SourceError('Boundary text encoding needs review')
        if encoding=='1252':encoding='cp1252'
        with shapefile.Reader(shp=io.BytesIO(member('.shp')),shx=io.BytesIO(member('.shx')),dbf=io.BytesIO(member('.dbf')),encoding=encoding) as reader:
            fields={r[0] for r in reader.fields[1:]}
            if not {layer+'_CODE26',layer+'_NAME26','AREASQKM26'}<=fields:raise SourceError('ABS boundary fields changed')
            if not len(reader):raise SourceError('Empty boundary source')
            yield reader,prj


def feature(record,layer):
    properties=record.record.as_dict()
    code=str(properties[layer+'_CODE26']);name=str(properties[layer+'_NAME26'])
    geometry=None if record.shape.shapeType==0 else record.shape.__geo_interface__
    return dict(code=code,name=name,state_code=properties.get('STE_CODE26'),properties=json.dumps(properties,allow_nan=False),
                geometry=json.dumps(geometry,allow_nan=False) if geometry else None)


def import_geography(engine,manifest,layer):
    path,meta=verified_resource(manifest,'abs_geography',lambda f:f['path'].rsplit('/',1)[-1].startswith(layer+'_2026_') and f['path'].endswith('.zip'))
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'market:geography:'+layer})
        existing=conn.execute(text('SELECT id FROM market.geography_snapshots WHERE layer=:layer AND sha256=:sha256'),dict(meta,layer=layer)).scalar()
        if existing:return dict(status='unchanged',snapshot_id=str(existing),layer=layer)
        with boundary_reader(path,layer) as (reader,prj):
            snapshot=uuid.uuid4();count=len(reader)
            conn.execute(text('''INSERT INTO market.geography_snapshots(id,layer,edition,sha256,source_url,download_url,licence,
                licence_url,retrieved_at,crs_wkt,row_count) VALUES(:id,:layer,'ASGS Edition 4 (2026)',:sha256,:source_url,:download_url,
                :licence,:licence_url,:retrieved_at,:prj,:count)'''),dict(meta,id=snapshot,layer=layer,prj=prj,count=count))
            batch=[];written=0
            for record in reader.iterShapeRecords():
                batch.append(dict(feature(record,layer),snapshot=snapshot))
                if len(batch)>=50:
                    _write(conn,batch);written+=len(batch);batch=[]
            if batch:_write(conn,batch);written+=len(batch)
            if written!=count or file_hash(path)!=meta['sha256']:raise SourceError('Boundary rows or checksum changed')
        conn.execute(text('''INSERT INTO market.current_geography_snapshots(layer,snapshot_id) VALUES(:layer,:id)
            ON CONFLICT(layer) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
            WHERE (SELECT retrieved_at FROM market.geography_snapshots WHERE id=EXCLUDED.snapshot_id)>
                  (SELECT retrieved_at FROM market.geography_snapshots WHERE id=market.current_geography_snapshots.snapshot_id)'''),dict(layer=layer,id=snapshot))
        conn.execute(text('''INSERT INTO market.pipeline_runs(id,source,status,snapshot_id,finished_at,metrics)
            VALUES(:run,:source,'complete',:snapshot,now(),CAST(:metrics AS jsonb))'''),dict(run=uuid.uuid4(),source='abs_'+layer.lower(),snapshot=snapshot,metrics=json.dumps({'features':count})))
    return dict(status='complete',snapshot_id=str(snapshot),layer=layer,features=count,crs='GDA2020 geographic; native coordinates')


def _write(conn,rows):
    conn.execute(text('''INSERT INTO market.geography_features(snapshot_id,code,name,state_code,properties,geometry)
        VALUES(:snapshot,:code,:name,:state_code,CAST(:properties AS jsonb),CAST(:geometry AS jsonb))'''),rows)


def build_geography(engine,as_of):
    with engine.connect() as conn:
        rows=[dict(r) for r in conn.execute(text('''SELECT s.layer,s.edition,s.row_count,s.source_url,s.licence,s.retrieved_at,s.sha256,
            count(*) FILTER(WHERE f.geometry IS NULL) AS missing_geometry
            FROM market.current_geography_snapshots c JOIN market.geography_snapshots s ON s.id=c.snapshot_id
            JOIN market.geography_features f ON f.snapshot_id=s.id GROUP BY s.id ORDER BY s.layer''')).mappings()]
    return dict(as_of=as_of,status='reference boundaries loaded; no inferred delivery remoteness',layers=rows,
        limitations=['Coordinates are stored in native GDA2020 geographic CRS with the full source WKT; no WGS84 transformation is asserted.',
                     'Postal Areas approximate postcodes and can cross boundaries. No postcode-to-region or delivery-remoteness allocation is inferred.',
                     'These SA4, LGA and POA files do not contain remoteness areas. Remote-area procurement eligibility remains unknown.',
                     'JSA geographic editions must be checked before joining region codes to 2026 boundaries.'])


def write_geography(report,directory):
    return write_report_files(report,directory,'geography_layers',report['layers'],
        ['layer','edition','row_count','missing_geometry','retrieved_at','source_url'],'ABS boundary references')


REMOTENESS_REVISION = 'abs-remoteness-2'
EDITION_CHAIN = 'RA 2021 (ASGS Edition 3) -> official CG SA1 2021-2026 -> ASGS Edition 4 allocation 2026'
REMOTENESS_READY = 'Derived reference only; official Edition 4 remoteness is expected March 2028 (release timing may change).'
REMOTENESS_LIMITATIONS = [REMOTENESS_READY,
    'FROM ratios are shares of each 2021 source population, not shares of the 2026 destination. Population counts are unavailable.',
    'Regional shares are AREA shares of allocated mesh blocks, never population shares. Mixed and unknown area remain separate buckets.',
    'Possible RA class presence in mixed SA1s does not locate those classes within individual mesh blocks.',
    'Blank-destination CG rows (BMOS_NULL_FLAG 4) are retained as source evidence and in FROM totals, without an inferred destination.',
    'Individual CG links include Acceptable and Poor quality ratings even where overall quality is Good; contributor evidence retains both ratings.',
    'Zero FROM ratios are retained in official-link class consensus; they do not establish population presence or population shares.',
    'Non-spatial units are separate; zero-area shares are null. No procurement eligibility or delivery location is inferred.',
    'Source: Australian Bureau of Statistics, Commonwealth of Australia; CC BY 4.0.']

_TAIL = ['STATE_CODE_2026','STATE_NAME_2026','AUS_CODE_2026','AUS_NAME_2026','AREA_ALBERS_SQKM']
_HIERARCHY = ['SA2_CODE_2026','SA2_NAME_2026','SA3_CODE_2026','SA3_NAME_2026','SA4_CODE_2026',
              'SA4_NAME_2026','GCCSA_CODE_2026','GCCSA_NAME_2026']
REMOTENESS_HEADERS = {
    'ra': ['SA1_CODE_2021','RA_CODE_2021','RA_NAME_2021','STATE_CODE_2021','STATE_NAME_2021',
           'AUS_CODE_2021','AUS_NAME_2021','AREA_ALBERS_SQKM','ASGS_LOCI_URI_2021'],
    'cg': ['SA1_CODE_2021','SA1_CODE_2026','RATIO_FROM_TO','INDIV_TO_REGION_QLTY_INDICATOR',
           'OVERALL_QUALITY_INDICATOR','BMOS_NULL_FLAG'],
    'sa1': ['SA1_CODE_2026','CHANGE_FLAG_2026','CHANGE_LABEL_2026'] + _HIERARCHY + _TAIL,
    'mb': ['MB_CODE_2026','MB_CATEGORY_2026','CHANGE_FLAG_2026','CHANGE_LABEL_2026','SA1_CODE_2026'] + _HIERARCHY + _TAIL,
    'lga': ['MB_CODE_2026','LGA_CODE_2026','LGA_NAME_2026'] + _TAIL,
    'poa': ['MB_CODE_2026','POA_CODE_2026','POA_NAME_2026','AUS_CODE_2026','AUS_NAME_2026','AREA_ALBERS_SQKM'],
}
REMOTENESS_FILES = {
    'ra': ('abs_remoteness_2021','RA_2021_AUST.xlsx','SA1_RA_2021_AUST'),
    'cg': ('abs_correspondence_2021_2026','CG_SA1_2021_SA1_2026.csv',None),
    'sa1': ('abs_allocation_2026','SA1_2026_AUST.xlsx','SA1_2026_AUST'),
    'mb': ('abs_allocation_2026','MB_2026_AUST.xlsx','MB_2026_AUST'),
    'lga': ('abs_allocation_2026','LGA_2026_AUST.xlsx','LGA_2026_AUST'),
    'poa': ('abs_allocation_2026','POA_2026_AUST.xlsx','POA_2026_AUST_MAIN'),
}


def abs_code(value, layer):
    value = '' if value is None else str(value).strip()
    width = {'SA1':11,'MB':11,'RA':2,'SA4':3,'LGA':5,'POA':4}[layer]
    if layer == 'POA' and value.isascii() and value.isdigit():
        value = value.zfill(4)
    if len(value) != width or not (value.isascii() and value.isdigit() or set(value) == {'Z'}):
        raise SourceError('Invalid ABS ' + layer + ' code')
    return value


def non_spatial(code, name='', layer='SA1'):
    return (code.startswith('Z') or any(s in str(name or '').casefold() for s in
        ('migratory','offshore','shipping','no usual address','outside australia','not applicable'))
        or (layer in ('SA1','MB') and code.endswith(('9799999','9899999','99499')))
        or (layer == 'SA4' and code.endswith(('97','99')))
        or (layer == 'LGA' and code.endswith(('9799','9899','9499','9999')))
        or (layer == 'POA' and code in ('9797','9494','9898','9999')))


def parse_remoteness_file(path, kind):
    """Yield strict source rows; large allocation workbooks are always streamed."""
    def records(rows):
        header = list(next(rows, ()))
        if header != REMOTENESS_HEADERS[kind]:
            raise SourceError('ABS remoteness header changed: ' + kind)
        count = 0
        for values in rows:
            if not any(v is not None and v != '' for v in values):
                continue
            if len(values) != len(header):
                raise SourceError('ABS remoteness row width changed')
            row = dict(zip(header, values))
            for key in header:
                if '_CODE_' in key and key.split('_')[0] in ('SA1','MB','RA','SA4','LGA','POA'):
                    if kind == 'cg' and key in ('SA1_CODE_2021','SA1_CODE_2026') and row[key] in ('',None):
                        row[key] = None
                    else:
                        row[key] = abs_code(row[key], key.split('_')[0])
            if 'AREA_ALBERS_SQKM' in row:
                value = row['AREA_ALBERS_SQKM']
                if value in (None,''):
                    row['AREA_ALBERS_SQKM'] = None
                else:
                    try:
                        area = float(value)
                    except (ValueError,TypeError) as exc:
                        raise SourceError('Invalid ABS area') from exc
                    if not math.isfinite(area) or area < 0:
                        raise SourceError('Invalid ABS area')
                    row['AREA_ALBERS_SQKM'] = area
            count += 1
            yield row
        if not count:
            raise SourceError('Empty ABS remoteness file')
    if kind == 'cg':
        with open(path, newline='', encoding='utf-8-sig') as stream:
            yield from records(iter(csv.reader(stream)))
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = REMOTENESS_FILES[kind][2]
            if sheet not in wb.sheetnames:
                raise SourceError('Missing ABS remoteness sheet')
            yield from records(wb[sheet].iter_rows(values_only=True))
        finally:
            wb.close()


def remoteness_resources(manifest):
    resources = {}
    for kind, (dataset, filename, _) in REMOTENESS_FILES.items():
        path, meta = verified_resource(manifest, dataset,
            lambda f, filename=filename: Path(f['path']).name == filename)
        resources[kind] = (path, dict(meta, bytes=path.stat().st_size))
    return resources


def derive_sa1(allocations, ra, correspondences):
    contributors = defaultdict(list)
    totals = defaultdict(Decimal)
    pairs = set()
    for row in correspondences:
        source, target = row['SA1_CODE_2021'], row['SA1_CODE_2026']
        if (target is not None and target not in allocations) or (source,target) in pairs:
            raise SourceError('Unknown or duplicate correspondence destination')
        pairs.add((source,target))
        flag = str(row['BMOS_NULL_FLAG'])
        quality = dict(individual_quality=row['INDIV_TO_REGION_QLTY_INDICATOR'],overall_quality=row['OVERALL_QUALITY_INDICATOR'])
        if row['OVERALL_QUALITY_INDICATOR'] != 'Good' or row['INDIV_TO_REGION_QLTY_INDICATOR'] not in ('Good','Acceptable','Poor','NA'):
            raise SourceError('Correspondence quality requires review')
        if source is None:
            if target is None or flag != '3' or row['RATIO_FROM_TO'] not in (None,''):
                raise SourceError('Invalid no-source correspondence')
            contributors[target].append(dict(sa1_2021=None,ra_class=None,from_ratio=None,bmos_null_flag=flag,**quality))
            continue
        if source not in ra or flag not in ('0','1','2','4'):
            raise SourceError('Unknown correspondence source or flag')
        try:
            ratio = Decimal(str(row['RATIO_FROM_TO']))
        except InvalidOperation as exc:
            raise SourceError('Invalid FROM ratio') from exc
        if not ratio.is_finite() or not 0 <= ratio <= 1:
            raise SourceError('Invalid FROM ratio')
        totals[source] += ratio
        if target is None:
            if flag != '4':
                raise SourceError('Invalid no-destination correspondence')
            # Retain in source evidence and FROM ratio totals; never invent a destination.
            continue
        old = ra[source]
        contributors[target].append(dict(sa1_2021=source,ra_code=old['RA_CODE_2021'],
            ra_class=old['RA_NAME_2021'],from_ratio=str(ratio),bmos_null_flag=flag,
            non_spatial=non_spatial(source,old['RA_NAME_2021']),**quality))
    if set(totals) != set(ra) or any(abs(t-1) > Decimal('0.000001') for t in totals.values()):
        raise SourceError('Incomplete FROM coverage or FROM ratios do not sum to one')
    result = {}
    for code, allocation in allocations.items():
        evidence = contributors[code]
        linked = [r for r in evidence if r['sa1_2021'] is not None]
        classes = sorted({r['ra_class'] for r in linked if not r['non_spatial']})
        special = non_spatial(code) or any(r['non_spatial'] for r in linked)
        unknown = not linked or any(r['sa1_2021'] is None for r in evidence)
        status = 'non-spatial' if special else 'unknown' if unknown else 'single-class' if len(classes)==1 else 'mixed'
        result[code] = dict(layer='SA1',code=code,name=code,status=status,ra_classes=classes,
            contributors=evidence,area_sqkm=allocation['AREA_ALBERS_SQKM'],
            edition_chain=EDITION_CHAIN,method='official CG contributor class consensus; FROM ratios retained, not destination weights',
            readiness=REMOTENESS_READY)
    return result


def load_remoteness(manifest=None, *, resources=None, on_row=None):
    """Validate six official resources and derive conservative area buckets.

    on_row(kind, ordinal, row) observes streamed rows; production does not persist them.
    The MB join retains only integer keys/offsets and numeric arrays, never raw rows.
    The 2021 MB allocation is deliberately unused: RA already supplies SA1 2021.
    """
    resources = resources or remoteness_resources(manifest)
    counts = Counter()
    quality_counts = Counter()
    blank_links = Counter()
    unallocated_links = []
    def rows(kind):
        for row in parse_remoteness_file(resources[kind][0],kind):
            counts[kind] += 1
            if kind == 'cg':
                quality_counts[row['INDIV_TO_REGION_QLTY_INDICATOR']] += 1
                if row['SA1_CODE_2021'] is None: blank_links['no_source'] += 1
                if row['SA1_CODE_2026'] is None:
                    blank_links['no_destination'] += 1
                    unallocated_links.append(dict(ordinal=counts[kind],**row))
            if on_row:
                on_row(kind,counts[kind],row)
            yield row
    def indexed(kind, key, fields):
        result = {}
        for row in rows(kind):
            if row[key] in result:
                raise SourceError('Duplicate ABS allocation key')
            result[row[key]] = {field:row[field] for field in fields}
        return result
    ra = indexed('ra','SA1_CODE_2021',('RA_CODE_2021','RA_NAME_2021'))
    ra_classes = {'Major Cities of Australia','Inner Regional Australia','Outer Regional Australia',
                  'Remote Australia','Very Remote Australia'}
    if any(r['RA_NAME_2021'] not in ra_classes and not non_spatial(c,r['RA_NAME_2021']) for c,r in ra.items()):
        raise SourceError('Unknown official RA class')
    sa1 = indexed('sa1','SA1_CODE_2026',('AREA_ALBERS_SQKM','SA4_CODE_2026'))
    derived = derive_sa1(sa1,ra,rows('cg'))
    del ra
    groups = {}
    def aggregate(layer, code, name, area, unit):
        special = non_spatial(code,name,layer)
        key = (layer,code)
        group = groups.setdefault(key,dict(layer=layer,code=code,name=name,
            status='non-spatial' if special else 'area-reference',ra_classes=set(),area_sqkm=0.,
            area_buckets_sqkm=defaultdict(float),missing_area_mb_count=0,mb_count=0,
            edition_chain=EDITION_CHAIN,method='official MB allocation; area share with mixed/unknown buckets',readiness=REMOTENESS_READY))
        group['mb_count'] += 1
        if not special:
            group['ra_classes'].update(unit['ra_classes'])
        bucket = 'non-spatial' if special else unit['ra_classes'][0] if unit['status']=='single-class' else unit['status']
        if area is None:
            group['missing_area_mb_count'] += 1
        else:
            group['area_sqkm'] += area
            group['area_buckets_sqkm'][bucket] += area
    # Workbook order differs between MB, LGA and POA: join only on official MB codes.
    # Fixed-width numeric MB codes can be indexed as integers without losing identity.
    # Special Z codes remain strings. Coverage flags cost one byte per mesh block.
    mb = {}
    units = list(derived.values())
    unit_indexes = {unit['code']:i for i,unit in enumerate(units)}
    mb_units, mb_areas = array('I'), array('d')
    def mb_key(code):
        return int(code) if code.isdigit() else code
    for row in rows('mb'):
        code, target = mb_key(row['MB_CODE_2026']), row['SA1_CODE_2026']
        if code in mb or target not in derived:
            raise SourceError('Duplicate MB or unknown SA1 allocation')
        unit = derived[target]
        mb[code] = len(mb_units)
        mb_units.append(unit_indexes[target])
        mb_areas.append(row['AREA_ALBERS_SQKM'] if row['AREA_ALBERS_SQKM'] is not None else math.nan)
        if row['SA4_CODE_2026'] != sa1[target]['SA4_CODE_2026']:
            raise SourceError('Inconsistent SA4 allocation')
        aggregate('SA4',row['SA4_CODE_2026'],row['SA4_NAME_2026'],row['AREA_ALBERS_SQKM'],unit)
    del sa1, unit_indexes
    for kind in ('lga','poa'):
        seen = bytearray(len(mb))
        seen_count = 0
        layer = kind.upper()
        for row in rows(kind):
            ordinal = mb.get(mb_key(row['MB_CODE_2026']))
            if ordinal is None or seen[ordinal]:
                raise SourceError('Unknown or duplicate MB allocation')
            seen[ordinal] = 1
            seen_count += 1
            area = mb_areas[ordinal]
            area = None if math.isnan(area) else area
            if area != row['AREA_ALBERS_SQKM']:
                raise SourceError('Inconsistent MB area')
            aggregate(layer,row[layer+'_CODE_2026'],row[layer+'_NAME_2026'],area,units[mb_units[ordinal]])
        if seen_count != len(mb):
            raise SourceError('Incomplete MB allocation coverage')
    del mb, mb_units, mb_areas, units, seen
    for group in groups.values():
        group['ra_classes'] = sorted(group['ra_classes'])
        group['area_buckets_sqkm'] = dict(group['area_buckets_sqkm'])
        denominator = group['area_sqkm']
        group['area_shares'] = {k:(v/denominator if denominator and not group['missing_area_mb_count']
            and group['status']!='non-spatial' else None) for k,v in group['area_buckets_sqkm'].items()}
        group['share_basis'] = 'AREA share of allocated mesh blocks; not population share'
    for path, meta in resources.values():
        if file_hash(path) != meta['sha256']:
            raise SourceError('ABS resource changed while parsing')
    output = list(derived.values())+list(groups.values())
    hashes = {k:m['sha256'] for k,(_,m) in resources.items()}
    fingerprint = _remoteness_fingerprint(resources)
    for row in output:
        row.update(source_hashes=hashes,source_fingerprint=fingerprint,parser_revision=REMOTENESS_REVISION)
    return dict(rows=output,source_counts=dict(counts),correspondence_quality_counts=dict(quality_counts),
                blank_correspondence_counts=dict(blank_links),unallocated_links=unallocated_links,
                sa1_status_counts=dict(Counter(r['status'] for r in derived.values())))


def _remoteness_fingerprint(resources):
    return hashlib.sha256(json.dumps([REMOTENESS_REVISION,
        sorted((k,m['sha256']) for k,(_,m) in resources.items())]).encode()).hexdigest()


_SNAPSHOT_ROW_FIELDS = frozenset(('snapshot_id','source_hashes','source_fingerprint',
                                 'parser_revision','edition_chain','readiness'))


def _stored_remoteness_row(row):
    """Shared provenance lives once on the snapshot; reports restore it on each row."""
    return {k:v for k,v in row.items() if k not in _SNAPSHOT_ROW_FIELDS}


def import_remoteness(engine, manifest):
    resources = remoteness_resources(manifest)
    fingerprint = _remoteness_fingerprint(resources)
    provenance = {k:dict(m,filename=REMOTENESS_FILES[k][1]) for k,(_,m) in resources.items()}
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'market:remoteness'})
        existing = conn.execute(text('SELECT id FROM market.remoteness_snapshots WHERE fingerprint=:fingerprint'),
                                dict(fingerprint=fingerprint)).scalar()
        if existing:
            return dict(status='unchanged',snapshot_id=str(existing))
        snapshot = uuid.uuid4()
        conn.execute(text('''INSERT INTO market.remoteness_snapshots
            (id,fingerprint,edition_chain,parser_revision,provenance,retrieved_at)
            VALUES(:id,:fingerprint,:chain,:revision,CAST(:provenance AS jsonb),:retrieved)'''),
            dict(id=snapshot,fingerprint=fingerprint,chain=EDITION_CHAIN,revision=REMOTENESS_REVISION,
                 provenance=json.dumps(provenance,default=str),retrieved=max(m['retrieved_at'] for _,m in resources.values())))
        result = load_remoteness(resources=resources)
        for start in range(0,len(result['rows']),500):
            conn.execute(text('''INSERT INTO market.remoteness_rows(snapshot_id,layer,code,payload)
                VALUES(:snapshot,:layer,:code,CAST(:payload AS jsonb))'''),[
                    dict(snapshot=snapshot,layer=r['layer'],code=r['code'],
                         payload=json.dumps(_stored_remoteness_row(r),allow_nan=False))
                    for r in result['rows'][start:start+500]])
        metrics = {k:v for k,v in result.items() if k!='rows'}
        metrics['stored_row_counts'] = dict(snapshots=1,derived=len(result['rows']),raw_source_rows=0)
        for kind, count in result['source_counts'].items():
            provenance[kind]['row_count'] = count
        conn.execute(text('''UPDATE market.remoteness_snapshots
            SET metrics=CAST(:metrics AS jsonb),provenance=CAST(:provenance AS jsonb) WHERE id=:id'''),
            dict(id=snapshot,metrics=json.dumps(metrics),provenance=json.dumps(provenance,default=str)))
    return dict(status='complete',snapshot_id=str(snapshot),**metrics,readiness=REMOTENESS_READY)


def build_remoteness(engine, as_of):
    with engine.connect() as conn:
        snapshot = conn.execute(text('''SELECT * FROM market.remoteness_snapshots
            ORDER BY retrieved_at DESC,imported_at DESC LIMIT 1''')).mappings().first()
        rows = [] if snapshot is None else [dict(r['payload'],snapshot_id=str(snapshot['id']),
            source_hashes={k:v['sha256'] for k,v in snapshot['provenance'].items()},parser_revision=snapshot['parser_revision'],
            source_fingerprint=snapshot['fingerprint'],edition_chain=snapshot['edition_chain'],readiness=REMOTENESS_READY)
            for r in conn.execute(text('SELECT payload FROM market.remoteness_rows WHERE snapshot_id=:id ORDER BY layer,code'),
                                  dict(id=snapshot['id'])).mappings()]
    return dict(as_of=as_of,status=REMOTENESS_READY if snapshot else 'No remoteness snapshot loaded. '+REMOTENESS_READY,
                snapshot=dict(snapshot) if snapshot else None,rows=rows,limitations=REMOTENESS_LIMITATIONS)


def write_remoteness(report, directory):
    return write_report_files(report,directory,'remoteness',report['rows'],
        ['layer','code','name','status','ra_classes','area_sqkm','area_shares','share_basis','contributors',
         'edition_chain','method','readiness','snapshot_id','source_hashes','parser_revision'],'ABS derived remoteness references')

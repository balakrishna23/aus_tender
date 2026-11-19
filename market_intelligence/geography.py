"""Load existing ABS boundaries without inventing postcode or remoteness correspondences."""
from contextlib import contextmanager
import io
import json
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

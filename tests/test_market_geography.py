import io
import json
import copy
import csv
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

import shapefile
import openpyxl

from market_intelligence.geography import boundary_reader,feature
from market_intelligence.geography import (REMOTENESS_FILES, REMOTENESS_HEADERS, abs_code,
    non_spatial, parse_remoteness_file, load_remoteness, remoteness_resources,
    import_remoteness, build_remoteness, write_remoteness)
from market_intelligence.parsers import SourceError


def remoteness_fixture(root):
    """Five destinations: consensus, mixed, blank source, absent source, non-spatial."""
    def row(kind, **values):
        result = {h: '' for h in REMOTENESS_HEADERS[kind]}
        result.update(values)
        return result
    ra = [row('ra',SA1_CODE_2021=code,RA_CODE_2021=ra_code,RA_NAME_2021=name,AREA_ALBERS_SQKM=1)
          for code,ra_code,name in [('10102100101','10','Major Cities of Australia'),
            ('10102100102','11','Inner Regional Australia'),
            ('ZZZZZZZZZZZ','ZZ','Outside Australia')]]
    sa1 = [row('sa1',SA1_CODE_2026=code,SA4_CODE_2026='101',AREA_ALBERS_SQKM=1)
           for code in ['10102100101','10102100102','10102100103','10102100104','ZZZZZZZZZZZ']]
    cg = [row('cg',SA1_CODE_2021=source,SA1_CODE_2026=target,RATIO_FROM_TO=ratio,
              BMOS_NULL_FLAG=flag,INDIV_TO_REGION_QLTY_INDICATOR='Good',OVERALL_QUALITY_INDICATOR='Good')
          for source,target,ratio,flag in [('10102100101','10102100101','.4','1'),
            ('10102100101','10102100102','.6','1'),('10102100102','10102100102','1','0'),
            ('','10102100103','','3'),('ZZZZZZZZZZZ','ZZZZZZZZZZZ','1','4')]]
    data = dict(ra=ra,sa1=sa1,cg=cg,mb=[],lga=[],poa=[])
    for i, unit in enumerate(sa1,1):
        mb = str(10000000000+i)
        data['mb'].append(row('mb',MB_CODE_2026=mb,SA1_CODE_2026=unit['SA1_CODE_2026'],
            SA4_CODE_2026='101',SA4_NAME_2026='Test region',AREA_ALBERS_SQKM=1))
        data['lga'].append(row('lga',MB_CODE_2026=mb,LGA_CODE_2026='10050',LGA_NAME_2026='Test council',AREA_ALBERS_SQKM=1))
        data['poa'].append(row('poa',MB_CODE_2026=mb,POA_CODE_2026='800',POA_NAME_2026='0800',AREA_ALBERS_SQKM=1))
    return data


def write_remoteness_fixture(root, data):
    files = []
    for kind, rows in data.items():
        dataset, filename, sheet = REMOTENESS_FILES[kind]
        path = root/filename
        if sheet:
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = sheet
            ws.append(REMOTENESS_HEADERS[kind])
            for r in rows: ws.append([r[h] for h in REMOTENESS_HEADERS[kind]])
            wb.save(path); wb.close()
        else:
            with path.open('w',newline='',encoding='utf-8') as stream:
                writer=csv.DictWriter(stream,fieldnames=REMOTENESS_HEADERS[kind]);writer.writeheader();writer.writerows(rows)
        files.append(dict(dataset=dataset,path=str(path),status='complete',bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),retrieved_at='2026-11-20T13:00:00+00:00',
            licence='CC BY 4.0',licence_url='https://creativecommons.org/licenses/by/4.0/',
            source_url='https://www.abs.gov.au/example',download_url='https://www.abs.gov.au/'+filename))
    manifest=root/'manifest.json'
    manifest.write_text(json.dumps(dict(status='complete',files=files)),encoding='utf-8')
    return manifest


class RemotenessTests(unittest.TestCase):
    def test_consensus_mixed_unknown_and_area_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); manifest=write_remoteness_fixture(root,remoteness_fixture(root))
            result=load_remoteness(manifest)
            self.assertEqual(result['sa1_status_counts'],{'single-class':1,'mixed':1,'unknown':2,'non-spatial':1})
            mixed=next(r for r in result['rows'] if r['status']=='mixed')
            self.assertEqual([r['from_ratio'] for r in mixed['contributors']],['0.6','1'])
            self.assertEqual(len(mixed['ra_classes']),2)
            poa=next(r for r in result['rows'] if r['layer']=='POA')
            self.assertEqual(poa['code'],'0800')
            self.assertEqual(poa['area_shares'],{'Major Cities of Australia':.2,'mixed':.2,'unknown':.4,'non-spatial':.2})
            self.assertIn('not population',poa['share_basis'])
            self.assertTrue(all('Edition 3' in r['edition_chain'] and 'March 2028' in r['readiness'] for r in result['rows']))
            report=dict(as_of='2026-11-20',status='test',rows=result['rows'],limitations=['AREA share only'])
            output=write_remoteness(report,root/'out')
            self.assertTrue(Path(output['json']).exists())
            self.assertIn('contributors',(root/'out'/'remoteness.csv').read_text(encoding='utf-8-sig'))

    def test_strict_headers_sheet_and_code_normalisation(self):
        self.assertEqual(abs_code(800,'POA'),'0800')
        for code,layer in [('ZZZZ','POA'),('9797','POA'),('99499','LGA'),('197','SA4')]:
            self.assertTrue(non_spatial(code,layer=layer))
        with self.assertRaises(SourceError): abs_code('800.0','POA')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'bad.csv';path.write_text('wrong,header\n1,2\n')
            with self.assertRaises(SourceError): list(parse_remoteness_file(path,'cg'))
            path=Path(tmp)/'bad.xlsx';wb=openpyxl.Workbook();wb.save(path);wb.close()
            with self.assertRaises(SourceError): list(parse_remoteness_file(path,'mb'))

    def test_same_class_consensus_and_blank_destination_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=remoteness_fixture(root)
            same=copy.deepcopy(data['ra'][0]);same['SA1_CODE_2021']='10102100105';data['ra'].append(same)
            cg=copy.deepcopy(data['cg'][0]);cg.update(SA1_CODE_2021='10102100105',RATIO_FROM_TO='1')
            data['cg'].append(cg)
            no_target=copy.deepcopy(data['cg'][0]);no_target.update(SA1_CODE_2026='',RATIO_FROM_TO='0.00002',BMOS_NULL_FLAG='4')
            data['cg'].append(no_target);data['cg'][0]['RATIO_FROM_TO']='0.39998'
            result=load_remoteness(write_remoteness_fixture(root,data))
            single=next(r for r in result['rows'] if r['layer']=='SA1' and r['code']=='10102100101')
            self.assertEqual(single['status'],'single-class')
            self.assertEqual(len(single['contributors']),2)
            self.assertEqual(single['contributors'][0]['from_ratio'],'0.39998')
            self.assertEqual(result['source_counts']['cg'],7)

    def test_zero_ratios_retain_official_class_links_and_quality(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=remoteness_fixture(root)
            zero=copy.deepcopy(data['cg'][0])
            zero.update(SA1_CODE_2026='10102100104',RATIO_FROM_TO='0',INDIV_TO_REGION_QLTY_INDICATOR='Poor')
            data['cg'].append(zero)
            result=load_remoteness(write_remoteness_fixture(root,data))
            single=next(r for r in result['rows'] if r['layer']=='SA1' and r['code']=='10102100104')
            self.assertEqual(single['status'],'single-class')
            self.assertEqual(single['contributors'][0]['from_ratio'],'0')
            self.assertEqual(single['contributors'][0]['individual_quality'],'Poor')
            self.assertEqual(len(single['source_hashes']),6)

    def test_reject_incomplete_and_inconsistent_official_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=remoteness_fixture(root)
            changes=[('cg',0,'RATIO_FROM_TO','.2'),('cg',3,'BMOS_NULL_FLAG','0'),
                     ('cg',0,'RATIO_FROM_TO','NaN'),('cg',0,'RATIO_FROM_TO','-1'),
                     ('cg',0,'OVERALL_QUALITY_INDICATOR','Poor'),('ra',0,'RA_NAME_2021','Unknown class'),
                     ('cg',0,'SA1_CODE_2021','10102100999'),('mb',0,'SA1_CODE_2026','10102100999'),
                     ('mb',0,'SA4_CODE_2026','102'),('lga',0,'AREA_ALBERS_SQKM',3),
                     ('poa',0,'AREA_ALBERS_SQKM',-1)]
            for kind,index,key,value in changes:
                with self.subTest(kind=kind,key=key):
                    data=copy.deepcopy(original);data[kind][index][key]=value
                    manifest=write_remoteness_fixture(root,data)
                    with self.assertRaises(SourceError): load_remoteness(manifest)
            for kind in ('sa1','mb','lga','poa','cg'):
                with self.subTest(duplicate=kind):
                    data=copy.deepcopy(original);data[kind].append(data[kind][0])
                    manifest=write_remoteness_fixture(root,data)
                    with self.assertRaises(SourceError): load_remoteness(manifest)
            data=copy.deepcopy(original);data['poa'].pop()
            with self.assertRaises(SourceError):load_remoteness(write_remoteness_fixture(root,data))

    def test_verified_hash_and_read_time_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);manifest=write_remoteness_fixture(root,remoteness_fixture(root))
            resources=remoteness_resources(manifest)
            path=resources['cg'][0]
            path.write_bytes(path.read_bytes()+b'\n')
            with self.assertRaises(SourceError):remoteness_resources(manifest)
            with self.assertRaises(SourceError):load_remoteness(resources=resources)

    def test_zero_missing_area_and_nonspatial_region_shares_are_null(self):
        for area in (None,0):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);data=remoteness_fixture(root)
                for kind in ('mb','lga','poa'):
                    for row in data[kind]:row['AREA_ALBERS_SQKM']=area
                for row in data['poa']:row['POA_CODE_2026']='9797';row['POA_NAME_2026']='Migratory'
                result=load_remoteness(write_remoteness_fixture(root,data))
                groups=[r for r in result['rows'] if r['layer']!='SA1']
                self.assertTrue(all(v is None for r in groups for v in r['area_shares'].values()))
                self.assertEqual(next(r for r in groups if r['layer']=='POA')['status'],'non-spatial')

    def test_streamed_allocation_join_uses_codes_not_workbook_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=remoteness_fixture(root)
            # Exercise the compact index's special-code key and its zero-based first offset.
            for kind in ('mb','lga','poa'):
                data[kind][-1]['MB_CODE_2026']='ZZZZZZZZZZZ'
            manifest=write_remoteness_fixture(root,data)
            original=load_remoteness(manifest)
            data['mb'].reverse();data['lga']=data['lga'][2:]+data['lga'][:2];data['poa'].reverse()
            reordered=load_remoteness(write_remoteness_fixture(root,data))
            fields=('layer','code','area_buckets_sqkm','area_shares','mb_count')
            summary=lambda result:[{k:r[k] for k in fields} for r in result['rows'] if r['layer']!='SA1']
            self.assertEqual(summary(original),summary(reordered))

    def test_import_stores_only_derivations_and_snapshot_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=remoteness_fixture(root)
            no_target=copy.deepcopy(data['cg'][0]);no_target.update(SA1_CODE_2026='',RATIO_FROM_TO='0.00002',BMOS_NULL_FLAG='4')
            data['cg'].append(no_target);data['cg'][0]['RATIO_FROM_TO']='0.39998'
            manifest=write_remoteness_fixture(root,data)
            db=MagicMock();conn=db.begin.return_value.__enter__.return_value
            conn.execute.return_value.scalar.return_value=None
            result=import_remoteness(db,manifest)
            calls=conn.execute.call_args_list
            inserts=[call for call in calls if str(call.args[0]).lstrip().startswith('INSERT')]
            self.assertEqual(len(inserts),2)  # one snapshot plus one small batch of derived rows
            self.assertTrue(all('remoteness_snapshots' in str(c.args[0]) or 'remoteness_rows(' in str(c.args[0]) for c in inserts))
            payloads=[json.loads(row['payload']) for row in inserts[1].args[1]]
            self.assertEqual(len(payloads),8)
            self.assertEqual(sum(len(r.get('contributors',[])) for r in payloads),5)
            self.assertTrue(all('source_hashes' not in r and 'edition_chain' not in r for r in payloads))
            update=next(c.args[1] for c in calls if str(c.args[0]).lstrip().startswith('UPDATE'))
            provenance=json.loads(update['provenance']);metrics=json.loads(update['metrics'])
            self.assertEqual(metrics['stored_row_counts'],{'snapshots':1,'derived':8,'raw_source_rows':0})
            self.assertEqual(metrics['unallocated_links'][0]['RATIO_FROM_TO'],'0.00002')
            self.assertIsNone(metrics['unallocated_links'][0]['SA1_CODE_2026'])
            for kind,meta in provenance.items():
                self.assertEqual(meta['row_count'],len(data[kind]))
                self.assertEqual(meta['bytes'],(root/meta['filename']).stat().st_size)
                self.assertTrue(all(k in meta for k in ('sha256','retrieved_at','licence','licence_url','source_url','download_url')))
            # Reports must restore shared snapshot provenance on every derived row.
            from market_intelligence.geography import EDITION_CHAIN,REMOTENESS_REVISION,REMOTENESS_READY
            snapshot=dict(id=result['snapshot_id'],provenance=provenance,metrics=metrics,
                          edition_chain=EDITION_CHAIN,parser_revision=REMOTENESS_REVISION,fingerprint='test-fingerprint')
            read_conn=db.connect.return_value.__enter__.return_value
            snapshot_result=MagicMock();snapshot_result.mappings.return_value.first.return_value=snapshot
            rows_result=MagicMock();rows_result.mappings.return_value=[dict(payload=r) for r in payloads]
            read_conn.execute.side_effect=[snapshot_result,rows_result]
            report=build_remoteness(db,'2026-11-20')
            self.assertEqual(report['rows'][0]['edition_chain'],EDITION_CHAIN)
            self.assertEqual(report['rows'][0]['readiness'],REMOTENESS_READY)
            self.assertEqual(len(report['rows'][0]['source_hashes']),6)
            self.assertEqual(report['rows'][0]['snapshot_id'],result['snapshot_id'])
            # Unchanged inputs must short-circuit derivation and all writes.
            conn.execute.reset_mock();conn.execute.return_value.scalar.return_value=result['snapshot_id']
            with patch('market_intelligence.geography.load_remoteness') as derive:
                self.assertEqual(import_remoteness(db,manifest)['status'],'unchanged')
                derive.assert_not_called()
            self.assertFalse(any(str(c.args[0]).lstrip().startswith(('INSERT','UPDATE')) for c in conn.execute.call_args_list))

    @unittest.skipUnless(os.environ.get('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Requires isolated PostgreSQL test database')
    def test_database_idempotence_and_provenance(self):
        from market_intelligence.db import engine,migrate
        db=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL']);migrate(db)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);manifest=write_remoteness_fixture(root,remoteness_fixture(root))
                first=import_remoteness(db,manifest);second=import_remoteness(db,manifest)
                self.assertEqual(second['status'],'unchanged')
                self.assertEqual(first['snapshot_id'],second['snapshot_id'])
                report=build_remoteness(db,'2026-11-20')
                self.assertTrue(report['rows'])
                self.assertEqual(len(report['rows'][0]['source_hashes']),6)
                self.assertEqual(report['snapshot']['metrics']['source_counts']['mb'],5)
        finally:db.dispose()


class GeographyTests(unittest.TestCase):
    def test_native_coordinates_and_missing_shapes_are_preserved(self):
        shp,shx,dbf=io.BytesIO(),io.BytesIO(),io.BytesIO()
        with shapefile.Writer(shp=shp,shx=shx,dbf=dbf,shapeType=shapefile.POLYGON) as w:
            w.field('POA_CODE26','C');w.field('POA_NAME26','C');w.field('AREASQKM26','N',decimal=2)
            w.poly([[[149,-36],[149,-35],[150,-35],[150,-36],[149,-36]]]);w.record('2600','2600',1.0)
            w.null();w.record('9999','Outside scope',0)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'poa.zip'
            with zipfile.ZipFile(path,'w') as z:
                for suffix,data in [('shp',shp.getvalue()),('shx',shx.getvalue()),('dbf',dbf.getvalue())]:z.writestr('POA.'+suffix,data)
                z.writestr('POA.prj','GEOGCS["GDA2020",DATUM["GDA2020"]]');z.writestr('POA.cpg','UTF-8')
            with boundary_reader(path,'POA') as (r,prj):
                rows=[feature(record,'POA') for record in r.iterShapeRecords()]
        self.assertEqual(rows[0]['code'],'2600');self.assertIsNone(rows[0]['state_code'])
        self.assertEqual(json.loads(rows[0]['geometry'])['coordinates'][0][0],[149.0,-36.0])
        self.assertIsNone(rows[1]['geometry']);self.assertIn('GDA2020',prj)


if __name__=='__main__':unittest.main()

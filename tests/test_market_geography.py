import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import shapefile

from market_intelligence.geography import boundary_reader,feature


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

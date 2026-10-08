"""Exercise the authorized UI sync endpoint; only read Sheets and import locally."""
import json
import re
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError

base='http://127.0.0.1:8765'
html=urlopen(base).read().decode()
token=re.search(r"const token='([^']+)'",html).group(1)
try:urlopen(Request(base+'/api/sync-sheets',method='POST'))
except HTTPError as error:assert error.code==403
else:raise AssertionError('Sheets sync accepted without token')
with urlopen(Request(base+'/api/sync-sheets',method='POST',headers={'X-Pilot-Token':token})) as response:
    assert response.status==202
    assert json.load(response)['status']=='running'
deadline=time.monotonic()+240
while time.monotonic()<deadline:
    with urlopen(base+'/api/sheets-status') as response:status=json.load(response)
    if status['status']!='running':
        assert status['status']=='completed',status
        assert status['tabs']==26 and status['failed_reads']==0,status
        print(json.dumps({'sync_endpoint':'passed','tabs':status['tabs'],'rows':status['rows'],'completed_at_utc':status['last_completed_at_utc']}))
        break
    time.sleep(2)
else:raise AssertionError('Sheets sync exceeded the smoke-check deadline')

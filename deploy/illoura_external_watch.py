"""Run on a different host to detect API/readiness silence. No credentials needed.

Exit 0 means ready; 1 means failure. Wire exit 1 to an independently authenticated
alert service. Running this on the monitored VM does not detect that VM's outage.
"""
import argparse
import json
from urllib.request import urlopen

def probe(url):
    if not url.startswith('https://'):raise ValueError('HTTPS URL required')
    try:
        with urlopen(url.rstrip('/')+'/readyz',timeout=15) as response:
            value=json.load(response)
            return response.status==200 and value.get('status')=='ok'
    except Exception:return False

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('base_url');a=p.parse_args()
    ok=probe(a.base_url)
    print(json.dumps({'ready':ok,'external_probe':True}))
    raise SystemExit(0 if ok else 1)

"""Provision the loopback-only research browser; credentials stay in a private JSON file.

Run as the research owner. This configures an existing Metabase, creates no subscriptions
and writes no public links. Re-running reuses its saved questions and dashboard.
"""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

VIEWS={
 'Agency spend':('v_agency_spend','original_awarded_value DESC NULLS LAST'),
 'Supplier profiles':('v_supplier_profile','original_awarded_value DESC NULLS LAST'),
 'Healthcare source directory':('v_healthcare_accounts','organisation'),
 'Published workforce indicators':('v_demand_signals','source,occupation_code,region'),
 'Panel dates to verify':('v_panel_calendar','event_date'),
}


class Client:
    def __init__(self,base='http://127.0.0.1:3000'):
        if base!='http://127.0.0.1:3000':raise ValueError('Provision over the private loopback listener')
        self.base=base;self.session=None

    def request(self,method,path,payload=None):
        headers={'Content-Type':'application/json'}
        if self.session:headers['X-Metabase-Session']=self.session
        req=urllib.request.Request(self.base+path,data=json.dumps(payload).encode() if payload is not None else None,method=method,headers=headers)
        with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)


def provision(private):
    private=Path(private);credentials_path=private/'credentials.json'
    if os.name!='nt' and credentials_path.stat().st_mode&0o077:raise ValueError('Credentials must be owner-only')
    credentials=json.loads(credentials_path.read_text());client=Client()
    if client.request('GET','/api/health').get('status')!='ok':raise RuntimeError('Metabase not healthy')
    properties=client.request('GET','/api/session/properties')
    if properties.get('setup-token'):
        session=client.request('POST','/api/setup',dict(token=properties['setup-token'],
            user=dict(first_name='Illoura',last_name='Owner',email=credentials['admin_email'],password=credentials['admin_password']),
            prefs=dict(site_name='Illoura Research',site_locale='en')))
    else:session=client.request('POST','/api/session',dict(username=credentials['admin_email'],password=credentials['admin_password']))
    client.session=session['id']
    databases=client.request('GET','/api/database')['data']
    database=next((d for d in databases if d['name']=='Illoura research (read only)'),None)
    if database is None:
        database=client.request('POST','/api/database',dict(name='Illoura research (read only)',engine='postgres',is_full_sync=False,
            details=dict(host='127.0.0.1',port=5432,dbname='illoura_market',user='illoura_market_reader',password=credentials['reader_password'],
                         ssl=False,**{'schema-filters-type':'inclusion','schema-filters-patterns':'market'})))
    state_path=private/'provisioned.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'cards':{}}
    state['database_id']=database['id']
    if not state.get('dashboard_id'):
        dashboard=client.request('POST','/api/dashboard',dict(name='Illoura evidence browser',
            description='Recorded evidence only. Source dates, entity review, panel access and workforce need require verification.'))
        state['dashboard_id']=dashboard['id'];state_path.write_text(json.dumps(state,indent=2))
    checks=[]
    for title,(view,order) in VIEWS.items():
        if view not in state['cards']:
            card=client.request('POST','/api/card',dict(name=title,type='question',display='table',visualization_settings={},
                description='Read-only source evidence; limited to 100 rows. Published commitments and purpose do not prove demand or eligibility.',
                dataset_query=dict(database=database['id'],type='native',native=dict(query=f'SELECT * FROM market.{view} ORDER BY {order} LIMIT 100'))))
            state['cards'][view]=card['id'];state_path.write_text(json.dumps(state,indent=2))
        result=client.request('POST',f"/api/card/{state['cards'][view]}/query",{})
        if result.get('status')!='completed':raise RuntimeError('Saved evidence query failed: '+view)
        checks.append(dict(view=view,rows=len(result['data']['rows']),status=result['status']))
    existing=client.request('GET',f"/api/dashboard/{state['dashboard_id']}")
    if not existing.get('dashcards'):
        client.request('PUT',f"/api/dashboard/{state['dashboard_id']}",dict(dashcards=[
            dict(id=-i-1,card_id=state['cards'][view],row=i*6,col=0,size_x=24,size_y=6)
            for i,(view,_) in enumerate(VIEWS.values())]))
    # Prove saved content requires authentication, without printing a token or data row.
    try:
        Client().request('GET',f"/api/dashboard/{state['dashboard_id']}")
        raise RuntimeError('Dashboard unexpectedly accessible without authentication')
    except urllib.error.HTTPError as exc:
        if exc.code not in (401,403):raise
    state.update(status='complete',checks=checks,authentication_required=True,listener='127.0.0.1:3000')
    state_path.write_text(json.dumps(state,indent=2));return state


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--private-directory',required=True);args=p.parse_args(argv)
    try:
        print(json.dumps(provision(args.private_directory),indent=2));return 0
    except Exception as exc:
        # HTTP response bodies can contain connection details. Keep errors categorical.
        print(json.dumps({'status':'failed','error_code':type(exc).__name__}));return 1


if __name__=='__main__':raise SystemExit(main())

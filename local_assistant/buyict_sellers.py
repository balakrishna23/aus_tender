"""Company facts from BuyICT's public seller catalogue: ABN, legal name, marketplaces, website.

Only company-level fields are stored. BuyICT's terms of use limit personal information seen on the
platform to conducting procurements through BuyICT, so named contacts are never taken from it;
contacts come from the company's own website through contact_discovery, and stay review candidates.
"""
import argparse,hashlib,json,re,sqlite3,time
from contextlib import closing
from datetime import datetime,timezone
from urllib.robotparser import RobotFileParser
import requests
import assistant

BASE='https://www.buyict.gov.au'
AGENT='Illoura-public-business-contact-research/1.0'
WAIT=2
SCHEMA='''CREATE TABLE IF NOT EXISTS buyict_sellers(
 sys_id TEXT PRIMARY KEY,name TEXT,legal_name TEXT,abn TEXT,supplier_key TEXT,marketplaces_json TEXT,
 identifiers_json TEXT,catalogue_seen_at_utc TEXT,website TEXT,linkedin_company TEXT,location_json TEXT,
 profile_updated_on TEXT,onboarding TEXT,profile_status TEXT,profile_checked_at_utc TEXT,profile_sha256 TEXT,
 source_url TEXT);'''

def now():return datetime.now(timezone.utc).isoformat()
def seller_url(sys_id):return BASE+'/sp?id=seller_catalogue&path=selling&at=all_marketplaces&sid='+sys_id
def clean_abn(text):
    digits=re.sub(r'\D','',str(text or ''))
    return digits if len(digits)==11 else ''
def clean_website(text):
    """Seller-entered URL as an https origin+path; '' when it is not a plausible public web address."""
    value=str(text or '').strip()
    if not value or ' ' in value or '@' in value:return ''
    value=re.sub(r'^(https?:)?//','',value,flags=re.I)
    value=value.split('#',1)[0].rstrip('/')
    if not re.fullmatch(r'[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/\S*)?',value):return ''
    return 'https://'+value+('' if '/' in value else '/')

def find_widget(obj,widget_id):
    if isinstance(obj,dict):
        widget=obj.get('widget')
        if isinstance(widget,dict) and widget.get('id')==widget_id:return obj
        values=obj.values()
    elif isinstance(obj,list):values=obj
    else:return None
    for value in values:
        found=find_widget(value,widget_id)
        if found:return found

class Portal:
    """Anonymous public-portal session, the same calls the catalogue page makes in a browser."""
    def __init__(self,session=None):
        self.session=session or requests.Session()
        self.robot=RobotFileParser()
        response=self.session.get(BASE+'/robots.txt',headers={'User-Agent':AGENT},timeout=30)
        if response.status_code==200:self.robot.parse(response.text.splitlines())
        elif response.status_code in (404,410):self.robot.parse([])
        else:raise ValueError('Cannot establish robots permission: HTTP '+str(response.status_code))
        landing=self.get(BASE+'/sp?id=seller_catalogue')
        token=re.search(r"window\.g_ck\s*=\s*'([^']+)'",landing.text)
        portal=re.search(r"portal_id\s*=\s*'([^']+)'",landing.text)
        if not token or not portal:raise ValueError('BuyICT page token not found; the portal may have changed')
        self.portal=portal.group(1)
        self.headers={'User-Agent':AGENT,'accept':'application/json','x-usertoken':token.group(1),'x-portal':self.portal,
            'content-type':'application/json;charset=UTF-8','referer':BASE+'/sp?id=seller_catalogue',
            'x-transaction-source':'Interface=Web,Interface-Name=PUBLIC,Interface-Type=Service Portal,Interface-SysID='+self.portal}
    def allowed(self,url):
        if not self.robot.can_fetch(AGENT,url):raise ValueError('Disallowed by robots.txt: '+url)
    def get(self,url):
        self.allowed(url);time.sleep(WAIT)
        response=self.session.get(url,headers=getattr(self,'headers',{'User-Agent':AGENT}),timeout=60)
        response.raise_for_status();return response
    def post(self,url,body):
        self.allowed(url);time.sleep(WAIT)
        response=self.session.post(url,headers=self.headers,data=json.dumps(body),timeout=90)
        response.raise_for_status();return response.json()['result']

def catalogue_widget(portal):
    page=portal.get(BASE+'/api/now/sp/page?id=seller_catalogue&portal_id='+portal.portal+
                    '&request_uri=%2Fsp%3Fid%3Dseller_catalogue').json()['result']
    wrap=find_widget(page,'seller_catalogue')
    if not wrap:raise ValueError('Seller catalogue widget not found; the portal may have changed')
    return wrap,wrap['widget']['data']['seller_catalogue_profile_modal']['sys_id']

def catalogue(portal):
    """Every seller card in the 'All Sellers' tab, 100 per page."""
    wrap,modal=catalogue_widget(portal)
    url=BASE+'/api/now/sp/rectangle/'+wrap['instance_id']+'?id=seller_catalogue'
    data=dict(wrap['widget']['data'],refresh_data=True,first_load=True)
    cards={};number=1;total=None
    while True:
        data=portal.post(url,data)['data']
        tab=next(m for m in data['all_mps'] if m['sys_id']=='all_marketplaces')
        total=tab['num_results']
        for card in tab.get('seller_cards') or []:cards[card['sys_id']]=card
        if number*100>=total:break
        number+=1
        tab.update(current_page=number,window_start=(number-1)*100,window_end=number*100,has_rendered=False)
        data.update(refresh_data=True,first_load=False,global_filter_has_changed=False,active_tab='all_marketplaces')
    if len(cards)<total:raise ValueError(f'Catalogue incomplete: {len(cards)} of {total} sellers read')
    return list(cards.values()),modal

def profile(portal,modal,sys_id):
    result=portal.post(BASE+'/api/now/sp/widget/'+modal+'?id=seller_catalogue',
                       {'panel':'all_marketplaces','seller':sys_id,'show_checkbox':False})
    seller=(((result.get('data') or {}).get('profileWidget') or {}).get('data') or {}).get('seller')
    if not seller:raise ValueError('Profile not returned')
    return seller

def priority_order(connection):
    """Profiles for suppliers we already know (MMR primes, active ICT/labour hire, most notices) first."""
    intelligence=assistant.DB_DIR/'intelligence.db';contracts=assistant.DB_DIR/'contract_api.db'
    connection.execute("ATTACH DATABASE ? AS intel",(intelligence.resolve().as_uri()+'?mode=ro',))
    connection.execute("ATTACH DATABASE ? AS api",(contracts.resolve().as_uri()+'?mode=ro',))
    try:
        return [row[0] for row in connection.execute('''
            SELECT b.sys_id FROM buyict_sellers b LEFT JOIN suppliers s ON s.supplier_key=b.supplier_key
            WHERE b.profile_checked_at_utc IS NULL
            ORDER BY b.abn IN (SELECT abn FROM intel.mmr_primes) DESC,
                     b.abn IN (SELECT supplier_abn FROM api.contracts WHERE current_end_date>=date('now')
                               AND category_family IN ('ICT services','labour hire and recruitment','ICT equipment and general')) DESC,
                     s.supplier_key IS NOT NULL DESC, COALESCE(s.notice_count,0) DESC, b.name COLLATE NOCASE''')]
    finally:
        connection.execute('DETACH DATABASE intel');connection.execute('DETACH DATABASE api')

def run(read_catalogue=True,profiles=0):
    path=assistant.DB_DIR/'company_contacts.db'
    with closing(sqlite3.connect(path.resolve().as_uri(),uri=True)) as connection:
        connection.executescript(SCHEMA)
        portal=Portal();modal=None;summary={'checked_at_utc':now()}
        if read_catalogue:
            cards,modal=catalogue(portal);seen=now()
            for card in cards:
                abn=clean_abn(card.get('abn'))
                connection.execute('''INSERT INTO buyict_sellers(sys_id,name,legal_name,abn,supplier_key,marketplaces_json,
                    identifiers_json,catalogue_seen_at_utc,source_url) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(sys_id) DO UPDATE SET name=excluded.name,legal_name=excluded.legal_name,abn=excluded.abn,
                    supplier_key=excluded.supplier_key,marketplaces_json=excluded.marketplaces_json,
                    identifiers_json=excluded.identifiers_json,catalogue_seen_at_utc=excluded.catalogue_seen_at_utc''',
                    (card['sys_id'],card.get('name'),card.get('legal_name'),abn,'ABN:'+abn if abn else '',
                     json.dumps(sorted(m['label'] for m in card.get('approved_marketplaces') or [])),
                     json.dumps(card.get('identifiers') or []),seen,seller_url(card['sys_id'])))
            connection.commit();summary['catalogue_sellers']=len(cards)
        if profiles:
            if modal is None:_,modal=catalogue_widget(portal)
            done=0
            for sys_id in priority_order(connection)[:profiles]:
                try:
                    seller=profile(portal,modal,sys_id);raw=json.dumps(seller,sort_keys=True)
                    links=seller.get('url') or {};dates=seller.get('dates') or {}
                    location=(seller.get('locations') or {}).get('primary') or {}
                    website=clean_website(links.get('website'))
                    status='website_published' if website else 'no_website_on_profile'
                    connection.execute('''UPDATE buyict_sellers SET website=?,linkedin_company=?,location_json=?,profile_updated_on=?,
                        onboarding=?,profile_status=?,profile_checked_at_utc=?,profile_sha256=? WHERE sys_id=?''',
                        (website,links.get('linkedin') or '',json.dumps(location),dates.get('last_updated') or '',
                         dates.get('onboarding') or '',status,now(),hashlib.sha256(raw.encode()).hexdigest(),sys_id))
                except (ValueError,KeyError,requests.RequestException) as exc:
                    connection.execute('UPDATE buyict_sellers SET profile_status=?,profile_checked_at_utc=? WHERE sys_id=?',
                                       ('not_read: '+str(exc)[:200],now(),sys_id))
                connection.commit();done+=1
            summary['profiles_read_this_run']=done
        counts=connection.execute('''SELECT COUNT(*),SUM(abn!=''),SUM(supplier_key IN (SELECT supplier_key FROM suppliers)),
            SUM(website!=''),SUM(profile_checked_at_utc IS NOT NULL) FROM buyict_sellers''').fetchone()
        summary.update(dict(zip(['sellers','with_abn','matched_to_suppliers','with_website','profiles_checked'],counts)))
    summary['limit']='Seller-published company facts on a government platform; a website is not a contact. No personal information is taken from BuyICT.'
    print(json.dumps(summary),flush=True)
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--skip-catalogue',action='store_true',help='Use the stored seller list')
    parser.add_argument('--profiles',type=int,default=0,help='Read this many unread profiles, priority order')
    args=parser.parse_args();run(not args.skip_catalogue,args.profiles)

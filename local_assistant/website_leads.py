"""Company websites for suppliers that are not on BuyICT, each with the evidence that ties it to the ABN.

  python website_leads.py --wikidata --related        # free sources
  python website_leads.py --search 200                # Brave Search for the next 200 priority suppliers
                                                      # (needs BRAVE_API_KEY in the project .env)

Sources, strongest first:
  wikidata        the ABN is stated on the company's Wikidata item, which also states its official website
  web_search      a search result whose own pages show the supplier's ABN ('ABN shown on website')
  related_seller  a BuyICT seller with the same name stem but another ABN (a related company; review)
  web_search      a search result whose pages show the supplier's name but not its ABN ('name match only')

Search results are used only to find a candidate site; what is stored is our own check of that site.
contact_discovery.py crawls the stored websites; route_review.py shows the source for review.
"""
import argparse,json,os,re,sqlite3,time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
import assistant,contact_discovery
from buyict_sellers import clean_website

SCHEMA='''CREATE TABLE IF NOT EXISTS website_leads(
 abn TEXT,website TEXT,source TEXT,identity TEXT,strength INTEGER,source_url TEXT,evidence_url TEXT,checked_at_utc TEXT,
 PRIMARY KEY(abn,website));
CREATE TABLE IF NOT EXISTS website_search_log(
 abn TEXT PRIMARY KEY,query TEXT,searched_at_utc TEXT,outcome TEXT);'''
# Lower is stronger; contact_discovery uses the strongest lead per ABN.
STRENGTH={'wikidata':1,'abn_on_site':2,'related_seller':3,'name_only':4}
WIKIDATA='https://query.wikidata.org/sparql'
BRAVE='https://api.search.brave.com/res/v1/web/search'
# Directories, registries and social sites list companies but are not the company's own website.
NOT_COMPANY_SITES=re.compile(r'(^|\.)(abr\.business\.gov\.au|abn-?lookup|abnlookup|abn\.business|opencorporates|linkedin|facebook|'
    r'instagram|twitter|x\.com|youtube|wikipedia|wikidata|yellowpages|truelocal|hotfrog|dnb\.com|zoominfo|crunchbase|'
    r'glassdoor|seek\.com|indeed|tenders\.gov\.au|buyict|gov\.au|asic|bloomberg|companieslist|aussiebusiness|'
    r'australiancompanies|abnregister|businessdirectory|startpage|cylex|localsearch|whitepages|apollo\.io|rocketreach|'
    r'signalhire|lusha|f6s|tracxn|pitchbook|craft\.co|owler|cbinsights|dandb|bizdb|infobel|kompass)(\.|$)',re.I)
STOP=(r'\b(pty|ltd|limited|p/l|pl|the|trustee|for|trust|australia|australian|aust|holdings|group|services|solutions|'
      r'consulting|international|corporation|corp|inc|co|company|of|and|t/a|as|unit|unlimited)\b')

def now():return datetime.now(timezone.utc).isoformat()
def stem(name):
    """'HAYS SPECIALIST RECRUITMENT (AUSTRALIA) PTY LTD' -> 'hays specialist'."""
    text=re.sub(r'\(.*?\)','',str(name or '').lower());text=re.sub(STOP,' ',text.replace('&',' '))
    return ' '.join(re.sub(r'[^a-z0-9 ]',' ',text).split()[:2])

def gap(connection):
    """Priority suppliers (MMR primes, active ICT/labour hire) without a BuyICT website, MMR first, then by contracts."""
    connection.execute('ATTACH DATABASE ? AS intel',((assistant.DB_DIR/'intelligence.db').resolve().as_uri()+'?mode=ro',))
    connection.execute('ATTACH DATABASE ? AS api',((assistant.DB_DIR/'contract_api.db').resolve().as_uri()+'?mode=ro',))
    try:
        return connection.execute("""WITH active AS (SELECT supplier_abn abn,COUNT(*) n FROM api.contracts WHERE current_end_date>=date('now')
                AND category_family IN ('ICT services','labour hire and recruitment','ICT equipment and general') GROUP BY 1),
            wanted AS (SELECT abn FROM intel.mmr_primes UNION SELECT abn FROM active)
            SELECT w.abn,s.name,s.supplier_key,w.abn IN (SELECT abn FROM intel.mmr_primes) mmr,COALESCE(a.n,0) contracts
            FROM wanted w JOIN suppliers s ON s.abn=w.abn LEFT JOIN active a ON a.abn=w.abn
            WHERE w.abn NOT IN (SELECT abn FROM buyict_sellers WHERE website!='')
            ORDER BY mmr DESC,contracts DESC,s.name COLLATE NOCASE""").fetchall()
    finally:
        connection.execute('DETACH DATABASE intel');connection.execute('DETACH DATABASE api')

def store(connection,abn,website,source,identity,source_url,evidence_url=''):
    website=clean_website(website)
    if not website:return 0
    kind='wikidata' if source=='wikidata' else source if source=='related_seller' else ('abn_on_site' if identity.startswith('ABN shown') else 'name_only')
    before=connection.total_changes
    connection.execute('''INSERT INTO website_leads VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(abn,website) DO UPDATE SET
        source=excluded.source,identity=excluded.identity,strength=MIN(strength,excluded.strength),source_url=excluded.source_url,
        evidence_url=excluded.evidence_url,checked_at_utc=excluded.checked_at_utc''',
        (abn,website,source,identity,STRENGTH[kind],source_url,evidence_url,now()))
    return connection.total_changes-before

def wikidata(connection,session=None):
    """ABN (P3548) and official website (P856) stated on the same Wikidata item, for priority suppliers without a
    BuyICT website; Australian domains preferred."""
    session=session or requests.Session()
    query='SELECT ?item ?abn ?site WHERE { ?item wdt:P3548 ?abn . ?item wdt:P856 ?site . }'
    response=session.get(WIKIDATA,params={'query':query},timeout=120,
                         headers={'Accept':'application/sparql-results+json','User-Agent':contact_discovery.AGENT})
    response.raise_for_status()
    by_abn={}
    for row in response.json()['results']['bindings']:
        abn=re.sub(r'\D','',row['abn']['value'])
        if len(abn)==11:by_abn.setdefault(abn,[]).append((row['site']['value'],row['item']['value']))
    known={row[0] for row in gap(connection)}
    added=0
    for abn,sites in by_abn.items():
        if abn not in known:continue
        site,item=sorted(sites,key=lambda s:('.au' not in (urlsplit(s[0]).hostname or ''),s[0]))[0]
        added+=store(connection,abn,site,'wikidata','ABN stated on Wikidata item (community-edited)',item)
    return added

def related_sellers(connection,check=None):
    """A gap supplier whose name stem equals a BuyICT seller's: that seller's website, marked as a related company,
    or as 'ABN shown on website' when the site itself shows this supplier's ABN."""
    check=check or verify_site
    sellers={}
    for name,legal,abn,site,url in connection.execute("SELECT name,legal_name,abn,website,source_url FROM buyict_sellers WHERE website!=''"):
        for value in (name,legal):
            key=stem(value)
            if len(key)>=4:sellers.setdefault(key,(name,abn,site,url))
    added=0
    for abn,name,*_ in gap(connection):
        match=sellers.get(stem(name))
        if match and match[1]!=abn:
            try:identity,evidence=check(clean_website(match[2]),abn,name)
            except Exception:identity,evidence=None,''
            if identity and identity.startswith('ABN shown'):
                added+=store(connection,abn,match[2],'related_seller_site',identity,match[3],evidence)
                continue
            added+=store(connection,abn,match[2],'related_seller',
                         f'Related company: BuyICT seller "{match[0]}" (ABN {match[1]}) has the same name; review the link',match[3])
    return added

def verify_site(website,abn,name,session=None,pages=4):
    """Fetch a candidate's home page and its contact/about/privacy/terms pages (robots obeyed) and look for the ABN, then the name."""
    session=session or requests.Session()
    allowed={contact_discovery.host(website)};origin=urlsplit(website).scheme+'://'+urlsplit(website).netloc
    robot=contact_discovery.RobotFileParser();robot_url=origin+'/robots.txt'
    for _ in range(4):
        contact_discovery.safe_url(robot_url,allowed)
        r=session.get(robot_url,headers={'User-Agent':contact_discovery.AGENT},timeout=15,allow_redirects=False)
        if r.status_code not in (301,302,303,307,308):break
        robot_url=contact_discovery.follow(robot_url,requests.compat.urljoin(robot_url,r.headers.get('Location','')),allowed)
    if r.status_code==200 and 'html' not in r.headers.get('Content-Type','').lower():robot.parse(r.text.splitlines())
    elif r.status_code in (200,404,410):robot.parse([])
    else:raise ValueError('Cannot establish robots permission: HTTP '+str(r.status_code))
    queue=[website];seen=set();name_page=''
    words=[w for w in stem(name).split() if len(w)>=3]
    while queue and len(seen)<pages:
        url=queue.pop(0)
        if url in seen or not robot.can_fetch(contact_discovery.AGENT,url):continue
        seen.add(url)
        try:resolved,response=contact_discovery.get_public(session,url,allowed,robot)
        except (ValueError,OSError,requests.RequestException):continue
        if 'html' not in response.headers.get('Content-Type','').lower():continue
        if abn in re.sub(r'\D','',response.text):return 'ABN shown on website',resolved
        text=response.text.lower()
        if words and all(w in text for w in words) and not name_page:name_page=resolved
        if url==website:
            for link in re.findall(r'href=["\']([^"\'#]+)',response.text):
                target=requests.compat.urljoin(resolved,link)
                if (contact_discovery.host(target) in allowed and re.search(r'contact|about|privacy|terms|legal',target,re.I)
                        and target not in queue):queue.append(target)
            queue.sort(key=lambda u:(0 if re.search(r'privacy|terms|legal',u,re.I) else 1 if 'contact' in u.lower() else 2))
        time.sleep(1)
    if name_page:return 'Name shown on website; ABN not found (name match only)',name_page
    return None,''

def candidate_sites(results,limit=3):
    """Distinct company-looking hosts from search results, in rank order."""
    hosts=[]
    for result in results:
        url=result.get('url','');host=contact_discovery.host(url)
        if not host or NOT_COMPANY_SITES.search(host) or any(host==h for h,_ in hosts):continue
        hosts.append((host,'https://'+(urlsplit(url).hostname or host)+'/'))
        if len(hosts)>=limit:break
    return [site for _,site in hosts]

def search(connection,limit,key,session=None,wait=1.1):
    """Brave Search for gap suppliers with no stronger lead yet; each candidate site is checked by verify_site."""
    session=session or requests.Session();done=0
    searched={r[0] for r in connection.execute('SELECT abn FROM website_search_log')}
    leads={r[0] for r in connection.execute('SELECT abn FROM website_leads WHERE strength<=2')}
    for abn,name,*_ in gap(connection):
        if done>=limit:break
        if abn in searched or abn in leads:continue
        query='"'+' '.join(name.split())+'" ABN '+abn
        response=session.get(BRAVE,params={'q':query,'country':'AU','count':10},timeout=30,
                             headers={'Accept':'application/json','X-Subscription-Token':key})
        if response.status_code==429:time.sleep(5);continue
        response.raise_for_status();done+=1
        sites=candidate_sites(((response.json().get('web') or {}).get('results')) or [])
        outcome='no company site in results'
        for site in sites:
            try:identity,evidence=verify_site(site,abn,name)
            except Exception as exc:identity,evidence=None,'';outcome=f'{site}: {type(exc).__name__}'
            if identity:
                store(connection,abn,site,'web_search',identity,'Brave Search: '+query,evidence)
                outcome=identity+': '+site
                if identity.startswith('ABN shown'):break
        connection.execute('INSERT OR REPLACE INTO website_search_log VALUES (?,?,?,?)',(abn,query,now(),outcome[:300]))
        connection.commit();time.sleep(wait)
    return done

def api_key():
    key=os.environ.get('BRAVE_API_KEY')
    if not key:
        env=Path(__file__).resolve().parent.parent/'.env'
        if env.is_file():
            match=re.search(r'^BRAVE_API_KEY\s*=\s*(\S+)',env.read_text(encoding='utf-8'),re.M)
            key=match.group(1).strip('"\'') if match else None
    return key

def run(use_wikidata=False,use_related=False,searches=0):
    summary={'checked_at_utc':now()}
    with closing(sqlite3.connect((assistant.DB_DIR/'company_contacts.db').resolve().as_uri(),uri=True,timeout=60)) as connection:
        connection.executescript(SCHEMA)
        if use_wikidata:summary['wikidata_added']=wikidata(connection);connection.commit()
        if use_related:summary['related_added']=related_sellers(connection);connection.commit()
        if searches:
            key=api_key()
            if not key:raise SystemExit('BRAVE_API_KEY is not set (project .env or environment)')
            summary['searches_this_run']=search(connection,searches,key)
        summary['leads_by_source']=dict(connection.execute(
            "SELECT source||' / strength '||strength,COUNT(*) FROM website_leads GROUP BY 1").fetchall())
        summary['gap_without_lead']=sum(1 for row in gap(connection)
            if not connection.execute('SELECT 1 FROM website_leads WHERE abn=?',(row[0],)).fetchone())
    print(json.dumps(summary),flush=True)
    return summary

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--wikidata',action='store_true');parser.add_argument('--related',action='store_true')
    parser.add_argument('--search',type=int,default=0,help='Brave searches to run this time (priority order)')
    args=parser.parse_args();run(args.wikidata,args.related,args.search)

"""Resumable missing-contact workflow; no guessed domains, emails or outreach.

Website URLs must first be linked to a supplier identity in a reviewed source map.
The crawler follows observed same-host contact/team links and obeys robots.txt.
Extracted contacts remain review candidates, not automatically trusted recipients.
"""
import argparse,hashlib,ipaddress,json,re,socket,sqlite3,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from contextlib import closing
from datetime import datetime,timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit,urljoin,unquote,quote
from urllib.robotparser import RobotFileParser
import requests
import assistant

ROOT=Path(__file__).parent
MAP=ROOT/'inputs'/'supplier_websites.json'
AGENT='Illoura-public-business-contact-research/1.0'
SCHEMA='''CREATE TABLE IF NOT EXISTS contact_research_queue(
 supplier_key TEXT PRIMARY KEY,website_url TEXT,identity_source_url TEXT,status TEXT,
 last_attempt_utc TEXT,detail TEXT,search_url TEXT);
CREATE TABLE IF NOT EXISTS contact_candidates(
 supplier_key TEXT,kind TEXT,value TEXT,purpose TEXT,source_url TEXT,source_sha256 TEXT,
 checked_at_utc TEXT,locator TEXT,review_status TEXT,
 PRIMARY KEY(supplier_key,kind,value,source_url));'''

def now():return datetime.now(timezone.utc).isoformat()
def host(url):return (urlsplit(url).hostname or '').lower().removeprefix('www.')
SUFFIXES={'com.au','net.au','org.au','edu.au','gov.au','asn.au','id.au','co.nz','co.uk','com','net','org','au','nz','io','co','ai'}
def brand(name):
    """'www.data3.com.au' -> 'data3': the label before the public suffix."""
    labels=name.split('.')
    while len(labels)>1 and ('.'.join(labels[-2:]) in SUFFIXES or labels[-1] in SUFFIXES):
        labels=labels[:-2] if '.'.join(labels[-2:]) in SUFFIXES else labels[:-1]
    return labels[-1]
def follow(current,target,allowed):
    """Accept a redirect only to a host already allowed, or to the same brand's other domain (data3.com.au -> data3.com)."""
    if host(target) in allowed:return target
    def same(a,b):return a==b or (min(len(a),len(b))>=5 and (a.startswith(b) or b.startswith(a)))
    if any(brand(name) in host(target).split('.') or same(brand(name),brand(host(target))) for name in allowed):
        allowed.add(host(target));return target
    raise ValueError('Redirects to a different company host: '+host(target))
def safe_url(url,allowed_host):
    parsed=urlsplit(url);allowed=allowed_host if isinstance(allowed_host,set) else {allowed_host}
    if parsed.scheme!='https' or parsed.username or parsed.password or host(url) not in allowed:
        raise ValueError('Only observed HTTPS URLs on the reviewed company host are allowed')
    for result in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:raise ValueError('Non-public destination rejected')
    return url

LANDLINE=re.compile(r'(?<![\d+])(?:\+61[\s.-]?(?:\(0?[2378]\)|\(0\)[\s.-]?[2378]|0?[2378])|\(0[2378]\)|0[2378])[\s.-]?\d{4}[\s.-]?\d{4}(?!\d)'
                    r'|(?<![\d+])(?:\+61[\s.-]?)?1[38]00[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)')
TEXT_MAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')

def text_contacts(text):
    """Business landlines/1300/1800 numbers and email addresses printed as text; fax numbers and mobiles excluded."""
    phones=[m.group(0).strip() for m in LANDLINE.finditer(text)
            if not re.search(r'\bfax\b|\bf\s*[:.]',text[max(0,m.start()-15):m.start()],re.I)]
    mails=[m.group(0).rstrip('.') for m in TEXT_MAIL.finditer(text)
           if not re.search(r'\.(png|jpe?g|gif|svg|webp)$',m.group(0),re.I)]
    return mails,phones

# A word that begins a part of the local name (info, sales.team); short words must be the whole part (hr, act);
# office place names and long stems may appear anywhere (DFPAdelaide, procurementteam).
FUNCTION_START=re.compile(r'(info|contact|hello|admin|office|sales|business|partner|supplier|career|jobs|talent|people|'
                          r'support|service|media|marketing|accounts|finance|general|mail|request|sayhi)')
FUNCTION_WORD={'hr','bd','act','nsw','vic','qld','wa','sa','tas','nt','team','hi'}
FUNCTION_ANYWHERE=re.compile(r'enquir|inquir|recruit|reception|procure|tender|canberra|sydney|melbourne|perth|brisbane|'
                             r'adelaide|hobart|darwin')
MOBILE=re.compile(r'^(?:\+?61|0)4')

def functional_local(local):
    parts=[p for p in re.split(r'[._+-]',local) if p]
    return bool(FUNCTION_ANYWHERE.search(local) or any(FUNCTION_START.match(p) or p in FUNCTION_WORD for p in parts))

def purpose(kind,value,site_host):
    """What a found value is, so review starts with company channels rather than individuals."""
    if kind=='email':
        local,_,domain=value.lower().partition('@');domain=domain.removeprefix('www.')
        sites=site_host if isinstance(site_host,set) else {site_host}
        if not any(domain==s or domain.endswith('.'+s) or s.endswith('.'+domain) for s in sites):
            return 'email domain differs from company website'
        if re.search(r'privacy|dpo|legal|abuse',local):return 'privacy/legal mailbox'
        if functional_local(local) or (len(brand(domain))>=4 and brand(domain) in local):return 'functional business mailbox'
        if re.fullmatch(r'[a-z]+[._-][a-z]+',local):return 'named individual: confirm role on source page'
        return 'unclear mailbox: review'
    digits=re.sub(r'[^\d+]','',value).replace('+0','0',1)
    if LANDLINE.fullmatch(value.strip()) or re.fullmatch(r'(61)?0?[2378]\d{8}|(61)?1[38]00\d{6}',digits.lstrip('+')):
        return 'Australian business line'
    if MOBILE.match(digits):return 'mobile: confirm it is a business line'
    return 'non-Australian or unrecognised number'

class ContactHTML(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.mail=[];self.phone=[];self.text=[];self.skip=0
    def handle_data(self,data):
        if not self.skip:self.text.append(data)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','template'):self.skip+=1
        if tag=='a' and not self.skip:
            link=attrs.get('href','')
            if link.lower().startswith('mailto:'):
                value=unquote(link[7:]).split('?',1)[0].strip()
                if re.fullmatch(r'[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',value):self.mail.append(value)
            elif link.lower().startswith('tel:'):
                value=unquote(link[4:]).strip()
                if re.fullmatch(r'\+?[\d ()-]{7,25}',value):self.phone.append(value)
            elif re.search(r'contact|enquir|get-in-touch|office|location|about|team|people|leadership',link,re.I):self.links.append(link)
    def handle_endtag(self,tag):
        if tag in ('script','style','template') and self.skip:self.skip-=1

def get_public(session,url,allowed_host,robot=None):
    for _ in range(4):
        safe_url(url,allowed_host)
        if robot is not None and not robot.can_fetch(AGENT,url):
            raise ValueError('Destination disallowed by robots')
        response=session.get(url,headers={'User-Agent':AGENT},timeout=18,allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            target=urljoin(url,response.headers.get('Location',''))
            url=follow(url,target,allowed_host) if isinstance(allowed_host,set) else target;continue
        if response.status_code!=200:raise ValueError('HTTP '+str(response.status_code))
        if len(response.content)>2_000_000:raise ValueError('Page exceeds 2 MB')
        return url,response
    raise ValueError('Redirect limit exceeded')

def link_rank(url):
    """Contact pages first, then enquiry/office pages, then people pages, then general 'about' pages."""
    path=urlsplit(url).path.lower()
    for rank,pattern in enumerate([r'contact|get-in-touch',r'enquir|office|location',r'team|people|leadership']):
        if re.search(pattern,path):return rank
    return 3

def discover(website,session=None,limit=5):
    session=session or requests.Session();allowed={host(website)}
    origin=urlsplit(website).scheme+'://'+urlsplit(website).netloc
    robot=RobotFileParser();robot_url=origin+'/robots.txt'
    for _ in range(4):
        # robots.txt often redirects to the www/https form or the brand's global domain; follow only those.
        safe_url(robot_url,allowed)
        r=session.get(robot_url,headers={'User-Agent':AGENT},timeout=15,allow_redirects=False)
        if r.status_code not in (301,302,303,307,308):break
        robot_url=follow(robot_url,urljoin(robot_url,r.headers.get('Location','')),allowed)
    if r.status_code==200 and 'html' not in r.headers.get('Content-Type','').lower():robot.parse(r.text.splitlines())
    elif r.status_code in (200,404,410):robot.parse([])  # an HTML page in place of robots.txt means there is none
    else:raise ValueError('Cannot establish robots permission: HTTP '+str(r.status_code))
    wait=max(1,robot.crawl_delay(AGENT) or robot.crawl_delay('*') or 1)
    if wait>30:raise ValueError('Long crawl delay: manual source review required')
    pending=[website];seen=set();found=[];events=[]
    while pending and len(seen)<limit:
        url=pending.pop(0)
        if url in seen:continue
        seen.add(url)
        if not robot.can_fetch(AGENT,url):events.append({'url':url,'status':'robots disallows'});continue
        try:
            resolved,response=get_public(session,url,allowed,robot)
            seen.add(resolved)
            if not robot.can_fetch(AGENT,resolved):raise ValueError('Redirect destination disallowed by robots')
            if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('Not an HTML contact source')
            parser=ContactHTML();parser.feed(response.text)
            digest=hashlib.sha256(response.content).hexdigest();checked=now()
            published=[('email',v,'published mailto anchor') for v in set(parser.mail)]+[('phone',v,'published tel anchor') for v in set(parser.phone)]
            if url==website or link_rank(resolved)<=1:
                # Text is read only on the home page and contact/office pages, never on team or people pages.
                mails,phones=text_contacts(' '.join(parser.text))
                anchored={v.lower() for _,v,_ in published}|{re.sub(r'\D','',v)[-9:] for k,v,_ in published if k=='phone'}
                published+=[('email',v,'published email text') for v in set(mails) if v.lower() not in anchored]
                published+=[('phone',v,'published phone text') for v in set(phones) if re.sub(r'\D','',v)[-9:] not in anchored]
            for kind,value,locator in published:
                found.append({'kind':kind,'value':value,'purpose':purpose(kind,value,allowed),'source_url':resolved,
                    'source_sha256':digest,'checked_at_utc':checked,'locator':locator,'review_status':'candidate_not_reviewed'})
            for link in parser.links:
                candidate=urljoin(resolved,link).split('#',1)[0]
                if host(candidate) in allowed and urlsplit(candidate).scheme=='https' and candidate not in seen:
                    pending.append(candidate)
            pending.sort(key=link_rank)
            events.append({'url':resolved,'status':'fetched','candidates':len(published),'sha256':digest})
        except (ValueError,OSError,requests.RequestException) as exc:events.append({'url':url,'status':'not fetched','reason':str(exc)})
        if pending:time.sleep(wait)
    return found,events

def relabel(connection):
    """Re-score stored candidates with the current rules, against the page host and the supplier's website host."""
    rows=connection.execute('''SELECT c.rowid,c.kind,c.value,c.source_url,c.purpose,COALESCE(q.website_url,'') FROM contact_candidates c
        LEFT JOIN contact_research_queue q ON q.supplier_key=c.supplier_key''').fetchall()
    changes=[(new,row[0]) for row in rows
             if (new:=purpose(row[1],row[2],{h for h in (host(row[3]),host(row[5])) if h}))!=row[4]]
    connection.executemany('UPDATE contact_candidates SET purpose=? WHERE rowid=?',changes)
    return len(changes)

def run(crawl=False,limit=20,workers=6):
    mapping=json.loads(MAP.read_text(encoding='utf-8'))['records'] if MAP.is_file() else []
    by_abn={r['supplier_abn']:r for r in mapping if r.get('identity_status')=='reviewed'}
    summaries=[]
    with closing(sqlite3.connect(assistant.DB_DIR/'company_contacts.db',timeout=60)) as connection:
        connection.row_factory=sqlite3.Row;connection.executescript(SCHEMA)
        # Second identity bridge: the website a seller published against its ABN on BuyICT.
        # The reviewed map wins where both exist.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='buyict_sellers'").fetchone():
            for seller in connection.execute("SELECT abn,website,source_url FROM buyict_sellers WHERE abn!='' AND website!=''"):
                by_abn.setdefault(seller['abn'],{'website_url':seller['website'],'identity_source_url':seller['source_url']})
        connection.execute("""UPDATE contact_research_queue
            SET status='route_established',detail='Reviewed approachable route stored separately.'
            WHERE supplier_key IN (SELECT supplier_key FROM approachable_routes)""")
        suppliers=list(connection.execute("""SELECT supplier_key,name,abn FROM suppliers
            WHERE supplier_key NOT IN (SELECT DISTINCT supplier_key FROM supplier_contacts
                WHERE kind LIKE '%email%' OR kind LIKE '%phone%')
              AND supplier_key NOT IN (SELECT supplier_key FROM approachable_routes)
            ORDER BY latest_publication DESC,notice_count DESC,name COLLATE NOCASE"""))
        for row in suppliers:
            known=by_abn.get(row['abn'],{})
            query='"'+row['name']+'" '+row['abn']+' official contact email telephone'
            connection.execute('INSERT OR IGNORE INTO contact_research_queue VALUES (?,?,?,?,?,?,?)',
                (row['supplier_key'],known.get('website_url',''),known.get('identity_source_url',''),
                 'website_established' if known else 'website_not_established',None,'',
                 'https://www.google.com/search?q='+quote(query)))
            if known:connection.execute('''UPDATE contact_research_queue SET website_url=?,identity_source_url=?,
                    status=CASE status WHEN 'website_not_established' THEN 'website_established' ELSE status END
                    WHERE supplier_key=?''',(known['website_url'],known['identity_source_url'],row['supplier_key']))
        relabel(connection);connection.commit()
        if crawl:
            queue=connection.execute("""SELECT q.* FROM contact_research_queue q
                JOIN suppliers s ON s.supplier_key=q.supplier_key
                WHERE q.website_url!='' AND q.status='website_established'
                ORDER BY s.latest_publication DESC,s.notice_count DESC,s.name COLLATE NOCASE
                LIMIT ?""",(limit,)).fetchall()
            # Several companies at once, but one host is only ever crawled by one worker, one page at a time.
            # Group ABNs that share a website are crawled once.
            sites={}
            for entry in queue:sites.setdefault(entry['website_url'],[]).append(entry)
            hosts={}
            for website in sites:hosts.setdefault(host(website),[]).append(website)
            def crawl_host(websites):
                results=[]
                for website in websites:
                    # One dead or hostile site (DNS failure, TLS error, bad markup) must not stop the run.
                    try:results.append((website,*discover(website),None))
                    except Exception as exc:results.append((website,[],[],f'{type(exc).__name__}: {exc}'[:500]))
                return results
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for job in as_completed([pool.submit(crawl_host,websites) for websites in hosts.values()]):
                    for website,found,events,error in job.result():
                        status=('blocked_or_unavailable' if error else 'candidates_found' if found else 'no_published_contact_found')
                        for entry in sites[website]:
                            for candidate in found:
                                connection.execute('INSERT OR REPLACE INTO contact_candidates VALUES (?,?,?,?,?,?,?,?,?)',
                                    (entry['supplier_key'],candidate['kind'],candidate['value'],candidate['purpose'],candidate['source_url'],
                                     candidate['source_sha256'],candidate['checked_at_utc'],candidate['locator'],candidate['review_status']))
                            connection.execute('UPDATE contact_research_queue SET status=?,last_attempt_utc=?,detail=? WHERE supplier_key=?',
                                               (status,now(),error or json.dumps(events),entry['supplier_key']))
                            summaries.append({'supplier_key':entry['supplier_key'],'status':status})
                        connection.commit()
        counts={row[0]:row[1] for row in connection.execute('SELECT status,count(*) FROM contact_research_queue GROUP BY status')}
        candidates=connection.execute('SELECT count(*) FROM contact_candidates').fetchone()[0]
    result={'checked_at_utc':now(),'queue_counts':counts,'candidate_routes':candidates,'attempts_this_run':summaries,
            'limit':'Search links are a research queue, not source proof. Candidates need identity/purpose review. No guessed emails, login bypass, bought lists, LinkedIn scraping or sending.'}
    reports=ROOT/'reports';reports.mkdir(exist_ok=True)
    (reports/'contact-discovery.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--crawl-known-sites',action='store_true');parser.add_argument('--limit',type=int,default=20)
    parser.add_argument('--workers',type=int,default=6,help='Different company sites crawled at once')
    args=parser.parse_args();run(args.crawl_known_sites,args.limit,args.workers)

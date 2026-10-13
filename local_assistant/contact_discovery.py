"""Resumable missing-contact workflow; no guessed domains, emails or outreach.

Website URLs must first be linked to a supplier identity in a reviewed source map.
The crawler follows observed same-host contact/team links and obeys robots.txt.
Extracted contacts remain review candidates, not automatically trusted recipients.
"""
import argparse,hashlib,ipaddress,json,re,socket,sqlite3,time
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
def safe_url(url,allowed_host):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password or host(url)!=allowed_host:
        raise ValueError('Only observed HTTPS URLs on the reviewed company host are allowed')
    for result in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(result[4][0]).is_global:raise ValueError('Non-public destination rejected')
    return url

class ContactHTML(HTMLParser):
    def __init__(self):super().__init__();self.links=[];self.mail=[];self.phone=[];self.skip=0
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
            elif re.search(r'contact|enquir|about|team',link,re.I):self.links.append(link)
    def handle_endtag(self,tag):
        if tag in ('script','style','template') and self.skip:self.skip-=1

def get_public(session,url,allowed_host,robot=None):
    for _ in range(4):
        safe_url(url,allowed_host)
        if robot is not None and not robot.can_fetch(AGENT,url):
            raise ValueError('Destination disallowed by robots')
        response=session.get(url,headers={'User-Agent':AGENT},timeout=18,allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            url=urljoin(url,response.headers.get('Location',''));continue
        if response.status_code!=200:raise ValueError('HTTP '+str(response.status_code))
        if len(response.content)>2_000_000:raise ValueError('Page exceeds 2 MB')
        return url,response
    raise ValueError('Redirect limit exceeded')

def discover(website,session=None,limit=5):
    session=session or requests.Session();allowed=host(website)
    origin=urlsplit(website).scheme+'://'+urlsplit(website).netloc
    robot=RobotFileParser();robot_url=origin+'/robots.txt'
    safe_url(robot_url,allowed)
    r=session.get(robot_url,headers={'User-Agent':AGENT},timeout=15,allow_redirects=False)
    if r.status_code==200:robot.parse(r.text.splitlines())
    elif r.status_code in (404,410):robot.parse([])
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
            if not robot.can_fetch(AGENT,resolved):raise ValueError('Redirect destination disallowed by robots')
            if 'text/html' not in response.headers.get('Content-Type',''):raise ValueError('Not an HTML contact source')
            parser=ContactHTML();parser.feed(response.text)
            digest=hashlib.sha256(response.content).hexdigest();checked=now()
            for kind,values in [('email',parser.mail),('phone',parser.phone)]:
                for value in set(values):
                    purpose='privacy/legal mailbox' if kind=='email' and re.search(r'privacy|dpo|legal|abuse',value,re.I) else 'purpose requires source review'
                    found.append({'kind':kind,'value':value,'purpose':purpose,'source_url':resolved,
                        'source_sha256':digest,'checked_at_utc':checked,'locator':'published '+('mailto' if kind=='email' else 'tel')+' anchor',
                        'review_status':'candidate_not_reviewed'})
            for link in parser.links:
                candidate=urljoin(resolved,link).split('#',1)[0]
                if host(candidate)==allowed and urlsplit(candidate).scheme=='https' and candidate not in seen:
                    pending.append(candidate)
            events.append({'url':resolved,'status':'fetched','candidates':len(parser.mail)+len(parser.phone),'sha256':digest})
        except (ValueError,requests.RequestException) as exc:events.append({'url':url,'status':'not fetched','reason':str(exc)})
        if pending:time.sleep(wait)
    return found,events

def run(crawl=False,limit=20):
    mapping=json.loads(MAP.read_text(encoding='utf-8'))['records'] if MAP.is_file() else []
    by_abn={r['supplier_abn']:r for r in mapping if r.get('identity_status')=='reviewed'}
    summaries=[]
    with closing(sqlite3.connect(assistant.DB_DIR/'company_contacts.db')) as connection:
        connection.row_factory=sqlite3.Row;connection.executescript(SCHEMA)
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
            if known:connection.execute('UPDATE contact_research_queue SET website_url=?,identity_source_url=? WHERE supplier_key=?',
                                        (known['website_url'],known['identity_source_url'],row['supplier_key']))
        connection.commit()
        if crawl:
            queue=connection.execute("""SELECT q.* FROM contact_research_queue q
                JOIN suppliers s ON s.supplier_key=q.supplier_key
                WHERE q.website_url!='' AND q.status NOT IN ('candidates_found','route_established')
                ORDER BY s.latest_publication DESC,s.notice_count DESC,s.name COLLATE NOCASE
                LIMIT ?""",(limit,)).fetchall()
            for entry in queue:
                try:
                    found,events=discover(entry['website_url'])
                    for candidate in found:
                        connection.execute('INSERT OR REPLACE INTO contact_candidates VALUES (?,?,?,?,?,?,?,?,?)',
                            (entry['supplier_key'],candidate['kind'],candidate['value'],candidate['purpose'],candidate['source_url'],
                             candidate['source_sha256'],candidate['checked_at_utc'],candidate['locator'],candidate['review_status']))
                    status='candidates_found' if found else 'no_published_anchor_contact_found'
                    detail=json.dumps(events)
                except (ValueError,requests.RequestException) as exc:status='blocked_or_unavailable';detail=str(exc)
                connection.execute('UPDATE contact_research_queue SET status=?,last_attempt_utc=?,detail=? WHERE supplier_key=?',
                                    (status,now(),detail,entry['supplier_key']));connection.commit()
                summaries.append({'supplier_key':entry['supplier_key'],'status':status})
        counts={row[0]:row[1] for row in connection.execute('SELECT status,count(*) FROM contact_research_queue GROUP BY status')}
        candidates=connection.execute('SELECT count(*) FROM contact_candidates').fetchone()[0]
    result={'checked_at_utc':now(),'queue_counts':counts,'candidate_routes':candidates,'attempts_this_run':summaries,
            'limit':'Search links are a research queue, not source proof. Candidates need identity/purpose review. No guessed emails, login bypass, bought lists, LinkedIn scraping or sending.'}
    reports=ROOT/'reports';reports.mkdir(exist_ok=True)
    (reports/'contact-discovery.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--crawl-known-sites',action='store_true');parser.add_argument('--limit',type=int,default=20)
    args=parser.parse_args();run(args.crawl_known_sites,args.limit)

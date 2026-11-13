"""Collect allowlisted official research pages/PDFs with robots and provenance.

No login, form submissions, outreach, guessed contacts, or resume processing.
"""
import argparse,hashlib,json,re,sqlite3,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin,urlsplit
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(r'/opt/aus_tender');OUT=Path(__file__).resolve().parents[1]/'reports/collection_run_20261022'
AGENT='Illoura-official-source-research/1.0'
SEEDS=[
('LEIDOS','onboarding','https://www.leidos.com/company/global/australia/partner-network'),
('DEFENCE','subcontractors','https://www.defence.gov.au/business-industry/finding-opportunities/sub-contractor-opportunities'),
('DEFENCE','Indigenous supplier route','https://www.defence.gov.au/business-industry/finding-opportunities/encouraging-indigenous-business'),
('TEAM NOVA','onboarding','https://novasystems.com/team-nova'),
('DXC','onboarding','https://dxc.com/contact-dxc/vendors-and-suppliers'),
('ERNST & YOUNG','RAP and collaboration','https://www.ey.com/en_au/industries/the-office-of-first-nations-engagement'),
('KPMG','Indigenous procurement','https://kpmg.com/au/en/about/values-culture/social-procurement.html'),
('DELOITTE','RAP','https://www.deloitte.com/au/en/about/people/social-responsibility/deloitte-reconciliation-action-plan.html'),
('HAYS','RAP','https://www.hays.com.au/diversity/reconciliation-action-plan'),
('NSW PROCUREMENT','ICT scheme','https://buy.nsw.gov.au/schemes/ict-services-scheme'),
('NSW PROCUREMENT','contingent workforce scheme','https://buy.nsw.gov.au/scheme/65CDBCCA-EA61-2354-9ECAFC84AE5A95B8'),
('FINANCE','People Panel','https://www.finance.gov.au/government/procurement/whole-australian-government-procurement/people-panel'),
('ANU','tender listings','https://portal.tenderlink.com/anu/alltenders/'),
('IDIC','opportunities','https://jobs.idic.com.au/'),
('CAREER CENTRE ALLIANCE','collaboration','https://www.careercentrealliance.com.au/'),
('NIAA','IPP policy','https://www.niaa.gov.au/resource-centre/indigenous-procurement-policy'),
('DTA','procurement planning','https://www.dta.gov.au/corporate-plan-2026-27')]
def stamp():return datetime.now(timezone.utc).isoformat()
def origin(url):
 s=urlsplit(url);return s.scheme+'://'+s.netloc
def extract_html(data, response_url):
 soup=BeautifulSoup(data,'html.parser')
 for el in soup(['script','style','noscript','svg']):el.decompose()
 title=soup.title.get_text(' ',strip=True) if soup.title else ''
 candidates=soup.find_all('main') or soup.find_all('article')
 main=max(candidates,key=lambda el:len(el.get_text(' ',strip=True))) if candidates else soup
 text=main.get_text('\n',strip=True)
 if len(text)<100:raise ValueError('Insufficient page content; requires manual review')
 links=[]
 for a in main.find_all('a',href=True):
  target=urljoin(response_url,a['href']);label=a.get_text(' ',strip=True)
  if target.startswith('https://') and re.search(r'RAP|reconciliation|register|supplier|partner|indigenous|panel|\.pdf(?:\?|$)',label+' '+target,re.I):
   links.append({'label':label,'url':target})
 return title,text,links

def collect_host(items):
 session=requests.Session();session.headers['User-Agent']=AGENT;robots={};last={};results=[]
 def fetch(org,kind,url,depth):
  base=origin(url);start=stamp();record={'organisation':org,'kind':kind,'source_url':url,'depth':depth,'checked_at_utc':start}
  try:
   if re.search(r'resume|curriculum|/cv[/.?]',url,re.I):raise ValueError('Resume URL excluded')
   if base not in robots:
    rp=RobotFileParser();response=session.get(base+'/robots.txt',timeout=25)
    if response.status_code==200:rp.parse(response.text.splitlines())
    elif response.status_code in (404,410):rp.parse([])
    else:raise ValueError('Cannot establish robots access: '+str(response.status_code))
    robots[base]=rp
   if not robots[base].can_fetch(AGENT,url):raise ValueError('Disallowed by robots.txt')
   time.sleep(max(0,2-(time.monotonic()-last.get(base,0))))
   response=session.get(url,timeout=35);last[base]=time.monotonic()
   if response.status_code!=200:raise ValueError('HTTP '+str(response.status_code))
   if origin(response.url)!=base:
    # Redirects are observed, but a different host needs its own permission check.
    return fetch(org,kind,response.url,depth)
   data=response.content
   if len(data)>30_000_000:raise ValueError('Document exceeds 30 MB collection limit')
   digest=hashlib.sha256(data).hexdigest();ctype=response.headers.get('content-type','');links=[]
   if 'pdf' in ctype or data[:4]==b'%PDF':
    reader=PdfReader(BytesIO(data));texts=[p.extract_text() or '' for p in reader.pages]
    text='\n\n'.join(f'[Page {i+1}]\n'+t for i,t in enumerate(texts));record['pages']=len(texts);record['image_only_pages']=sum(len(t.strip())<80 for t in texts);suffix='.pdf'
   elif 'html' in ctype or data.lstrip().startswith(b'<'):
    title,text,links=extract_html(data,response.url);record['title']=title;suffix='.html'
   else:raise ValueError('Unsupported content type: '+ctype)
   if re.search(r'verify you are human|checking your browser|access denied|captcha',text[:2500],re.I):raise ValueError('Access challenge; not bypassed')
   folder=OUT/'official_sources'
   if suffix=='.pdf':
    # Keep one content-addressed PDF; dated manifests retain their own check time.
    reports=Path(__file__).resolve().parents[1]/'reports'
    folder=reports/'shared_official_sources'
    for existing in sorted(reports.glob('*/official_sources/'+digest+'.pdf')):
     if existing.with_suffix('.txt').is_file() and hashlib.sha256(existing.read_bytes()).hexdigest()==digest:
      folder=existing.parent;break
   folder.mkdir(parents=True,exist_ok=True)
   raw_path=folder/(digest+suffix);text_path=folder/(digest+'.txt')
   if not raw_path.exists():raw_path.write_bytes(data)
   if not text_path.exists():text_path.write_text(text,encoding='utf-8')
   record.update(status='collected',final_url=response.url,response_sha256=digest,bytes=len(data),text_chars=len(text),links=links,text_path=str(folder/(digest+'.txt')))
   record['business_emails']=sorted(set(re.findall(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}',text)))
   record['evidence_snippets']=[line[:1500] for line in text.splitlines() if re.search(r'Indigenous|First Nations|register|supplier|partner|procurement|reconciliation|November|refresh',line,re.I)][:45]
  except Exception as exc:record.update(status='unavailable',error=str(exc)[:500])
  return record
 for org,kind,url in items:
  result=fetch(org,kind,url,0);results.append(result)
  if result['status']=='collected':
   # Only a small, observed set of same-host RAP/policy PDF links; no recursive site crawl.
   candidates=[]
   for link in result.get('links',[]):
    if origin(link['url'])==origin(result['final_url']) and re.search(r'\.pdf(?:\?|$)',link['url'],re.I) and re.search(r'RAP|reconciliation|IPP|policy|panel agreement',link['label']+' '+link['url'],re.I):candidates.append(link['url'])
   for target in list(dict.fromkeys(candidates))[:3]:results.append(fetch(org,'official linked PDF',target,1))
 return results

def main():
 OUT.mkdir(parents=True,exist_ok=True);groups={}
 for seed in SEEDS:groups.setdefault(origin(seed[2]),[]).append(seed)
 records=[]
 with ThreadPoolExecutor(max_workers=4) as pool:
  jobs=[pool.submit(collect_host,items) for items in groups.values()]
  for job in as_completed(jobs):
   batch=job.result();records.extend(batch)
   print(json.dumps([{'organisation':x['organisation'],'status':x['status'],'url':x['source_url'],'error':x.get('error')} for x in batch]),flush=True)
   (OUT/'official_source_collection.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
 db=sqlite3.connect(ROOT/'databases/research_sources.db')
 db.execute('''CREATE TABLE IF NOT EXISTS source_pages(source_url TEXT PRIMARY KEY,organisation TEXT,kind TEXT,checked_at_utc TEXT,status TEXT,final_url TEXT,response_sha256 TEXT,bytes INTEGER,text_content TEXT,links_json TEXT,business_emails_json TEXT,error TEXT)''')
 for r in records:
  text=Path(r['text_path']).read_text(encoding='utf-8') if r.get('text_path') else None
  db.execute('INSERT OR REPLACE INTO source_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(r['source_url'],r['organisation'],r['kind'],r['checked_at_utc'],r['status'],r.get('final_url'),r.get('response_sha256'),r.get('bytes'),text,json.dumps(r.get('links',[])),json.dumps(r.get('business_emails',[])),r.get('error')))
 db.commit();db.close();print('SOURCE COLLECTION COMPLETE',len(records),flush=True)
if __name__=='__main__':main()

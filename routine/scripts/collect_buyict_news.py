"""Read the public BuyICT news widget through its observed anonymous page calls."""
import csv
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from bs4 import BeautifulSoup

sys.path.insert(0, r'/opt/aus_tender/local_assistant')
from buyict_sellers import Portal, BASE, find_widget

OUT=Path(__file__).resolve().parents[1]/'reports/collection_run_20261022'
ROOT=Path(r'/opt/aus_tender')


def page(portal, uri):
    query=urlsplit(uri).query
    return portal.get(BASE+'/api/now/sp/page?'+(query+'&' if query else '')+
        'portal_id='+portal.portal+'&request_uri='+quote(uri,safe='')).json()


def main():
    portal=Portal()
    landing=portal.get(BASE+'/public')
    portal.portal=re.search(r"portal_id\s*=\s*'([^']+)'",landing.text)[1]
    portal.headers['x-portal']=portal.portal
    home=page(portal,'/public')
    def links(value):
        if isinstance(value,dict):
            if isinstance(value.get('url'),str):yield value['url']
            for v in value.values():yield from links(v)
        elif isinstance(value,list):
            for v in value:yield from links(v)
    news=next(u for u in links(home) if '/news?' in u and 'id=public_content' in u)
    model=page(portal,news);widget=find_widget(model,'buyict_article_listing')['widget'];data=widget['data']
    endpoint=BASE+'/api/now/sp/widget/'+widget['sys_id']+'?id=public_content'
    options={'current_page':1,'categories':[],'search_term':'','page_size':60,'sort':data['view_options'][0]}
    ids=portal.post(endpoint,{'action':'getItemIds','items':{'knowledge_base':data['items']['knowledge_base'],
        'table':data['items']['table']},'search_options':options})['data']['items']
    count=ids['count'];items=dict(data['items'],**ids);records=[]
    for number in range(1,(count+59)//60+1):
        batch=portal.post(endpoint,{'action':'getItems','items':items,
            'pagination':{'size':60,'current_page':number,'start':(number-1)*60,'end':number*60}})['data']['items']['results']
        records.extend(batch)
    assert len({r['sys_id'] for r in records})==count, 'News listing incomplete'
    checked=datetime.now(timezone.utc).isoformat()
    for r in records:r.update(checked_at_utc=checked,source_url=urljoin(BASE,r['url']))
    (OUT/'buyict_news_index.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    with (OUT/'buyict_news_index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    selected=[r for r in records if r['published']>='2026-08-01' and re.search(r'Digital Marketplace Panel 2|DMP2',r['short_description'],re.I)]
    selected+= [r for r in records if any(s in r['short_description'].lower() for s in ['seller invitation request','central administration fee reinstated','rate increase has come'])]
    selected=selected[:12]
    sources=json.loads((OUT/'official_source_collection.json').read_text(encoding='utf-8'))
    articles=[]
    for item in selected:
        result=page(portal,item['url']);wrap=find_widget(result,'buyict_article')
        article=wrap['widget']['data']['article'] if wrap else None
        if not article or not article.get('is_valid'):raise ValueError('Article not valid: '+item['source_url'])
        soup=BeautifulSoup(article['text'],'html.parser');text=soup.get_text('\n',strip=True)
        raw=json.dumps(article,ensure_ascii=False,indent=2).encode('utf-8');digest=hashlib.sha256(raw).hexdigest()
        folder=OUT/'official_sources';(folder/(digest+'.json')).write_bytes(raw)
        (folder/(digest+'.txt')).write_text(text,encoding='utf-8')
        record=dict(organisation='BUYICT',kind='official panel news',source_url=item['source_url'],depth=0,
            checked_at_utc=datetime.now(timezone.utc).isoformat(),status='collected',final_url=item['source_url'],
            response_sha256=digest,hash_scope='Extracted public article JSON from anonymous page API',bytes=len(raw),
            text_chars=len(text),title=article['title'],published=article['published'],
            text_path=str(folder/(digest+'.txt')),business_emails=[],
            links=[{'label':a.get_text(' ',strip=True),'url':urljoin(item['source_url'],a['href'])} for a in soup.find_all('a',href=True)])
        sources=[r for r in sources if r['source_url']!=record['source_url']]+[record]
        articles.append(article);print(article['number'],article['published'],article['title'],flush=True)
    (OUT/'buyict_news_articles.json').write_text(json.dumps(articles,indent=2),encoding='utf-8')
    (OUT/'official_source_collection.json').write_text(json.dumps(sources,indent=2),encoding='utf-8')
    with sqlite3.connect(ROOT/'databases/research_sources.db') as db:
        db.execute('CREATE TABLE IF NOT EXISTS buyict_news(sys_id TEXT PRIMARY KEY,title TEXT,published TEXT,source_url TEXT,synopsis TEXT,checked_at_utc TEXT)')
        db.executemany('INSERT OR REPLACE INTO buyict_news VALUES (?,?,?,?,?,?)',[(r['sys_id'],r['short_description'],r['published'],r['source_url'],r.get('synopsis',''),checked) for r in records])
        for r in sources:
            if r['organisation']!='BUYICT':continue
            db.execute('INSERT OR REPLACE INTO source_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(r['source_url'],r['organisation'],r['kind'],r['checked_at_utc'],r['status'],r['final_url'],r['response_sha256'],r['bytes'],Path(r['text_path']).read_text(encoding='utf-8'),json.dumps(r['links']),json.dumps([]),None))
    print(json.dumps({'index_rows':count,'article_bodies':len(articles),'remaining_bodies':count-len(articles)}),flush=True)


if __name__=='__main__':main()

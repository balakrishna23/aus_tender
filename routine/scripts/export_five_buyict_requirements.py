"""Export public requirements for five known opportunities, without authenticated actions."""
import csv
import hashlib
import json
import re
import sqlite3
import sys
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote,urlsplit
from bs4 import BeautifulSoup

sys.path.insert(0,r'/opt/aus_tender/local_assistant')
from buyict_sellers import Portal,BASE,find_widget

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/buyict_five_invitation_requirements_20261022'
IDS=['LH-08128','RFI-04207','LH-08145','RFI-04242','RFI-04206']
UI={'user','seller','page','accordian','contract_page','answered_questions','access_message',
    'isInvited','isSeller','isBuyer','isLoggedIn','isMarketplaceSeller','hasQuote'}


def plain(value):
    if isinstance(value,str):
        return BeautifulSoup(value,'html.parser').get_text('\n',strip=True) if re.search(r'<[a-zA-Z/][^>]*>',value) else value
    if isinstance(value,list):return [plain(v) for v in value]
    if isinstance(value,dict):
        if value.get('priv_info'):
            return {'label':value.get('label'),'access_status':'requires authenticated marketplace access'}
        return {k:plain(v) for k,v in value.items() if k not in UI and k not in {'template','client_script','css','link','docWidget'}}
    return value


def write_csv(name,rows):
    if not rows:return
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with (OUT/name).open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def main():
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'sources').mkdir(exist_ok=True)
    db=sqlite3.connect(r'/opt/aus_tender/databases/buy_ict.db');db.row_factory=sqlite3.Row
    portal=Portal();landing=portal.get(BASE+'/public')
    portal.portal=re.search(r"portal_id\s*=\s*'([^']+)'",landing.text)[1];portal.headers['x-portal']=portal.portal
    records=[];fields=[];criteria=[];access=[]
    for key in IDS:
        stored=dict(db.execute('SELECT * FROM buy_ict_tenders WHERE rfq_id=?',(key,)).fetchone())
        url=stored['url'];uri=urlsplit(url).path+'?'+urlsplit(url).query
        api=BASE+'/api/now/sp/page?'+urlsplit(url).query+'&portal_id='+portal.portal+'&request_uri='+quote(uri,safe='')
        response=portal.get(api);model=response.json();wrap=find_widget(model,'opportunity_rfq_details')
        if not wrap:raise ValueError('Opportunity widget unavailable: '+key)
        outer=wrap['widget']['data'];data=(outer.get('opp_widget') or {}).get('data') or outer
        content=plain(data)
        main={r['label']:plain(r.get('value')) for r in data.get('mainDetails',[]) if not r.get('priv_info')}
        actual=main.get('RFQ ID') or main.get('RFI ID')
        if actual!=key:raise ValueError('Returned identifier mismatch: '+key+' / '+str(actual))
        checked=datetime.now(timezone.utc).isoformat()
        visible_json=json.dumps(content,ensure_ascii=False,indent=2)
        (OUT/'sources'/f'{key}.json').write_text(visible_json,encoding='utf-8')
        reqs=content.get('requirements',[])
        for r in reqs:
            fields.append({'opportunity_id':key,'field':r.get('label'),
                'value':r.get('value'),'list_items':r.get('listItems'),
                'access_status':r.get('access_status','public'),'source_url':url,'checked_at_utc':checked})
        for group in content.get('allCriteria',[]):
            for criterion in group.get('criteria',[]):
                criteria.append({'opportunity_id':key,'group':group.get('label'),**criterion,
                    'source_url':url,'checked_at_utc':checked})
        record={'opportunity_id':key,'title':content.get('title'),'buyer':main.get('Buyer'),
            'closing_date_time_source':main.get('RFQ closing date') or main.get('RFI closing date'),
            'questions_deadline_source':main.get('Deadline for asking questions'),
            'buyer_contact_instructions':main.get('Buyer contact'),'main_details':main,'requirements':reqs,
            'description':content.get('description'),'key_duties':content.get('key_duties'),
            'response_requirements':content.get('response_requirements'),'criteria_text':content.get('criteriaText'),
            'all_criteria':content.get('allCriteria',[]),'public_page_details':content,
            'source_url':url,'checked_at_utc':checked,'visible_data_sha256':hashlib.sha256(visible_json.encode()).hexdigest(),
            'source_type':'Anonymous official BuyICT public page API; no signed-in session',
            'secure_document_access':bool(data.get('can_access_secure')),
            'attachments_reviewed':False,'full_invitation_review_complete':False}
        records.append(record)
        references=[r for r in reqs if re.search(r'attach|document|response format',str(r),re.I)]
        access.append({'opportunity_id':key,'attachment_access':'not available in anonymous public view' if not data.get('can_access_secure') else 'not yet inspected',
            'attachment_count':None,'attachments_downloaded':0,'resumes_opened':0,
            'public_attachment_references':references,'access_message':data.get('access_message'),
            'source_url':url,'checked_at_utc':checked})
        print(key,record['title'],'criteria',len([r for r in criteria if r['opportunity_id']==key]),'secure access',record['secure_document_access'],flush=True)
    package={'exported_at_utc':datetime.now(timezone.utc).isoformat(),'scope':'Public requirements for five reported invitations; secure attachments unavailable',
        'complete':False,'records':records,'attachment_access':access,
        'limits':['Brave is not connected to this chat and the Windows native pipe is unavailable.',
            'The public API explicitly reports can_access_secure=false. No gated document endpoints were requested.',
            'Absence of attachments in this export is not evidence that the buyer has attached no documents.',
            'Resume files were not opened or downloaded. A requirement to provide a resume is retained as procurement metadata.',
            'Invitation status was supplied by the user from another chat and has not been reverified here.',
            'No responses submitted; no account details changed.']}
    (OUT/'requirements.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv('requirements.csv',[{k:v for k,v in r.items() if k!='public_page_details'} for r in records])
    write_csv('requirement_fields.csv',fields);write_csv('criteria.csv',criteria);write_csv('attachment_access.csv',access)
    (OUT/'READ_ME.txt').write_text('PARTIAL EXPORT: current public BuyICT requirements for all five opportunities.\n\nSecure attachments require the authenticated marketplace session; none were accessed.\nrequirements.json preserves all publicly visible data. CSV files provide the five records, individual requirement fields, assessment criteria and attachment-access gaps.\nThe response request for resumes is metadata only. No resume files were read.\nNo submissions or account edits occurred.\n',encoding='utf-8')
    # Verify counts and every CSV field before packaging.
    assert len(records)==5 and {r['opportunity_id'] for r in records}==set(IDS)
    for filename,expected in [('requirements.csv',5),('requirement_fields.csv',len(fields)),('criteria.csv',len(criteria)),('attachment_access.csv',5)]:
        with (OUT/filename).open(encoding='utf-8-sig',newline='') as f:assert len(list(csv.DictReader(f)))==expected
    names=['requirements.json','requirements.csv','requirement_fields.csv','criteria.csv','attachment_access.csv','READ_ME.txt']
    files=[OUT/n for n in names]+sorted((OUT/'sources').glob('*.json'))
    manifest=[{'file':p.relative_to(OUT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files]
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8');files.append(OUT/'manifest.json')
    archive=ROOT/'outputs/BuyICT_Five_Invitations_Public_Requirements_20261022.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in files:z.write(p,p.relative_to(OUT).as_posix())
    with zipfile.ZipFile(archive) as z:
        for r in manifest:assert hashlib.sha256(z.read(r['file'])).hexdigest()==r['sha256']
    print(json.dumps({'opportunities':5,'requirement_fields':len(fields),'criteria':len(criteria),'attachments_downloaded':0,'archive':str(archive)}),flush=True)


if __name__=='__main__':main()

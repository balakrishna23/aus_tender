"""Verify selected public BuyICT opportunities for the daily brief; no submissions."""
import argparse,hashlib,json,re,sqlite3,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote,urlsplit

sys.path.insert(0,r'/opt/aus_tender/local_assistant')
from buyict_sellers import Portal,BASE,find_widget
sys.path.insert(0,str(Path(__file__).resolve().parent))
from export_five_buyict_requirements import plain


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--ids',nargs='+',required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    portal=Portal();landing=portal.get(BASE+'/public')
    portal.portal=re.search(r"portal_id\s*=\s*'([^']+)'",landing.text)[1];portal.headers['x-portal']=portal.portal
    records=[]
    with sqlite3.connect(r'/opt/aus_tender/databases/buy_ict.db') as db:
        for key in args.ids:
            row=db.execute('SELECT url FROM buy_ict_tenders WHERE rfq_id=?',(key,)).fetchone()
            if not row:raise ValueError('Unknown public opportunity ID: '+key)
            url=row[0];uri=urlsplit(url).path+'?'+urlsplit(url).query
            response=portal.get(BASE+'/api/now/sp/page?'+urlsplit(url).query+'&portal_id='+portal.portal+'&request_uri='+quote(uri,safe=''))
            wrap=find_widget(response.json(),'opportunity_rfq_details')
            if not wrap:raise ValueError('Opportunity widget missing: '+key)
            outer=wrap['widget']['data'];data=(outer.get('opp_widget') or {}).get('data') or outer
            details={r.get('label'):r.get('value') for r in data.get('mainDetails',[]) if not r.get('priv_info')}
            if (details.get('RFQ ID') or details.get('RFI ID'))!=key:raise ValueError('Source ID mismatch: '+key)
            content=plain(data);serialized=json.dumps(content,ensure_ascii=False,indent=2)
            record={'id':key,'source_url':url,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
                'visible_data_sha256':hashlib.sha256(serialized.encode()).hexdigest(),
                'content':content,'secure_access':bool(data.get('can_access_secure')),
                'scope':'Public page only; account invitation status and secure attachments unverified'}
            records.append(record);(args.out/(key+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
            print(key,details.get('RFQ closing date') or details.get('RFI closing date'),flush=True)
    (args.out/'public_shortlist.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()

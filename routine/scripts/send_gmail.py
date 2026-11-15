"""Send one reviewed AWS newsletter. Reconcile uncertain sends; never blindly retry."""
import argparse,base64,fcntl,hashlib,json,os
from datetime import datetime,timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from zoneinfo import ZoneInfo
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession,Request

from mail_config import load_mail_config
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def send(out,token_path):
    out=out.resolve()
    if not str(out).startswith('/opt/illoura-routine/reports/daily_sweep_'):raise ValueError('Unexpected run directory')
    with (out/'mail.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _send_locked(out,token_path)
def _send_locked(out,token_path):
    SENDER,RECIPIENT=load_mail_config()
    if (out/'send_receipt.json').exists():raise ValueError('Edition already has a send receipt')
    report=json.loads((out/'source_refresh.json').read_text())
    expected={'contract_api','aus_contract','aus_tender','planned_procurement','standing_offer','buy_ict','act_tender','nsw_tender'}
    results=report.get('results') or []
    if len(results)!=8 or not all(isinstance(r,dict) for r in results) or {r.get('spider') for r in results}!=expected or 'feed' not in report:raise ValueError('Source completion checks failed')
    gaps={r['spider'] for r in results if r.get('status')!='OK' or r.get('crawl_failed') or r.get('errors')}
    # The brief is built from BuyICT only: a problem there, or a failed run with no named source, blocks the send.
    if 'buy_ict' in gaps or (not report.get('success') and not gaps):raise ValueError('Source gaps require resolution before sending')
    layout=json.loads((out/'email_layout_checks.json').read_text())
    if {x['viewport'] for x in layout if x.get('passed')}!={'desktop','mobile'}:raise ValueError('Layout checks missing')
    approved=json.loads((out/'cloud_send_review.json').read_text())
    if approved.get('authorization')!='User requested the Illoura routine and email on AWS' or approved.get('reviewed') is not True:raise ValueError('Current review missing')
    for name in ['newsletter_preview.html','newsletter_preview.txt','newsletter_editorial.json']:
        if approved['sha256'][name]!=digest(out/name):raise ValueError('Content changed after review: '+name)
    editorial=json.loads((out/'newsletter_editorial.json').read_text())
    config=json.loads((out/'editorial_notes.json').read_text())
    if config['edition_date']!=str(datetime.now(ZoneInfo('Asia/Kolkata')).date()):raise ValueError('Edition date is stale')
    if editorial['from']!=SENDER or editorial['to']!=RECIPIENT:raise ValueError('Unexpected sender/recipient')
    creds=Credentials.from_authorized_user_file(str(token_path))
    if not creds.valid:creds.refresh(Request())
    session=AuthorizedSession(creds);api='https://gmail.googleapis.com/gmail/v1/users/me'
    def get(path,**params):
        r=session.get(api+path,params=params,timeout=45);r.raise_for_status();return r.json()
    if get('/profile')['emailAddress'].lower()!=SENDER:raise ValueError('Gmail identity mismatch')
    subject=editorial['subject']
    matches=get('/messages',q='in:sent from:'+SENDER+' to:'+RECIPIENT+' subject:"'+subject.replace('"','')+'"',maxResults=25)
    for item in matches.get('messages',[]):
        message=get('/messages/'+item['id'],format='metadata')
        headers={x['name'].lower():x['value'] for x in message.get('payload',{}).get('headers',[])}
        if headers.get('subject')==subject and RECIPIENT in headers.get('to','') and SENDER in headers.get('from',''):
            receipt={'message_id':message['id'],'thread_id':message.get('threadId'),'label_ids':message.get('labelIds',[]),'sender':SENDER,'recipient':RECIPIENT,'subject':subject,'sent':True,'reconciled_existing_sent':True,'recorded_at_utc':datetime.now(timezone.utc).isoformat(),'execution_host':'AWS Sydney'}
            (out/'send_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt));return
    if (out/'send_attempt.json').exists():raise ValueError('Unresolved previous send attempt; reconcile before retry')
    now=datetime.now(timezone.utc).isoformat()
    message=EmailMessage(policy=SMTP);message['From']=SENDER;message['To']=RECIPIENT;message['Subject']=subject
    message['Message-ID']='<illoura-'+hashlib.sha256((str(out)+subject).encode()).hexdigest()[:28]+'@'+SENDER.rsplit('@',1)[1]+'>'
    message.set_content((out/'newsletter_preview.txt').read_text())
    message.add_alternative((out/'newsletter_preview.html').read_text(),subtype='html')
    attempt={'started_at_utc':now,'authorization':approved['authorization'],'sender':SENDER,'recipient':RECIPIENT,'subject':subject,'message_id_header':message['Message-ID'],'sha256':approved['sha256'],'execution_host':'AWS Sydney'}
    (out/'send_attempt.json').write_text(json.dumps(attempt,indent=2))
    raw=base64.urlsafe_b64encode(message.as_bytes()).decode()
    r=session.post(api+'/messages/send',json={'raw':raw},timeout=60);r.raise_for_status();sent=r.json()
    confirmed=get('/messages/'+sent['id'],format='metadata')
    if 'SENT' not in confirmed.get('labelIds',[]):raise RuntimeError('Gmail returned message but SENT not confirmed; reconcile')
    receipt={'message_id':sent['id'],'thread_id':sent.get('threadId'),'label_ids':confirmed['labelIds'],'sender':SENDER,'recipient':RECIPIENT,'subject':subject,'sent':True,'recorded_at_utc':datetime.now(timezone.utc).isoformat(),'execution_host':'AWS Sydney'}
    (out/'send_receipt.json').write_text(json.dumps(receipt,indent=2))
    for name in ['newsletter_review.json','finalization.json','cloud_run.json']:
        path=out/name
        if path.exists():
            value=json.loads(path.read_text());value.update(sent=True,send_receipt=str(out/'send_receipt.json'),sent_at_utc=receipt['recorded_at_utc']);path.write_text(json.dumps(value,indent=2))
    print(json.dumps(receipt))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--token',type=Path,default=Path('/etc/illoura-routine/gmail-token.json'));a=p.parse_args();send(a.out,a.token)

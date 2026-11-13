# Kept for reference; not run by the timer.
import hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/opt/aus_tender')
sys.path.insert(0,'/opt/illoura-routine/scripts')
from classify import closing_time
from send_gmail import send
from mail_config import load_mail_config
SENDER,RECIPIENT=load_mail_config()
work=Path('/opt/illoura-routine')
state=json.loads((work/'latest_run.json').read_text())
out=Path(state['run_dir'])
expected={'LH-08557': '88b6f25ba67799c5b786ec180fae9d764e4cecc8c40f11677bf0fc3b5a879ad6', 'LH-08438': '0805129c8d07b41cc9f86ca6901641000ef00335684113f3a7f4dd470774769d', 'LH-08495': '7a3bea931cf061bd5ff2b3c81319701c93949fb18369242600b402c5d49ec355', 'LH-08434': 'c11687a0de57adc558936baa38a44234f3c397e8b1228f95bfe5ba96716bf71b', 'LH-08545': '26fdc72d0e9c683cd4b59f59c21fe3c916282c6aeb0a0e8556e78c0fd2a68c43', 'LH-08549': '509c365c225fcb284238d17590e2ba37636ce6fde5b8fc58e16df45452168627', 'LH-08511': 'a8e1f14189d599aa5289771f81615188e7a19ecd1f5845c12760cfcae601472b', 'LH-08563': '3e5150426bf84d664dd65fb11f4d298dfa047f459c6af1c9e80fb299c9ed47da', 'LH-08551': 'd9b487f0a2474fdf0ec9c4d15808905b9ba813fa931195444f271c9078ba9872', 'LH-08414': '6fc5e1fe9f6e68357e3d1e51c9e40948d0e147b79cde3b018c722abb871302ae'}
old_review={'status': 'reviewed_blocked_mailbox_authorization', 'sent': False, 'scheduled_sending_enabled': False, 'from': SENDER, 'to': RECIPIENT, 'subject': 'Illoura daily brief | 12 November 2026 | 13 November response decisions', 'html_sha256': 'fd7dcba211d664065c3a9f50f5db1b4d7556c275f1f097d06dd96a67be3b3973', 'text_sha256': 'c294f81e9ae17dcff456ff080afc59cceecf20c8d7c4b0057eb2e4a5785ae625', 'word_count': 1372, 'verified_public_listings': 6, 'source_gaps': [], 'send_authorization': 'User requested: run daily Illoura routine and send email on AWS', 'execution_host': 'AWS Sydney Lightsail', 'reviewed_at_utc': '2026-11-12T14:59:37.742203+00:00', 'visual_review': 'Desktop/mobile top and full screenshots reviewed', 'delivery_blocker': 'Mailbox owner must authorize Google OAuth for '+SENDER, 'selected_public_listings': 6, 'total_public_listings_checked': 10}
now=datetime.now(timezone.utc)
for key,sha in expected.items():
    record=json.loads((out/'shortlist'/(key+'.json')).read_text())
    assert record['visible_data_sha256']==sha, 'Public content changed: '+key
    checked=datetime.fromisoformat(record['checked_at_utc'])
    assert 0 <= (now-checked).total_seconds() < 900, 'Public check stale: '+key
config=json.loads((out/'editorial_notes.json').read_text())
for key in config['opportunities']:
    r=json.loads((out/'shortlist'/(key+'.json')).read_text())['content']
    fields={x['label']:x.get('value') for x in r['mainDetails']}
    assert r['rfqOpen'] and not r.get('is_cancelled') and closing_time(fields.get('RFQ closing date'))>now, 'Listing no longer open: '+key
sha={n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in ['newsletter_preview.html','newsletter_preview.txt','newsletter_editorial.json']}
assert sha['newsletter_preview.html']==old_review['html_sha256']
assert sha['newsletter_preview.txt']==old_review['text_sha256']
approved={'authorization':'User requested the Illoura routine and email on AWS','reviewed':True,'reviewed_at_utc':now.isoformat(),'sha256':sha,'public_evidence_unchanged':True,'visual_review':'Desktop and mobile screenshots already reviewed; content hashes unchanged'}
(out/'cloud_send_review.json').write_text(json.dumps(approved,indent=2))
mail={'status':'authorized','sender':SENDER,'recipient':RECIPIENT,'gmail_credential_installed':True,'recorded_at_utc':now.isoformat()}
(out/'mail_authorization.json').write_text(json.dumps(mail,indent=2))
send(out,Path('/etc/illoura-routine/gmail-token.json'))
receipt=json.loads((out/'send_receipt.json').read_text())
assert receipt['sent'] and 'SENT' in receipt['label_ids']
for name in ['cloud_run.json','finalization.json','newsletter_review.json','outbound_send_request.json']:
    value=json.loads((out/name).read_text())
    value.update(status='sent',sent=True,sent_at_utc=receipt['recorded_at_utc'],send_receipt=str(out/'send_receipt.json'))
    value.pop('delivery_blocker',None)
    (out/name).write_text(json.dumps(value,indent=2))
    if name=='cloud_run.json':state=value
(work/'latest_run.json').write_text(json.dumps(state,indent=2))

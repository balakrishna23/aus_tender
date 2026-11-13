# Kept for reference; not run by the timer.
import sys
sys.path.insert(0,'/opt/illoura-routine/scripts')
from mail_config import load_mail_config
SENDER,RECIPIENT=load_mail_config()
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
work=Path('/opt/illoura-routine')
state=json.loads((work/'latest_run.json').read_text())
out=Path(state['run_dir'])
assert not (out/'send_receipt.json').exists()
report=json.loads((out/'source_refresh.json').read_text())
checks=json.loads((out/'email_layout_checks.json').read_text())
assert report['success'] and len(report['results'])==8 and 'feed' in report
assert len(checks)==2 and all(x['passed'] for x in checks)
assert not Path('/etc/illoura-routine/gmail-token.json').exists(), 'Credential state changed; inspect before continuing'
now=datetime.now(timezone.utc).isoformat()
review=json.loads((out/'newsletter_review.json').read_text())
for key,name in [('html_sha256','newsletter_preview.html'),('text_sha256','newsletter_preview.txt')]:
    assert review[key]==hashlib.sha256((out/name).read_bytes()).hexdigest()
review.update(status='reviewed_blocked_mailbox_authorization',sent=False,execution_host='AWS Sydney Lightsail',reviewed_at_utc=now,visual_review='Desktop/mobile top and full screenshots reviewed',send_authorization=state['authorization'],delivery_blocker='Mailbox owner must authorize Google OAuth for '+SENDER,selected_public_listings=6,total_public_listings_checked=10)
(out/'newsletter_review.json').write_text(json.dumps(review,indent=2))
request=json.loads((out/'outbound_send_request.json').read_text())
request.pop('link_id',None)
request.update(execution_host='AWS Sydney Lightsail',transport='AWS Gmail OAuth',status='blocked_mailbox_authorization',authorization=state['authorization'])
(out/'outbound_send_request.json').write_text(json.dumps(request,indent=2))
for name in ['finalization.json','cloud_run.json']:
    value=json.loads((out/name).read_text())
    value.update(status='reviewed_blocked_mailbox_authorization',sent=False,updated_at_utc=now)
    (out/name).write_text(json.dumps(value,indent=2))
    if name=='cloud_run.json':state=value
(work/'latest_run.json').write_text(json.dumps(state,indent=2))
print(json.dumps({'status':state['status'],'sent':False,'reviewed_at_utc':now,'cloud_run':str(out)}))

"""Send-safety tests. Google transport is mocked; no credentials or email used."""
import hashlib,json,tempfile,unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
import send_gmail as sender
class Response:
    def __init__(self,value):self.value=value
    def raise_for_status(self):pass
    def json(self):return self.value
class Session:
    def __init__(self,*a):self.posts=0
    def get(self,url,params=None,**kwargs):
        if url.endswith('/profile'):return Response({'emailAddress':sender.load_mail_config()[0]})
        if url.endswith('/messages'):return Response({})
        return Response({'id':'mock-id','labelIds':['SENT']})
    def post(self,*a,**k):self.posts+=1;return Response({'id':'mock-id','threadId':'mock-thread'})
class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='daily_sweep_security_',dir='/opt/illoura-routine/reports');self.out=Path(self.temp.name)
        def write(name,value):(self.out/name).write_text(json.dumps(value))
        names=['contract_api','aus_contract','aus_tender','planned_procurement','standing_offer','buy_ict','act_tender','nsw_tender']
        write('source_refresh.json',{'success':True,'results':[{'spider':n,'status':'OK'} for n in names],'feed':'rebuilt'})
        write('email_layout_checks.json',[{'viewport':x,'passed':True} for x in ['desktop','mobile']])
        write('editorial_notes.json',{'edition_date':str(datetime.now(ZoneInfo('Asia/Kolkata')).date())})
        write('newsletter_editorial.json',{'from':sender.load_mail_config()[0],'to':sender.load_mail_config()[1],'subject':'MOCK TEST — never sent'})
        (self.out/'newsletter_preview.html').write_text('<p>Mock test</p>');(self.out/'newsletter_preview.txt').write_text('Mock test')
        self.approve()
        self.session=Session();self.google=patch.object(sender,'AuthorizedSession',return_value=self.session);self.google.start()
        self.credentials=patch.object(sender.Credentials,'from_authorized_user_file',return_value=type('Fake',(),{'valid':True})());self.credentials.start()
    def approve(self):
        value={'authorization':'User requested the Illoura routine and email on AWS','reviewed':True,'sha256':{n:sender.digest(self.out/n) for n in ['newsletter_preview.html','newsletter_preview.txt','newsletter_editorial.json']}}
        (self.out/'cloud_send_review.json').write_text(json.dumps(value))
    def tearDown(self):self.google.stop();self.credentials.stop();self.temp.cleanup()
    def test_send_once(self):
        sender.send(self.out,Path('/not-a-real-token'))
        self.assertEqual(self.session.posts,1)
        with self.assertRaises(ValueError):sender.send(self.out,Path('/not-a-real-token'))
        self.assertEqual(self.session.posts,1)
    def test_changed_content_is_blocked(self):
        (self.out/'newsletter_preview.txt').write_text('changed')
        with self.assertRaises(ValueError):sender.send(self.out,Path('/not-a-real-token'))
        self.assertEqual(self.session.posts,0)
    def test_unresolved_send_is_blocked(self):
        (self.out/'send_attempt.json').write_text('{}')
        with self.assertRaises(ValueError):sender.send(self.out,Path('/not-a-real-token'))
        self.assertEqual(self.session.posts,0)
    def test_unapproved_recipient_is_blocked(self):
        data=json.loads((self.out/'newsletter_editorial.json').read_text());data['to']='other@example.org';(self.out/'newsletter_editorial.json').write_text(json.dumps(data));self.approve()
        with self.assertRaises(ValueError):sender.send(self.out,Path('/not-a-real-token'))
        self.assertEqual(self.session.posts,0)
    def test_source_gap_is_blocked(self):
        data=json.loads((self.out/'source_refresh.json').read_text());next(r for r in data['results'] if r['spider']=='buy_ict')['status']='FAILED';(self.out/'source_refresh.json').write_text(json.dumps(data))
        with self.assertRaises(ValueError):sender.send(self.out,Path('/not-a-real-token'))
        self.assertEqual(self.session.posts,0)
if __name__=='__main__':unittest.main()

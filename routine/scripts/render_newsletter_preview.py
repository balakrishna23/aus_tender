"""Render approved-for-review editorial JSON as HTML, plain text and an unsent EML.

This utility deliberately has no mail credentials, SMTP code or sending option.
"""
import argparse
import hashlib
import html
import json
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from urllib.parse import urlsplit


def escape(value):
    return html.escape(str(value), quote=True)


def render(package, out):
    def link(item):
        url=item['url']
        if urlsplit(url).scheme != 'https':
            raise ValueError('Evidence URLs must be HTTPS')
        return '<a href="'+escape(url)+'" style="color:#075985;text-decoration:underline">'+escape(item['label'])+'</a>'

    pieces=[]
    plain=[package['subject'], '', package['greeting'], '', package['introduction']]
    pieces.append('<p>'+escape(package['greeting'])+'</p><p>'+escape(package['introduction'])+'</p>')
    for section in package['sections']:
        pieces.append('<h2 style="font-size:19px;line-height:1.35;color:#183c38;margin:28px 0 12px">'+escape(section['title'])+'</h2>')
        plain += ['', section['title']]
        for item in section['items']:
            pieces.append('<div style="margin:0 0 18px"><p style="margin:0 0 6px"><strong>'+escape(item['heading'])+'</strong></p>')
            plain += ['',item['heading']]
            for paragraph in item.get('paragraphs',[]):
                pieces.append('<p style="margin:0 0 7px">'+escape(paragraph)+'</p>')
                plain.append(paragraph)
            if item.get('links'):
                pieces.append('<p style="margin:4px 0;font-size:13px">'+' &nbsp;|&nbsp; '.join(link(x) for x in item['links'])+'</p>')
                plain += [x['label']+': '+x['url'] for x in item['links']]
            pieces.append('</div>')
    plain += ['',package['footer']]
    body=''.join(pieces)
    content='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+escape(package['subject'])+'''</title></head>
<body style="margin:0;background:#f2f4f2;color:#26332f;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55">
<div style="display:none;max-height:0;overflow:hidden">'''+escape(package['preheader'])+'''</div>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f4f2"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="640" cellspacing="0" cellpadding="0" style="width:100%;max-width:640px;background:white;border:1px solid #dce4de">
<tr><td style="background:#183c38;color:white;padding:26px 28px"><div style="font-size:12px;letter-spacing:2px;color:#cfdecc">ILLOURA SERVICES</div><h1 style="font-size:27px;line-height:1.2;margin:8px 0 10px;color:white">Government IT opportunity brief</h1><div style="font-size:13px;color:#e4ece4">'''+escape(package['edition_label'])+'''</div></td></tr>
<tr><td style="padding:12px 28px 24px">'''+body+'''</td></tr>
<tr><td style="padding:20px 28px;background:#edf2ed;font-size:12px;color:#45594b">'''+escape(package['footer'])+'''</td></tr></table></td></tr></table></body></html>'''
    text='\n'.join(plain)+'\n'
    out.mkdir(parents=True,exist_ok=True)
    (out/'newsletter_preview.html').write_text(content,encoding='utf-8')
    (out/'newsletter_preview.txt').write_text(text,encoding='utf-8')
    message=EmailMessage(policy=SMTP)
    message['From']='Illoura Services <'+package['from']+'>'
    message['To']=package['to']
    message['Subject']=package['subject']
    message.set_content(text)
    message.add_alternative(content,subtype='html')
    (out/'newsletter_preview.eml').write_bytes(message.as_bytes())
    review={'status':'awaiting_user_review','sent':False,'scheduled_sending_enabled':False,
            'from':package['from'],'to':package['to'],'subject':package['subject'],
            'html_sha256':hashlib.sha256(content.encode()).hexdigest(),
            'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
            'word_count':len(text.split())}
    (out/'newsletter_review.json').write_text(json.dumps(review,indent=2),encoding='utf-8')
    print(json.dumps(review))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('editorial',type=Path)
    args=parser.parse_args()
    render(json.loads(args.editorial.read_text(encoding='utf-8')),args.editorial.parent)

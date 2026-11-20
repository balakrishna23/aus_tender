import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from market_intelligence.acquire_common import AcquisitionError, OfficialRedirects, PublisherClient
from market_intelligence.acquire_rap import (DIRECTORY, ORIGIN, TYPES, acquire, assemble,
    form_for, last_page, load_collection, parse_directory)


def html(kind, name='Regional Community Health Association', url='', period='April 2026 to April 2028'):
    return (f'<div class="who-has-a-rap"><h3 class="rap-organisation">{name}</h3>'
        f'<div class="rap-type"><span class="rap-value">{kind}</span></div>'
        f'<div class="rap-period"><span class="rap-value">{period}</span></div>'
        f'<div class="rap-view">' + (f'<a href="{url}">View</a>' if url else '') + '</div>'
        '<div class="rap-website"><a href="https://private.invalid/">Website excluded</a></div></div>').encode()


def collection(root):
    bodies = {kind: html(kind, name=prefix + ' Community Health Association')
              for kind, prefix in zip(TYPES, ('Regional', 'National', 'Rural', 'Urban'))}
    bodies.update(default=bodies['Reflect'] + b'<a href="' + (DIRECTORY+'page/21/').encode() + b'">Last</a>',
                  last=bodies['Elevate'], robots=b'User-agent: *\nAllow: /\n')
    files = []
    for key, body in bodies.items():
        name = key + '.html'
        (root/name).write_bytes(body)
        url = ORIGIN+'/robots.txt' if key == 'robots' else DIRECTORY+'page/21/' if key == 'last' else DIRECTORY
        files.append(dict(name=name, url=url, final_url=url, form=form_for(key) if key in TYPES else None,
            status=200, bytes=len(body), sha256=hashlib.sha256(body).hexdigest()))
    manifest = dict(observed_at='2026-11-19T00:00:00+00:00', files=files)
    (root/'manifest.json').write_text(json.dumps(manifest))
    return bodies, manifest


class RapAcquisitionTests(unittest.TestCase):
    def test_parser_exact_schema_and_no_website_or_pdf_hash(self):
        from market_intelligence.research import SCHEMAS
        row = parse_directory(html('Reflect'), '2026-11-19')[0]
        self.assertEqual(set(row), SCHEMAS['rap_directory'])
        self.assertIsNone(row['document_sha256'])
        self.assertEqual(row['document_url'], '')
        self.assertNotIn('private.invalid', json.dumps(row))

    def test_structure_changes_fail_closed(self):
        with self.assertRaises(AcquisitionError):
            parse_directory(b'<div class="who-has-a-rap"></div>', '2026-11-19')

    def test_stdlib_parser_preserves_nested_text_entities_and_ignores_website(self):
        body=html('Reflect',name='Invented &amp; <span>Imaginary</span> Association',
                  period='<span>April 2026</span> to <span>April 2028</span>')
        body=body.replace(b'<h3 ',b'<img src="opaque"/><h3 ')
        row=parse_directory(body,'2026-11-19')[0]
        self.assertEqual(row['organisation'],'Invented & Imaginary Association')
        self.assertEqual(row['period_as_published'],'April 2026 to April 2028')
        self.assertEqual(row['document_url'],'')

    def test_stdlib_parser_rejects_unclosed_misnested_duplicate_and_nested_cards(self):
        for body in [html('Reflect')[:-6],
                     html('Reflect').replace(b'</h3>',b'</p>'),
                     html('Reflect').replace(b'</h3>',b'</h3><span class="rap-organisation">Other</span>'),
                     b'<div class="who-has-a-rap">'+html('Reflect')+b'</div>']:
            with self.subTest(body=body[:40]),self.assertRaises(AcquisitionError):
                parse_directory(body,'2026-11-19')

    def test_local_manifest_rebuild_and_completeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); collection(root)
            bundle=load_collection(root)
            self.assertEqual(len(bundle['records']), 4)
            self.assertEqual(bundle['provenance']['type_counts'], dict.fromkeys(TYPES, 1))
            self.assertTrue(bundle['provenance']['complete'])

    def test_empty_wrong_type_and_missing_window_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            bodies,_=collection(Path(tmp))
            for key, replacement in [('Reflect', b''), ('Reflect', html('Innovate')),
                ('last',html('Elevate','Unseen Community Association')), ('default', b'')]:
                with self.subTest(key=key, replacement=replacement[:30]):
                    with self.assertRaises(AcquisitionError):
                        assemble(dict(bodies, **{key:replacement}), '2026-11-19T00:00:00+00:00')

    def test_checksum_incomplete_manifest_form_and_robots_tampering(self):
        for mode in ('checksum','missing','form','robots','last','status','redirect'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp); _,manifest=collection(root)
                if mode=='checksum': (root/'Reflect.html').write_bytes(b'changed')
                if mode=='missing': manifest['files'].pop()
                if mode=='form': manifest['files'][0]['form']['keyword']='filtered'
                if mode=='robots':
                    body=b'User-agent: *\nDisallow: /\n'; (root/'robots.html').write_bytes(body)
                    manifest['files'][-1].update(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
                if mode=='last': manifest['files'][-2]['url']=DIRECTORY+'page/20/'
                if mode=='status': manifest['files'][0]['status']=403
                if mode=='redirect': manifest['files'][0]['final_url']='https://other.invalid/'
                (root/'manifest.json').write_text(json.dumps(manifest))
                with self.assertRaises((AcquisitionError,ValueError)): load_collection(root)

    def test_pagination_only_official_directory_pages(self):
        self.assertEqual(last_page(('<a href="'+DIRECTORY+'page/21/">x</a><a href="https://evil.invalid/page/999/">x</a>').encode()),DIRECTORY+'page/21/')
        with self.assertRaises(AcquisitionError): last_page(b'<html>No pagination</html>')

    def test_post_encoding_and_unchanged_get(self):
        client=PublisherClient(origin=ORIGIN)
        response=Mock(status=200,headers={})
        response.geturl.return_value=DIRECTORY
        response.read1.side_effect=[b'ok',b'',b'ok',b'']
        client.opener=Mock()
        client.opener.open.return_value.__enter__=Mock(return_value=response)
        client.opener.open.return_value.__exit__=Mock(return_value=False)
        client.fetch(DIRECTORY,form=form_for('Reflect'))
        request=client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(),'POST')
        self.assertIn(b'rapType=Reflect',request.data)
        client.fetch(DIRECTORY)
        self.assertEqual(client.opener.open.call_args.args[0].get_method(),'GET')

    def test_redirect_checked_before_network_for_host_and_robots(self):
        redirects=OfficialRedirects(lambda url: False, ORIGIN)
        for target in ('https://evil.invalid/',DIRECTORY+'blocked/'):
            with self.assertRaises(AcquisitionError):
                redirects.redirect_request(Mock(),None,302,'',{},target)

    def test_acquire_loads_robots_first_and_paces_and_never_downloads_plans(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'fixture'; source.mkdir(); bodies,_=collection(source)
            calls=[]
            def fetch(client,url,limit=None,*,form=None):
                calls.append((url,form,client.request_delay))
                key='robots' if url.endswith('/robots.txt') else form['rapType'] if form else 'last' if '/page/' in url else 'default'
                return bodies[key],url
            with patch.object(PublisherClient,'fetch',fetch):
                result=acquire(Path(tmp)/'new')
            self.assertEqual(result['rows'],4)
            self.assertEqual(calls[0][0],ORIGIN+'/robots.txt')
            self.assertEqual(len(calls),7)
            self.assertTrue(all(c[2]>=3 for c in calls[1:]))


if __name__=='__main__': unittest.main()

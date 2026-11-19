"""Export already-collected RAP metadata, reviewed findings or state contracts, with provenance.

No new scraping, contacts, addresses, responsible people's names, descriptions or raw JSON leave the sources.
"""
import argparse
from contextlib import closing
import csv
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

EMAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+')


def clean(value):return EMAIL.sub('[contact omitted]',value) if isinstance(value,str) else value


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save_bundle(source,records,provenance,destination):
    if not records:raise ValueError('Empty source')
    data=dict(source=source,observed_at=datetime.now(timezone.utc).isoformat(),contains_contact_data=False,
              provenance=provenance,records=records)
    path=Path(destination);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    return dict(source=source,rows=len(records),sha256=digest(path))


def rap_directory(directory,destination):
    directory=Path(directory).resolve(strict=True)
    with (directory/'directory.csv').open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
    with (directory/'downloads.csv').open(encoding='utf-8-sig',newline='') as stream:
        documents={r['url']:r for r in csv.DictReader(stream) if r['status']=='downloaded'}
    summary=json.loads((directory/'summary.json').read_text(encoding='utf-8'))
    records=[];checked=set()
    for row in rows:
        doc=documents.get(row['rap_url']);sha=None
        if doc:
            # Old Windows manifests use backslashes; never allow a path to leave the retained library.
            path=(directory/Path(doc['file'].replace('\\','/'))).resolve(strict=True)
            if not path.is_relative_to(directory):raise ValueError('Document path outside library')
            if doc['file'] not in checked:
                if path.stat().st_size!=int(doc['bytes']) or digest(path)!=doc['sha256']:raise ValueError('RAP document checksum mismatch')
                checked.add(doc['file'])
            sha=doc['sha256']
        identity='|'.join(row[k] for k in ['organisation','rap_type','period_as_published','rap_url'])
        records.append(dict(record_key=hashlib.sha256(identity.encode()).hexdigest(),organisation=clean(row['organisation']),
            rap_type=row['rap_type'],period_as_published=row['period_as_published'],document_url=clean(row['rap_url']),
            source_url=row['source_page'],document_sha256=sha,collection_date=summary['collection_date'],
            licence='Original copyright retained; internal research only; commercial reuse not established'))
    # Repeated directory observations are one source claim, not distinct organisations.
    records=list({r['record_key']:r for r in records}.values())
    return save_bundle('rap_directory',records,dict(source_url='https://www.reconciliation.org.au/reconciliation-action-plans/who-has-a-rap/',
        licence='Original copyright retained; internal research only',collection_date=summary['collection_date'],
        directory_sha256=digest(directory/'directory.csv'),downloads_sha256=digest(directory/'downloads.csv'),
        verified_documents=len(checked),commercial_reuse='unclear'),destination)


def rap_commitments(database,destination):
    records=[]
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('BEGIN');conn.row_factory=sqlite3.Row
        companies={r['organisation']:json.loads(r['payload_json']) for r in conn.execute('SELECT * FROM company_reviews ORDER BY reviewed_on')}
        for row in conn.execute('SELECT evidence_key,organisation,reviewed_on,action,pdf_page,summary,timeline,source_url FROM commitments'):
            company=companies[row['organisation']];doc=company['document']
            if doc.get('path') and Path(doc['path']).is_file() and digest(doc['path'])!=doc['sha256']:
                raise ValueError('Reviewed RAP document checksum changed')
            if not 1<=row['pdf_page']<=doc['page_count']:raise ValueError('Invalid RAP page citation')
            records.append(dict(record_key=row['evidence_key'],**{k:clean(row[k]) for k in row.keys() if k!='evidence_key'},
                period_start=company['period_start'],period_end=company['period_end'],candidate_abns=company['candidate_abns'],
                document_sha256=doc['sha256'],review_method=company['review_method'],
                entity_scope='candidate associations only; legal entity scope not confirmed',
                target_completion='unknown; published commitment does not establish a shortfall'))
    return save_bundle('rap_commitments',records,dict(source='Existing page-cited reviewed evidence',licence='Original copyright retained; internal research only',
        source_access='read_only_transaction',commercial_reuse='unclear'),destination)


def state_contracts(database,destination):
    try:from .categories import tag
    except ImportError:
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        from market_intelligence.categories import tag
    records=[]
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('BEGIN')
        sources={r['source_file']:dict(r) for r in conn.execute('SELECT source_file,state,url,licence,modified,rows FROM sources')}
        for row in conn.execute('SELECT * FROM contracts'):
            source=sources[row['source_file']]
            category,rule=tag(row['unspsc'],row['category_family'],row['description'])
            keep=['state','source_file','row_no','agency','title','reference','procurement_method','unspsc','award_date','start_date',
                  'end_date','value','supplier','supplier_abn','panel','loaded_at']
            data={k:clean(row[k]) for k in keep}
            data.update(source_url=source['url'],licence=source['licence'],source_modified=source['modified'],
                        category_tag=category,category_rule=rule,record_key=hashlib.sha256('|'.join(str(row[k]) for k in ['state','source_file','row_no']).encode()).hexdigest())
            records.append(data)
    return save_bundle('state_contracts',records,dict(source_access='read_only_transaction',sources=list(sources.values()),
        commercial_reuse='Source-specific licence retained; verify per source'),destination)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',choices=['rap_directory','rap_commitments','state_contracts'],required=True)
    p.add_argument('--input',required=True);p.add_argument('--destination',required=True);a=p.parse_args()
    print(json.dumps(globals()[a.source](a.input,a.destination)))

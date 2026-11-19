"""Immutable, whitelisted export of Tender Feed opportunities. Never exports contacts."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

FIELDS=['source','source_id','kind','title','buyer','category','unspsc','published','closes_at','expected_from',
        'expected_to','status_effective','location','url','retrieved_at']
EMAIL=re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]+')


def export_forward(source,destination):
    source=Path(source).resolve(strict=True);destination=Path(destination).resolve()
    if destination.exists() or source==destination:raise ValueError('Choose a new immutable export path')
    destination.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(dir=destination.parent,suffix='.db');os.close(descriptor)
    metadata=dict(exported_at=datetime.now(timezone.utc).isoformat(),source='Tender Feed',
                  licence='Underlying publisher terms; commercial reuse not asserted',source_access='read_only_transaction',
                  contains_contact_data=False,redacted_contact_cells=0)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as origin,closing(sqlite3.connect(temporary)) as out,out:
        origin.execute('PRAGMA query_only=ON');origin.execute('BEGIN')
        present={r[1] for r in origin.execute('PRAGMA table_info(opportunities)')}
        if set(FIELDS)-present:raise ValueError('Tender Feed schema changed')
        out.execute('CREATE TABLE opportunities('+','.join(f+' TEXT' for f in FIELDS)+')')
        count=0
        for row in origin.execute('SELECT '+','.join(FIELDS)+' FROM opportunities ORDER BY source,source_id'):
            safe=[]
            for value in row:
                if isinstance(value,str) and EMAIL.search(value):
                    value=EMAIL.sub('[contact omitted]',value);metadata['redacted_contact_cells']+=1
                safe.append(value)
            out.execute('INSERT INTO opportunities VALUES('+','.join('?' for _ in FIELDS)+')',safe);count+=1
        if not count:raise ValueError('Tender Feed returned zero rows')
        metadata['rows']=count
        metadata['latest_retrieved_at']=origin.execute('SELECT max(retrieved_at) FROM opportunities').fetchone()[0]
        out.execute('CREATE TABLE metadata(payload TEXT NOT NULL)')
        out.execute('INSERT INTO metadata VALUES(?)',(json.dumps(metadata),))
    os.chmod(temporary,0o600);os.link(temporary,destination);Path(temporary).unlink()
    return metadata


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--destination',required=True)
    args=p.parse_args();print(json.dumps(export_forward(args.source,args.destination)))

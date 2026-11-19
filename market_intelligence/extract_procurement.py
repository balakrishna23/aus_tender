"""Export whitelisted procurement facts without modifying collector databases.

Contact names, emails, phone numbers and free-text descriptions are excluded.
Each source is read in a transaction. Different collector databases can represent
different collection times; their individual retrieval fields remain in the export.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

CONTRACT_FIELDS = ['cn_id','buyer','buyer_abn','supplier','supplier_abn','category_family','unspsc',
                   'original_value','current_value','start_date','current_end_date','first_published',
                   'amendment_count','extension_count','source_url','page_url','retrieved_at']
PANEL_FIELDS = ['son_id','title','agency','primary_category','standing_offer_period','url','scraped_at']


def extract(directory, destination):
    directory=Path(directory).resolve(strict=True)
    destination=Path(destination).resolve()
    if destination.parent==directory:
        raise ValueError('Write the export outside collector database storage')
    if destination.exists():
        raise ValueError('Export destination already exists; choose a new dated snapshot path')
    destination.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix='procurement-snapshot-',suffix='.db',dir=destination.parent)
    os.close(descriptor)
    metadata={'exported_at':datetime.now(timezone.utc).isoformat(),'source_access':'read_only_transactions',
              'contains_contact_data':False,'cross_source_simultaneity':'not_asserted','sources':{}}
    with closing(sqlite3.connect(temporary)) as out, out:
        for filename,table,fields in [('contract_api','contracts',CONTRACT_FIELDS),
                                      ('aus_contract','contract_panel_links',['cn_id','son_id','url','scraped_at']),
                                      ('standing_offer','panels',PANEL_FIELDS)]:
            source=directory/(filename+'.db')
            types={name:('REAL' if name in ['original_value','current_value'] else 'INTEGER' if name in ['amendment_count','extension_count'] else 'TEXT') for name in fields}
            out.execute('CREATE TABLE '+table+'('+','.join(name+' '+types[name] for name in fields)+')')
            if not source.exists():
                if filename=='contract_api':raise ValueError('Contract API source is required')
                metadata['sources'][filename]={'status':'missing','rows':0}
                continue
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as origin:
                origin.execute('PRAGMA query_only=ON');origin.execute('BEGIN')
                source_table='standing_offers' if filename=='standing_offer' else 'contracts'
                sql='SELECT '+','.join(fields)+' FROM '+source_table
                if filename=='aus_contract':sql+=" WHERE son_id IS NOT NULL AND trim(son_id)<>''"
                cursor=origin.execute(sql)
                count=0
                while rows:=cursor.fetchmany(4000):
                    out.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in fields)+')',rows)
                    count+=len(rows)
                metadata['sources'][filename]={'status':'complete','rows':count}
                retrieved_column='retrieved_at' if filename=='contract_api' else 'scraped_at'
                metadata['sources'][filename]['latest_record_retrieved_at']=origin.execute('SELECT max('+retrieved_column+') FROM '+source_table).fetchone()[0]
                metadata['sources'][filename]['licence_or_terms']='Creative Commons Attribution 3.0 Australia' if filename=='contract_api' else 'AusTender website terms; bulk commercial redistribution not asserted'
                if filename=='contract_api' and origin.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crawl_state'").fetchone():
                    checkpoint=origin.execute("SELECT value FROM crawl_state WHERE key='modified_through'").fetchone()
                    metadata['sources'][filename]['modified_through']=checkpoint[0] if checkpoint else None
                if filename=='contract_api' and not count:raise ValueError('Empty contract source')
        out.execute('CREATE UNIQUE INDEX contract_identity ON contracts(cn_id)')
        out.execute('CREATE TABLE export_metadata(payload text NOT NULL)')
        out.execute('INSERT INTO export_metadata VALUES(?)',(json.dumps(metadata),))
    os.chmod(temporary,0o600)
    os.link(temporary,destination)
    Path(temporary).unlink()
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    print(json.dumps(extract(args.directory,args.destination)))

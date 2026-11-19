"""Read-only procurement identity export; usable with the standard library alone.

Run by an authorised operator with read access to the source database. Output is
an atomic, private SQLite subset; it contains no source contact details.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def extract(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError('Export must not replace the source database')
    if destination.exists():
        raise ValueError('Export destination already exists; choose a new dated snapshot path')
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='buyer-snapshot-',suffix='.db',dir=destination.parent)
    os.close(descriptor)
    # Each execution has a consistent read transaction, including the audit counts.
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as origin:
        origin.execute('PRAGMA query_only=ON')
        origin.execute('BEGIN')
        rows=origin.execute("SELECT DISTINCT buyer,buyer_abn FROM contracts WHERE buyer IS NOT NULL AND trim(buyer)<>''").fetchall()
        source_count=origin.execute('SELECT count(*) FROM contracts').fetchone()[0]
        with closing(sqlite3.connect(temporary)) as output, output:
            output.execute('CREATE TABLE contracts(buyer text,buyer_abn text)')
            output.executemany('INSERT INTO contracts VALUES(?,?)',rows)
            output.execute('CREATE TABLE export_metadata(payload text NOT NULL)')
            metadata={'retrieved_at':datetime.now(timezone.utc).isoformat(),'source_contract_rows':source_count,
                      'buyer_pairs':len(rows),'source_access':'read_only_transaction','contains_contact_data':False}
            output.execute('INSERT INTO export_metadata VALUES(?)',(json.dumps(metadata),))
    os.chmod(temporary,0o600)
    os.link(temporary,destination)
    Path(temporary).unlink()
    return metadata


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    print(json.dumps(extract(args.source,args.destination)))

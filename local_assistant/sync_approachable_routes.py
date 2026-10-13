"""Append reviewed, approachable supplier routes without replacing existing contacts."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, sqlite3
import assistant, company_coverage

SOURCE=Path(__file__).parent/'inputs'/'approachable_company_routes.json'
SCHEMA='''CREATE TABLE IF NOT EXISTS approachable_routes(
 route_id TEXT PRIMARY KEY,supplier_key TEXT NOT NULL,company TEXT,person TEXT,role TEXT,
 preferred_kind TEXT,preferred_value TEXT,secondary_kind TEXT,secondary_value TEXT,
 source_url TEXT,checked_at_utc TEXT,proof TEXT,limit_text TEXT,added_at_utc TEXT);'''

def sync():
    records=json.loads(SOURCE.read_text(encoding='utf-8'))['records']
    added=0
    with sqlite3.connect(assistant.DB_DIR/'company_contacts.db') as connection:
        connection.executescript(SCHEMA)
        for record in records:
            for supplier_key in record['supplier_keys']:
                identity='|'.join([supplier_key,record['preferred_kind'],record['preferred_value'],record['source_url']])
                route_id=hashlib.sha256(identity.encode()).hexdigest()
                values=(route_id,supplier_key,record['company'],record.get('person',''),record['role'],
                    record['preferred_kind'],record['preferred_value'],record.get('secondary_kind',''),
                    record.get('secondary_value',''),record['source_url'],record['checked_at_utc'],
                    record['proof'],record['limit'],datetime.now(timezone.utc).isoformat())
                before=connection.total_changes
                connection.execute('INSERT OR IGNORE INTO approachable_routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
                added+=connection.total_changes-before
        connection.executescript(company_coverage.SCHEMA)
        company_coverage.contact_counts(connection)
        connection.commit()
        total=connection.execute('SELECT COUNT(*) FROM approachable_routes').fetchone()[0]
    print(json.dumps({'added':added,'total':total,'mode':'append_only'}))

if __name__=='__main__':sync()

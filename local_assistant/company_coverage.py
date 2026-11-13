"""All-history supplier coverage. Never treat an agency contact as a supplier contact.

Published supplier locations come from contract-version address fields. Business
email/phone routes must come from a separately reviewed source register.
"""
import hashlib,json,re,sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import assistant

CONTACTS=Path(__file__).parent/'inputs'/'verified_company_contacts.json'
SCHEMA='''CREATE TABLE IF NOT EXISTS suppliers(
 supplier_key TEXT PRIMARY KEY,name TEXT,abn TEXT,identity_basis TEXT,aliases_json TEXT,
 notice_count INTEGER,latest_publication TEXT,location_json TEXT,address_completeness TEXT,
 location_source_url TEXT,location_source_release TEXT,location_retrieved_at TEXT,
 address_fields_sha256 TEXT,street_display_policy TEXT,contact_status TEXT,inventory_source_url TEXT);
CREATE TABLE IF NOT EXISTS supplier_contacts(
 supplier_key TEXT,kind TEXT,value TEXT,person TEXT,role TEXT,source_url TEXT,
 identity_source_url TEXT,checked_at_utc TEXT,verification_method TEXT,proof_locator TEXT,
 identity_scope TEXT,limit_text TEXT);
CREATE TABLE IF NOT EXISTS coverage_info(name TEXT PRIMARY KEY,value TEXT);
'''

def identity(name,abn):
    normalized=re.sub(r'[\s-]','',str(abn or ''))
    if re.fullmatch(r'\d{11}',normalized):return 'ABN:'+normalized,normalized,'published ABN; legal status not independently checked'
    return 'NAME:'+str(name or '').strip().casefold(),str(abn or ''),'exact name only; missing/nonstandard ABN; identity unconfirmed'

def location_status(address):
    if address.get('street') and (address.get('locality') or address.get('postcode')):return 'published street/location'
    if any(address.get(k) for k in ['locality','region','postcode']):return 'partial location only'
    return 'no usable published location'

def source_value(value):
    text=str(value or '').strip()
    return '' if text.casefold() in {'-','none','null','n/a','not provided','not disclosed','unknown','not applicable','not specified'} else text

def contact_counts(connection):
    """Set each supplier's status from both reviewed registers (contacts and approachable routes)."""
    has_routes=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approachable_routes'").fetchone()
    routes=' UNION SELECT supplier_key FROM approachable_routes' if has_routes else ''
    direct=(" UNION SELECT supplier_key FROM approachable_routes WHERE preferred_kind IN ('email','phone')"
            " OR secondary_kind IN ('email','phone')") if has_routes else ''
    connection.execute(f"""UPDATE suppliers SET contact_status=CASE WHEN supplier_key IN
        (SELECT supplier_key FROM supplier_contacts{routes})
        THEN 'reviewed public business route available' ELSE 'email/phone not established; web review pending' END""")
    known='SELECT COUNT(*) FROM ({}) WHERE supplier_key IN (SELECT supplier_key FROM suppliers)'
    total=connection.execute('SELECT COUNT(*) FROM suppliers').fetchone()[0]
    reviewed=connection.execute(known.format('SELECT supplier_key FROM supplier_contacts'+routes)).fetchone()[0]
    email_phone=connection.execute(known.format(
        "SELECT supplier_key FROM supplier_contacts WHERE kind LIKE '%email%' OR kind LIKE '%phone%'"+direct)).fetchone()[0]
    counts={'suppliers_with_reviewed_business_routes':reviewed,'suppliers_with_reviewed_email_phone':email_phone,
            'suppliers_without_reviewed_email_phone':total-email_phone}
    connection.executemany('INSERT OR REPLACE INTO coverage_info VALUES (?,?)',[(k,str(v)) for k,v in counts.items()])
    return counts

def build(report_dir=None):
    collected={};source=assistant.DB_DIR/'contract_api.db'
    def add(name,abn,cn,stamp,record=None):
        if not name or not str(name).strip():return
        key,clean,basis=identity(name,abn)
        entry=collected.setdefault(key,{'supplier_key':key,'name':name,'abn':clean,'identity_basis':basis,'aliases':set(),
            'notices':set(),'latest_publication':'','record':None,'proof_record':None,'panel_source_url':''})
        entry['aliases'].add(name)
        if cn:entry['notices'].add(cn)
        if str(stamp or '')>entry['latest_publication']:entry['latest_publication']=str(stamp or '');entry['name']=name
        if record:
            if entry['proof_record'] is None or str(record.get('release_date') or '')>str(entry['proof_record'].get('release_date') or ''):
                entry['proof_record']=record
            address={k:source_value(record.get('supplier_'+k)) for k in ['street','locality','region','postcode','country']}
            # Prefer the latest record that actually carries useful location fields.
            if location_status(address)!='no usable published location':
                prior=entry['record']
                if prior is None or str(record.get('release_date') or '')>str(prior.get('release_date') or ''):
                    entry['record']=record
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=15)) as connection:
        connection.row_factory=sqlite3.Row
        columns='supplier,supplier_abn,cn_id,release_id,release_date,supplier_street,supplier_locality,supplier_region,supplier_postcode,supplier_country,source_url,retrieved_at'
        versions=0
        for row in connection.execute('SELECT '+columns+' FROM contract_versions'):
            record=dict(row);add(record['supplier'],record['supplier_abn'],record['cn_id'],record['release_date'],record);versions+=1
        contracts=0
        for row in connection.execute('SELECT supplier,supplier_abn,cn_id,first_published FROM contracts'):
            add(row['supplier'],row['supplier_abn'],row['cn_id'],row['first_published']);contracts+=1
    # Include panel suppliers even when they have no stored contract award.
    panels=assistant.DB_DIR/'standing_offer.db'
    if panels.is_file():
        with closing(sqlite3.connect(panels.resolve().as_uri()+'?mode=ro',uri=True)) as connection:
            connection.row_factory=sqlite3.Row
            for row in connection.execute('SELECT current_suppliers,previous_suppliers,url FROM standing_offers'):
                for field in ['current_suppliers','previous_suppliers']:
                    try:members=json.loads(row[field] or '[]')
                    except (ValueError,TypeError):continue
                    for member in members:
                        if isinstance(member,dict):
                            name=member.get('Supplier Name') or member.get('name') or member.get('supplier')
                            abn=member.get('ABN') or member.get('abn')
                            add(name,abn,None,None)
                            if name:collected[identity(name,abn)[0]]['panel_source_url']=row['url']
    path=assistant.DB_DIR/'company_contacts.db'
    register=json.loads(CONTACTS.read_text(encoding='utf-8')) if CONTACTS.is_file() else {'records':[]}
    records=register.get('records',[])
    with closing(sqlite3.connect(path)) as destination:
        destination.executescript(SCHEMA)
        if 'inventory_source_url' not in [row[1] for row in destination.execute('PRAGMA table_info(suppliers)')]:
            destination.execute('ALTER TABLE suppliers ADD COLUMN inventory_source_url TEXT')
        # Derived supplier coverage is rebuilt; source procurement records remain untouched.
        destination.execute('DELETE FROM supplier_contacts');destination.execute('DELETE FROM suppliers')
        for key,entry in collected.items():
            record=entry['record'] or {}
            address={k:source_value(record.get('supplier_'+k)) for k in ['street','locality','region','postcode','country']}
            corporate=any(re.search(r'\b(pty|ltd|limited|inc|llc|gmbh|corporation|corp)\b',name,re.I) for name in entry['aliases'])
            policy='government-published supplier location; not independently confirmed current' if corporate else 'unverified legal form: street withheld from user-facing register'
            if not corporate:address['street']=''
            completeness=location_status(address)
            matching=[r for r in records if r.get('supplier_abn')==entry['abn'] and r.get('review_status')=='reviewed']
            status='reviewed public business route available' if matching else 'email/phone not established; web review pending'
            proof=entry['proof_record'] or {}
            destination.execute('INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                key,entry['name'],entry['abn'],entry['identity_basis'],json.dumps(sorted(entry['aliases'])),len(entry['notices']),
                entry['latest_publication'],json.dumps(address,ensure_ascii=False),completeness,
                record.get('source_url') or '',record.get('release_id') or '',record.get('retrieved_at') or '',
                hashlib.sha256(json.dumps(address,sort_keys=True).encode()).hexdigest(),policy,status,
                proof.get('source_url') or entry['panel_source_url']))
            for contact in matching:
                for kind,value in contact.get('routes',{}).items():
                    if not value:continue
                    destination.execute('INSERT INTO supplier_contacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(
                        key,kind,value,contact.get('person',''),contact.get('role',''),contact['source_url'],
                        contact.get('identity_source_url',''),contact['checked_at_utc'],contact['verification_method'],
                        contact['proof_locator'],contact['identity_scope'],contact['limit']))
        generated=datetime.now(timezone.utc).isoformat()
        address_count=destination.execute("SELECT COUNT(*) FROM suppliers WHERE address_completeness!='no usable published location'").fetchone()[0]
        summary={'generated_at_utc':generated,'contract_records':contracts,'version_records':versions,
            'distinct_source_names':sum(len(e['aliases']) for e in collected.values()),'supplier_identities':len(collected),
            'suppliers_with_published_location':address_count,**contact_counts(destination),
            'scope':'Every supplier identity represented in stored contract versions/rollups and parsed panel members; includes non-company suppliers. Published ABNs group aliases; name-only identities remain unconfirmed.',
            'limit':'Full inventory is not full web verification. Agency contactPoint/email/phone fields are deliberately excluded. A location is not a verified sales contact, current business address or warm introduction.'}
        destination.executemany('INSERT OR REPLACE INTO coverage_info VALUES (?,?)',[(k,str(v)) for k,v in summary.items()])
        destination.commit()
        if destination.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Contact register integrity failed')
    output=Path(report_dir) if report_dir is not None else Path(__file__).parent/'reports'
    output.mkdir(parents=True,exist_ok=True)
    (output/'company-coverage.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary),flush=True)
    return summary

if __name__=='__main__':build()

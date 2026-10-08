"""Bounded checks of official AusTender contract facts, never hiring claims."""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

API='https://api.tenders.gov.au/ocds/findById/'


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parts=urlsplit(newurl)
        if parts.scheme!='https' or parts.netloc!='api.tenders.gov.au':
            raise ValueError('Unexpected redirect outside the official contract API')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch_contract(cn):
    if not re.fullmatch(r'CN\d+',cn):
        raise ValueError('Unsupported contract ID')
    request=Request(API+cn,headers={'Accept':'application/json','User-Agent':'Illoura-evidence-pilot/2.1'})
    with build_opener(OfficialRedirects()).open(request,timeout=12) as response:
        raw=response.read(2_000_001)
        if len(raw)>2_000_000:
            raise ValueError('Contract response exceeded the verification limit')
        value=json.loads(raw)
    if not isinstance(value,dict) or not isinstance(value.get('releases'),list):
        raise ValueError('The official source did not return an OCDS release package')
    return value,hashlib.sha256(raw).hexdigest()


def local_day(value):
    if not value:
        return None
    moment=datetime.fromisoformat(value.replace('Z','+00:00'))
    if moment.tzinfo is None:
        moment=moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo('Australia/Canberra')).date().isoformat()


def latest_fields(package,cn):
    matches=[]
    for release in package['releases']:
        for contract in release.get('contracts') or []:
            if contract.get('id')==cn:
                amendment=(contract.get('amendments') or [{}])[-1].get('id') or ''
                number=re.search(r'-A(\d+)$',amendment)
                matches.append((int(number.group(1)) if number else 0,release.get('date') or '',release,contract))
    if not matches:
        raise ValueError('Requested contract ID was absent from the official response')
    matches.sort(key=lambda r:(r[0],r[1]))
    _,_,release,contract=matches[-1]
    original_contract=matches[0][3]
    parties=release.get('parties') or []
    buyer=next((p for p in parties if 'procuringEntity' in (p.get('roles') or [])),{})
    supplier=next((p for p in parties if 'supplier' in (p.get('roles') or [])),{})
    amount=(contract.get('value') or {}).get('amount')
    return {'cn_id':cn,'buyer':buyer.get('name'),'supplier':supplier.get('name'),
            'supplier_abn':next((str(i['id']) for i in supplier.get('additionalIdentifiers') or []
                                 if i.get('scheme')=='AU-ABN'),None),
            'description':original_contract.get('description') or contract.get('description'),
            'latest_release_description':contract.get('description'),
            'description_basis':'Original notice description, as in the stored roll-up; latest release description is also shown.',
            'start_date':local_day((original_contract.get('period') or {}).get('startDate')),
            'current_value':float(amount) if amount is not None else None,
            'currency':(contract.get('value') or {}).get('currency'),
            'current_end_date':local_day((contract.get('period') or {}).get('endDate')),
            'release_date':release.get('date')}


def verify_record(record,fetch=fetch_contract):
    cn=re.sub(r'-A\d+$','',record['cn_id'])
    result={'cn_id':cn,'source_url':API+cn,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
            'proof_scope':'Contract fields only. This does not verify hiring, renewal intent, supplier openness or a warm relationship.'}
    try:
        package,digest=fetch(cn)
        fields=latest_fields(package,cn)
        mismatches,compared=[],[]
        for key in ['buyer','supplier','supplier_abn','description','current_value','current_end_date']:
            if key not in record:
                continue
            old,new=record[key],fields[key]
            # Missing fields cannot be verified; do not silently treat absent values as matches.
            if old is None or new is None:
                mismatches.append({'field':key,'stored':old,'official':new,'reason':'missing value'})
            elif key=='current_value':
                if not isinstance(old,(int,float)) or abs(old-new)>0.01:
                    mismatches.append({'field':key,'stored':old,'official':new})
            elif str(old).strip()!=str(new).strip():
                mismatches.append({'field':key,'stored':old,'official':new})
            compared.append(key)
        if fields['currency']!='AUD':
            mismatches.append({'field':'currency','stored':'AUD','official':fields['currency']})
        result.update(status='mismatch' if mismatches else 'matched' if compared else 'no stored fields to compare',
                      compared_fields=compared,mismatches=mismatches,official_fields=fields,response_sha256=digest)
        if fields['start_date'] and fields['current_end_date'] and fields['current_end_date']<fields['start_date']:
            result['source_quality_issue']='Official recorded end date precedes original start date. Do not use these dates for term planning; source clarification is needed.'
    except Exception as exc:
        result.update(status='unavailable',error=str(exc))
    return result


def verify_evidence(facts,limit=3):
    records={}
    for row in facts:
        if row.get('cn_id') and row.get('view') in ('named_contract','renewals','company_contract_examples'):
            records.setdefault(row['cn_id'],row)
    selected=list(records.values())[:limit]
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(verify_record,selected))

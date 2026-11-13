"""Read separately maintained public-source research with age and scope labels."""
import json
from datetime import datetime,timezone
from pathlib import Path

PATH=Path(__file__).parent/'inputs'/'public_research.json'
CHECKS=Path(__file__).parent/'reports'/'public-source-checks.json'


def findings(entities,roles=(),path=PATH):
    if not path.is_file():return []
    if path.stat().st_size>1_000_000:raise ValueError('Public research input too large')
    package=json.loads(path.read_text(encoding='utf-8'))
    checks={r['source_url']:r for r in json.loads(CHECKS.read_text(encoding='utf-8'))} if CHECKS.is_file() else {}
    matches=[]
    for record in package['records']:
        org=record['organisation'].casefold()
        if entities and not any(org in entity.casefold() or entity.casefold() in org for entity in entities):continue
        if record['kind']=='job advert' and roles and record.get('role_family') not in roles:continue
        reviewed=record.get('reviewed_at_utc',package['reviewed_at_utc'])
        checked=datetime.fromisoformat(reviewed)
        age=max(0,(datetime.now(timezone.utc)-checked).days)
        row={**record,'reviewed_at_utc':reviewed,
             'review_method':record.get('review_method',package['review_method']),'review_age_days':age,
             'review_status':'needs recheck (older than 30 days)' if age>30 else 'recent manual source review; not checked live by this answer'}
        if record.get('source_url') in checks:
            check=checks[record['source_url']]
            row['last_direct_source_check']={k:check[k] for k in ['checked_at_utc','status','scope','response_sha256','error','published_job_markers_present'] if k in check}
        if record.get('period_end_year'):
            row['period_status']='historical by stated end year' if record['period_end_year']<datetime.now(timezone.utc).year else \
                'stated plan years include current year; implementation not verified'
        matches.append(row)
    return matches

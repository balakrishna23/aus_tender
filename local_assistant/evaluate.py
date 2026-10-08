"""Plan fidelity plus semantic retrieval checks against current local data."""
import json
import time
from pathlib import Path
import assistant
from planning import rule_plan
from evaluation_cases import CASES

records=[]
for index,(question,tasks,roles,entities,days) in enumerate(CASES,1):
    started=time.monotonic()
    errors=[]
    plan=rule_plan(question)
    if not set(tasks)<=set(plan['tasks']):errors.append('Missing tasks: '+str(set(tasks)-set(plan['tasks'])))
    if plan['roles']!=roles:errors.append('Role filters changed')
    if plan['entities']!=entities:errors.append('Organisation filters changed')
    if plan['days']!=days:errors.append('Date window changed')
    try:
        answer=assistant.answer(question,False,plan,verify=False)
        for row in answer['facts']:
            if row.get('view')=='renewals':
                if not 0<=row['days_until_end']<=days:errors.append('Renewal outside date window')
                if plan['staffing_only'] and row['category_family']=='ICT equipment and general':errors.append('Equipment leaked into staffing results')
                if 'cyber' in roles:
                    from planning import ROLE_WORDS
                    if not any(x in (row.get('description') or '').lower() for x in ROLE_WORDS['cyber']):errors.append('Cyber filter lost')
            if row.get('view')=='panels' and row.get('deed_end')=='30-Sep-2025':
                if not row['date_status'].startswith('expired'):errors.append('Expired membership claimed current')
        if ('candidate' in question.lower() or 'cash runway' in question.lower()) and not answer['missing']:
            errors.append('Missing inputs concealed')
        if 'hiring' in question.lower() and not any('demand' in x for x in answer['missing']):
            errors.append('Current hiring uncertainty concealed')
        evidence_count=len(answer['facts'])
    except Exception as exc:
        errors.append(str(exc));evidence_count=0
    record={'case':index,'question':question,'passed':not errors,'errors':errors,
            'seconds':round(time.monotonic()-started,3),'evidence_count':evidence_count,'plan':plan}
    records.append(record)
    print(json.dumps({k:record[k] for k in ['case','passed','errors','seconds']}),flush=True)
directory=Path(__file__).parent/'reports';directory.mkdir(exist_ok=True)
(directory/'evaluation-v2.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
passed=sum(r['passed'] for r in records)
print(f'Passed {passed}/{len(records)}; this is query/evidence fidelity, not a live completeness or universal reasoning audit.')
raise SystemExit(passed!=len(records))

"""Evaluate actual local planning plus retrieval on held-out paraphrases."""
import json,time,sys
from pathlib import Path
import assistant
from heldout_cases import CASES

if len(sys.argv)>1:assistant.MODEL=sys.argv[1]
if len(sys.argv)>2 and sys.argv[2]=='second':
    from second_holdout import CASES
records=[]
for index,(question,tasks,roles,entities,days) in enumerate(CASES,1):
    began=time.monotonic();errors=[]
    try:
        result=assistant.answer(question,True,verify=False)
        plan=result['plan']
        if not set(tasks)<=set(plan['tasks']):errors.append('Missing tasks: '+str(set(tasks)-set(plan['tasks'])))
        if roles!=plan['roles']:errors.append('Role filters changed')
        if entities!=plan['entities']:errors.append('Organisation filters changed: '+str(plan['entities']))
        if days!=plan['days']:errors.append('Date window changed')
        if 'open' in question.lower() and any(r.get('view')=='opportunities' and r['status_effective'].lower()!='open' for r in result['facts']):
            errors.append('Planned records included as open')
        if 'current panels' in question.lower() and any(r.get('view')=='panels' and not r['date_status'].startswith('within') for r in result['facts']):
            errors.append('Expired panel included in current query')
        record={'case':index,'question':question,'passed':not errors,'errors':errors,'plan':plan,
                'model_used':result['model_used'],'model_error':result['model_error']}
    except Exception as exc:
        record={'case':index,'question':question,'passed':False,'errors':[str(exc)]}
    record['seconds']=round(time.monotonic()-began,3);records.append(record)
    print(json.dumps(record),flush=True)
directory=Path(__file__).parent/'reports';directory.mkdir(exist_ok=True)
name=assistant.MODEL.replace(':','-').replace('/','-')+('-second' if len(sys.argv)>2 else '')
(directory/f'heldout-{name}.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
print(f"Passed {sum(r['passed'] for r in records)}/{len(records)} for {assistant.MODEL}")
raise SystemExit(any(not r['passed'] for r in records))

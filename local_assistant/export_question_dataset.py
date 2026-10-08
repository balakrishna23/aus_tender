"""Prepare routing examples for human review; this does not train model weights."""
import json,re
from pathlib import Path
from planning import rule_plan,ALIASES,contains
from evaluation_cases import CASES

folder=Path(__file__).parent/'reports'/'training-review'
folder.mkdir(parents=True,exist_ok=True)
rows=[]
for index,(question,required_tasks,roles,entities,days) in enumerate(CASES,1):
    plan=rule_plan(question)
    raw_entities=[]
    for entity in plan['entities']:
        candidates=[key for key,value in ALIASES.items() if value==entity and contains(question,key)]
        if not candidates and entity.casefold() in question.casefold():candidates=[entity]
        if candidates:
            match=re.search(re.escape(max(candidates,key=len)),question,re.I)
            raw_entities.append(match.group(0))
    target={'tasks':plan['tasks'],'entities':raw_entities}
    row={'id':f'illoura-route-{index:03d}','review_status':'needs human review',
         'label_source':'Required tasks were annotated in the evaluation set; the complete target plan was expanded by the current rules.',
         'messages':[{'role':'system','content':'Extract supported database tasks and exact organisation substrings. Return JSON with tasks and entities. Do not supply facts or SQL.'},
                     {'role':'user','content':question},{'role':'assistant','content':json.dumps(target)}],
         'required_task_subset':required_tasks,'constraints':{'roles':roles,'resolved_entities':entities,'days':days}}
    rows.append(row)
for name,items in [('train-candidates.jsonl',rows[:40]),('validation-candidates.jsonl',rows[40:])]:
    (folder/name).write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in items),encoding='utf-8')
(folder/'manifest.json').write_text(json.dumps({'training_candidates':40,'validation_candidates':10,
    'status':'Prepared, not trained and not human-approved.',
    'limits':['All 50 examples were used during application refinement; this is not an untouched evaluation split.',
              'Routing examples do not teach current contract facts. Fresh procurement data must still be retrieved.',
              'Review full targets, add real user failures, and create a new untouched test set before weight training.']},indent=2),encoding='utf-8')
print('Prepared 40 training candidates and 10 validation candidates for review. No weights trained.')

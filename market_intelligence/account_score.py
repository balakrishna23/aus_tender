"""Explainable lane scores. Unknown inputs retain their weight and contribute no points."""
import json
from bisect import bisect_left, bisect_right
import math
from pathlib import Path

CONFIG = Path(__file__).parent / 'rules' / 'account_score.json'
LANES = {'federal','private','state','healthcare'}
COMPONENTS = {
 'federal': {'spend','frequency','expiry','buyict_gap','ipp_gap','levers','forward','open_opportunities','rap','lookalike','panel_access','grants','budget','demand_vs_bench'},
 'private': {'rap_type','plan_ending','procurement_target','employment_target','supplier_value','ipp_prime','lookalike'},
 'state': {'historical_value','historical_frequency','lookalike'},
 'healthcare': {'staffing_need','quality','size','rap'},
}


def validate_config(config):
    if set(config) != {'version','method','lanes','penalties','definitions','retrieved','source_notes','private_supplier_min_awarded_value_36m'}:
        raise ValueError('Unexpected account score config fields')
    if type(config['version']) is not int or config['version'] != 1 or config['method'] != 'relative_percentiles' or set(config['lanes']) != LANES:
        raise ValueError('Unsupported account scoring method/lanes')
    threshold=config['private_supplier_min_awarded_value_36m']
    if type(threshold) not in (int,float) or not math.isfinite(threshold) or threshold<0:raise ValueError('Invalid supplier universe threshold')
    def weight(v):
        return type(v) in (float,int) and math.isfinite(v) and 0<=v<=1
    for lane,weights in config['lanes'].items():
        if not isinstance(weights,dict) or set(weights)!=COMPONENTS[lane]:raise ValueError('Unexpected lane components')
        if not weights or any(not weight(w) for w in weights.values()) or abs(sum(weights.values())-1)>1e-9:
            raise ValueError('Lane weights must sum to one')
        if set(weights)-set(config['definitions']):raise ValueError('Undefined component')
    if set(config['penalties'])!={'blocker','insourcing'} or any(not weight(w) for w in config['penalties'].values()):
        raise ValueError('Invalid penalties')
    for definition in config['definitions'].values():
        if set(definition)!={'definition','basis'} or not all(isinstance(v,str) and v.strip() for v in definition.values()):
            raise ValueError('Components require definitions and data basis')
    from datetime import date
    date.fromisoformat(config['retrieved'])
    if not isinstance(config['source_notes'],list) or not config['source_notes'] or not all(isinstance(v,str) and v.strip() for v in config['source_notes']):raise ValueError('Source notes required')
    if set(config['definitions'])!=set.union(*COMPONENTS.values()):raise ValueError('Unexpected component definitions')
    return config


def load_config():
    return validate_config(json.loads(CONFIG.read_text(encoding='utf-8')))


def score_accounts(rows, config):
    validate_config(config)
    distributions={}
    for row in rows:
        if row.get('lane') not in LANES:raise ValueError('Unknown account lane')
        for name in config['lanes'][row['lane']]:
            value=row.get('inputs',{}).get(name)
            if value is None:continue
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Invalid component value')
            distributions.setdefault((row['lane'],name),[]).append(value)
    for values in distributions.values():values.sort()
    result=[]
    for original in rows:
        row=dict(original);weights=config['lanes'][row['lane']];components={};coverage=0;points=0
        for name,weight in weights.items():
            value=row.get('inputs',{}).get(name)
            basis=row.get('basis',{}).get(name) or config['definitions'][name]['basis']
            if value is None:
                components[name]=dict(value=None,weight=weight,points=None,basis='Unknown: '+basis)
                continue
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('Invalid component value')
            known=distributions[(row['lane'],name)]
            percentile=0 if value<=0 else 50*(bisect_left(known,value)+bisect_right(known,value))/len(known)
            component_points=weight*percentile;coverage+=weight;points+=component_points
            components[name]=dict(value=value,weight=weight,percentile=round(percentile,4),points=round(component_points,4),basis=basis)
        penalties={}
        for name,weight in config['penalties'].items():
            value=row.get('penalty_inputs',{}).get(name)
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1):raise ValueError('Invalid penalty')
            deduction=None if value is None else 100*weight*value
            penalties[name]=dict(value=value,weight=weight,points=None if deduction is None else -deduction,
                                 basis=row.get('penalty_basis',{}).get(name,'Unknown: no verified input'))
            if deduction is not None:points-=deduction
        row.update(score=round(max(0,points),2) if coverage else None,score_coverage_pct=round(coverage*100,2),components=components,penalties=penalties)
        row.setdefault('already_engaged',False)
        row['new_target']=not row['already_engaged'] and row.get('relationship_status')!='do_not_pursue'
        result.append(row)
    result.sort(key=lambda r:(r['score'] is None,-(r['score'] or 0),r['lane'],r['account'].casefold(),r['account_key']))
    counts={}
    for overall,row in enumerate(result,1):
        lane=row['lane'];counts[lane]=counts.get(lane,0)+1
        row.update(overall_rank=overall,rank_in_lane=counts[lane])
    return result

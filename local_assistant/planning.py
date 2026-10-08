"""Validated multi-part query plans. Domain constraints override model guesses."""
import re

TASKS = ['summary','company','renewals','opportunities','panels','today','exemptions',
         'rates','mmr','contacts','comparison','draft','quality','fact_check','research','sheets']
ROLE_WORDS = {
    'cyber': ['cyber','cybersecurity','information security','security analyst','security architect','penetration','infosec','irap'],
    'analysis': ['business analyst','business analysts','process analyst','systems analyst'],
    'AI/data': ['data analyst','data analysts','data engineer','data scientist','machine learning','artificial intelligence'],
    'architecture': ['enterprise architect','solution architect','solutions architect','technical architect'],
    'delivery': ['project manager','program manager','delivery manager','scrum master','product owner'],
    'engineering': ['developer','developers','software engineer','devops','cloud engineer','network engineer'],
    'testing': ['tester','test analyst','test manager','test automation'],
    'platforms': ['servicenow','sap','salesforce','dynamics 365','azure'],
}
ALIASES = {'DXC':'DXC', 'EY':'ERNST & YOUNG', 'APRA':'Australian Prudential Regulation Authority',
           'ATO':'Australian Taxation Office', 'OAIC':'Office of the Australian Information Commissioner',
           'DEWR':'Department of Employment and Workplace Relations', 'Defence':'Department of Defence',
           'Treasury':'Department of the Treasury', 'Fujitsu':'Fujitsu', 'Deloitte':'Deloitte',
           'KPMG':'KPMG', 'IBM':'IBM', 'AUCYBER':'AUCYBER', 'Geoscience':'Geoscience Australia',
           'Health':'Department of Health', 'Hays':'HAYS', 'Talent International':'TALENT INTERNATIONAL',
           'Dell':'DELL', 'Hudson':'HUDSON', 'Gillian Beaumont':'Gillian Beaumont'}


def contains(text, value):
    return bool(re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)',text,re.I))


def rule_plan(question):
    lower = question.lower()
    # Normalize common time phrases before extracting constraints.
    numbers={'one':1,'two':2,'three':3,'four':4,'six':6,'twelve':12,'thirty':30,'sixty':60,'ninety':90}
    for word,number in numbers.items():
        lower=re.sub(r'\b'+word+r'(?=\s+(?:days?|weeks?|months?)\b)',str(number),lower)
    lower=re.sub(r'\b(?:next|coming) quarter\b','next 90 days',lower)
    lower=re.sub(r'\b(?:next|coming) month\b','next 30 days',lower)
    lower=re.sub(r'\b(?:next|coming) week\b','next 7 days',lower)
    lower=re.sub(r'\bfortnight\b','14 days',lower)
    tasks, missing = [], []
    if re.search(r'\b(google sheets?|spreadsheet\w*|worksheets?|sheets?)\b|\btracker\s+(?:tab|notes?|rows?|status)\b|\brow\s+\d+\b.{0,60}\btab\b',lower):
        tasks.append('sheets')
    roles = [role for role, words in ROLE_WORDS.items() if any(contains(lower,w) for w in words)]
    if re.search(r'\bBAs?\b',question) and 'analysis' not in roles:roles.append('analysis')
    entities = [value for key,value in ALIASES.items() if contains(question,key)]
    for key in ['ERNST & YOUNG','Office of the Australian Information Commissioner',
                'Australian Taxation Office','Australian Prudential Regulation Authority']:
        if contains(question,key) and key not in entities:
            entities.append(key)
    ids = re.findall(r'\bCN\d+(?:-A\d+)?\b',question,re.I)
    panels = re.findall(r'\bSON\d+\b',question,re.I)
    days_match = re.search(r'\b(?:next|within|in|over)\s+(\d+)\s*days?\b',lower)
    if not days_match:
        days_match = re.search(r'\b(\d+)\s*days?\b',lower)
    days = int(days_match.group(1)) if days_match else 90
    months = re.search(r'\b(?:next|within)\s+(\d+)\s*months?\b',lower)
    if months:
        days = int(months.group(1))*30
        missing.append('Month windows use 30 days per month in this pilot; the resulting dates are displayed explicitly.')
    weeks=re.search(r'\b(?:next|within|in|over)\s+(\d+)\s*weeks?\b',lower)
    if weeks:
        days=int(weeks.group(1))*7
    if contains(lower,'next year') or contains(lower,'within a year'):
        days = 365
    if not 1 <= days <= 365:
        missing.append('This pilot supports renewal windows from 1 to 365 days; use a window in that range.')
    days = max(1,min(365,days))
    interval=re.search(r'\bbetween\s+(\d+)\s+(?:and|to)\s+(\d+)\s*days?\b',lower)
    start_days=0
    if interval:
        start_days,days=map(int,interval.groups())
        if not 0<=start_days<=days<=365:
            missing.append('The requested day interval is invalid or exceeds 365 days; no renewal result is reliable until it is corrected.')
            start_days,days=0,90
    abns=list(dict.fromkeys(re.sub(r'\D','',x) for x in re.findall(r'\bABN\s*[:#]?\s*((?:\d[ -]?){11})(?!\d)',question,re.I)))
    value_bounds={}
    for bound,pattern in [('min_value',r'(above|over|more than|at least|minimum)\s*(?:AUD\s*)?\$\s*([\d,.]+)\s*(million|thousand|m|k)?\b'),
                          ('max_value',r'(below|under|less than|at most|maximum)\s*(?:AUD\s*)?\$\s*([\d,.]+)\s*(million|thousand|m|k)?\b')]:
        match=re.search(pattern,lower,re.I)
        if match:
            scale={'m':1_000_000,'million':1_000_000,'k':1000,'thousand':1000,None:1}[match.group(3).lower() if match.group(3) else None]
            value_bounds[bound]=float(match.group(2).replace(',',''))*scale
            value_bounds[bound+'_inclusive']=match.group(1).lower() in ('at least','minimum','at most','maximum')
    mmr_match=re.search(r'\b(at least|over|more than)\s+(\d+)\s+(?:active\s+)?mmr\b',lower)
    staffing = bool(re.search(r'\b(labour hire|labor hire|staffing|placements?|ict people|ict staff|contractors?|recruitment)\b',lower))
    exclude = []
    negative_bodies=[]
    filter_text=re.sub(r'\b(?:do not|don.t)\s+(?:exclude|avoid)\b[^.!?;]*','',lower)
    for match in re.finditer(r'\b(exclude|excluding|not|rather than|without|avoid)\b([^.!?;]{0,160})',filter_text):
        if match.group(1)=='not' and not re.match(r'^\s*(?:(?:a|an|any|the)\s+)?(?:equipment|hardware|licen|saas|software as|assessment|one.off|cyber|business analyst|cloud|data|information security|security)\b',match.group(2)):
            continue
        negative_bodies.append(re.split(r'\b(?:but|include|including|instead)\b',match.group(2),maxsplit=1)[0])
    for label,pattern in [('equipment',r'equipment|hardware'),('licences',r'licen[cs]'),
                          ('saas',r'saas|software as a service'),('assessments',r'assessments?|one.off')]:
        if any(re.search(pattern,body) for body in negative_bodies):
            exclude.append(label)
    exclude_roles=[role for role,words in ROLE_WORDS.items() if any(contains(body,w) for body in negative_bodies for w in words)]
    roles=[r for r in roles if r not in exclude_roles]
    if re.search(r'\b(renew\w*|expir\w*|ending|end in|end within|ends? within|finishing|running out|coming to an end)\b',lower):
        tasks.append('renewals')
    if re.search(r'\b(panel\w*|memberships?)\b',lower) or panels:
        tasks.append('panels')
    if re.search(r'\b(open|opportunit\w*|tenders?|rfqs?|planned\b.{0,25}\bprocurements?|roles closing)\b',lower):
        tasks.append('opportunities')
    if re.search(r'\b(exemption\w*|set.aside|indigenous procurement)\b',lower):
        tasks.append('exemptions')
    if re.search(r'\b(mmr|indigenous participation|niaa)\b',lower):
        tasks.append('mmr')
    if re.search(r'\b(rates?|competitor\w*|winners?)\b',lower):
        tasks.append('rates')
    if re.search(r'\b(contact\w*|who to speak|warm\w*|approach\w*|introduction)\b',lower):
        tasks.append('contacts')
    if re.search(r'\b(compare|comparison|versus| vs\.? |attention first)\b',lower):
        tasks.append('comparison')
    if re.search(r'\b(draft|email|introduction under)\b',lower):
        tasks.append('draft')
    if re.search(r'\b(today actions|actions today|follow.?ups?|daily actions)\b',lower):
        tasks.append('today')
    if re.search(r'\b(data|source)\b.{0,35}\b(fresh|complete|old|coverage|gaps|updated)\b',lower) or re.search(
            r'\b(freshness|completeness|data quality|stale)\b',lower):
        tasks.append('quality')
    if re.search(r'\b(overview|what data|database contents|how much data)\b',lower):
        tasks.append('summary')
    if ids:
        tasks.append('fact_check')
    if not tasks and re.search(r'\bcontracts?\b',lower):
        tasks.append('company')
    if entities and (not tasks or any(t in tasks for t in ['contacts','comparison','draft'])):
        tasks.append('company')
    if abns and not tasks:
        tasks.append('company')
    if 'comparison' in tasks and len(entities)>=2:
        tasks += ['company','renewals','panels','mmr']
    if 'panels' in tasks and not re.search(r'\bcontracts?\b',lower) and 'comparison' not in tasks:
        tasks=[x for x in tasks if x!='renewals']
    if re.search(r'\b(cvs?|candidates?|bench|availability|cleared people)\b',lower):
        missing.append('Candidate CVs, clearance verification, availability and rates are not connected. No candidate suitability can be established.')
    if re.search(r'\b(looking for|hiring|urgently needs|staffing need|current demand)\b',lower):
        missing.append('Current hiring or subcontracting demand needs an explicit current company notice or human confirmation. Awards and renewals do not prove it.')
    if re.search(r'\b(warm|relationship|introduc\w*)\b',lower):
        missing.append('A warm path requires a verified relationship or introduction; the relationship register is not connected.')
    if re.search(r'\b(rap|reconciliation action plan)\b',lower):
        tasks.append('research')
        missing.append('Current RAP commitments and action numbers require research; no verified RAP table is connected.')
    if re.search(r'\b(live source|live website|web research|verified live|verify.*live|checked live)\b',lower):
        missing.append('This run reads stored data. Live website verification is not implemented, so sources are not claimed as checked live.')
    if re.search(r'\b(profit|margin|cash|payroll)\b',lower):
        missing.append('Commercial margins and payroll assumptions are not supplied.')
    if re.search(r'\b(nv1|nv2|baseline|clearance|cleared|citizenship)\b',lower):
        missing.append('Clearance and citizenship requirements are not extracted into this query. No clearance or eligibility fit is established.')
    if re.search(r'\b(canberra|sydney|melbourne|brisbane|perth|remote|onsite|on.site)\b',lower):
        missing.append('Delivery location and work arrangement filters are not connected. Supplier postal addresses are not treated as role locations.')
    if re.search(r'\b(202\d-\d\d-\d\d|january|february|march|april|may|june|july|august|september|october|november|december)\b',lower) and not ids:
        missing.append('Absolute calendar date filters are not supported yet; use an explicit relative day window. The default window is labelled in the answer.')
    if not tasks and roles:
        tasks.append('opportunities')
    sort = 'value' if re.search(r'\b(largest|highest value|biggest)\b',lower) else 'soonest'
    top_match = re.search(r'\b(?:top|choose|select|rank)\s+(\d+)\b',lower)
    top = min(10,max(1,int(top_match.group(1)))) if top_match else 3
    excludes_expired=bool(re.search(r'\b(exclude|excluding|without|not|avoid)\b.{0,25}\bexpired\b',lower))
    panel_status='expired' if 'panels' in tasks and re.search(r'\b(expired|ended|past)\b',lower) and not excludes_expired else \
        'current' if 'panels' in tasks and re.search(r'\b(current|active)\b',lower) else 'all'
    return {'tasks':list(dict.fromkeys(tasks)), 'entities':list(dict.fromkeys(entities)),
            'roles':roles,'days':days,'staffing_only':staffing,'exclude':exclude,
            'contract_ids':[x.upper() for x in ids], 'panel_ids':[x.upper() for x in panels],
            'sort':sort,'top':top,'missing':missing,'parser':'rules','panel_status':panel_status,
            'verify_live':bool(re.search(r'\b(verify|check|validate)\b.{0,50}\b(live|official api)\b',lower)),
            'research_requested':bool(re.search(r'\b(rap|reconciliation action plan|supplier onboarding|partnership|partnering)\b',lower)),
            'abns':abns,'start_days':start_days,**value_bounds,
            'exclude_roles':exclude_roles,
            'min_mmr_count':int(mmr_match.group(2)) if mmr_match else None,
            'min_mmr_inclusive':bool(mmr_match and mmr_match.group(1)=='at least'),
            'mmr_count_basis':'group_mmr_contracts' if contains(lower,'group') else 'mmr_contracts',
            'explicit_window':bool(days_match or months or weeks or interval or 'next year' in lower or 'within a year' in lower),
            'opportunity_status':'both' if contains(lower,'planned') and contains(lower,'open') else
                'planned' if contains(lower,'planned') else 'open' if contains(lower,'open') else 'both'}


def validated_model_plan(question, chat, initial):
    """Fallback for unfamiliar organisations or paraphrases; explicit filters survive."""
    schema={'type':'object','properties':{
        'tasks':{'type':'array','items':{'type':'string','enum':TASKS}},
        'entities':{'type':'array','items':{'type':'string'},'maxItems':3}},
        'required':['tasks','entities'],'additionalProperties':False}
    value,_=chat([
        {'role':'system','content':
         'Extract supported database tasks and organisation names. Entities must be exact substrings '
         'of the question, never a role like cyber or a generic word like company. '
         'Use company for supplier contracts; renewals for ending contracts; panels for membership; '
         'opportunities for open notices; unknown capabilities get no task. Never discard explicit constraints. '
         'Return only JSON.'}, {'role':'user','content':question}],schema,140)
    import json
    guessed=json.loads(value)
    if not isinstance(guessed,dict) or not isinstance(guessed.get('tasks'),list) or not isinstance(guessed.get('entities'),list):
        raise ValueError('Model returned an invalid plan structure')
    if len(guessed['entities'])>3 or any(not isinstance(x,str) or len(x)>160 for x in guessed['entities']):
        raise ValueError('Model returned invalid organisation names')
    if any(x not in TASKS for x in guessed.get('tasks',[])):
        raise ValueError('Unsupported task')
    banned=set(ROLE_WORDS)|{'company','agency','contract','contracts','equipment','people','cyber','ict','supplier'}
    entities=[x.strip() for x in guessed.get('entities',[]) if isinstance(x,str) and x.strip()]
    if any(x.casefold() not in question.casefold() or x.casefold() in banned for x in entities):
        raise ValueError('Unverified organisation extraction; use the exact company name')
    # A model can copy a substring correctly while assigning it the wrong meaning.
    entities=[x for x in entities if not any(contains(x,w) for words in ROLE_WORDS.values() for w in words)
              and not re.search(r'\b(contracts?|days?|months?|weeks?|evidence|within|ending|renewals?|panels?|suppliers?)\b',x,re.I)]
    plan=dict(initial)
    if not plan['tasks']:
        plan['tasks']=guessed.get('tasks',[])
    if not plan['entities']:
        plan['entities']=entities
    if plan['entities'] and not initial['tasks'] and re.search(r'\b(about|contracts?|footprint|know)\b',question,re.I):
        plan['tasks']=['company']
    plan['parser']='rules + local model'
    return plan

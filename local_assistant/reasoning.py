"""Evidence-led, multi-view analysis. Models never supply contract facts or arithmetic."""
import math
import re
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from planning import ROLE_WORDS


def date_value(value):
    if not value:
        return None
    for fmt in ['%Y-%m-%d','%d-%b-%Y','%d %b %Y','%d/%m/%Y']:
        try:
            return datetime.strptime(str(value)[:10] if fmt=='%Y-%m-%d' else str(value),fmt).date()
        except ValueError:
            pass
    return None


def money(value):
    return f'AUD ${value:,.2f}' if isinstance(value,(int,float)) else 'not recorded'


def analyse(plan, query, like, today=None):
    today=today or datetime.now(ZoneInfo('Australia/Canberra')).date()
    start=today+timedelta(days=plan.get('start_days',0))
    end=today+timedelta(days=plan['days'])
    facts, sections, missing, metrics = [], [], list(plan['missing']), {}
    retrieval_status=[]
    hybrid_enabled=bool(plan.get('hybrid') and plan.get('search_topic'))

    def hybrid_select(rows,field,view):
        from hybrid import rank,lexical_scores
        from urllib.error import URLError
        import sqlite3
        try:
            selected,status=rank(rows,plan['search_topic'],field)
            if not plan.get('explicit_search_topic') and plan['roles'] and view!='public_research':
                # Preserve existing exact role matches as the lexical leg of the union.
                def identity(row):return (row.get('cn_id'),row.get('source'),row.get('source_id'))
                selected_ids={identity(row) for row in selected}
                for row in rows:
                    if identity(row) not in selected_ids and any(re.search(r'(?<!\w)'+re.escape(word)+r'(?!\w)',row.get(field,'') or '',re.I)
                            for role in plan['roles'] for word in ROLE_WORDS[role]):
                        selected.append({**row,'retrieval':{'method':'exact role keyword','topic':plan['search_topic'],
                            'limit':'Role wording only; not verified capability or current demand.'}})
                status['matched_records']=len(selected)
        except (ValueError,OSError,URLError,TimeoutError,sqlite3.Error,ImportError) as exc:
            # Always apply lexical relevance on fallback; never return all SQL candidates.
            if plan.get('explicit_search_topic'):
                scores=lexical_scores(plan['search_topic'],[r.get(field,'') for r in rows])
                selected=[r for r,score in zip(rows,scores) if score>0]
            else:
                selected=[r for r in rows if any(re.search(r'(?<!\w)'+re.escape(word)+r'(?!\w)',r.get(field,'') or '',re.I)
                    for role in plan['roles'] for word in ROLE_WORDS[role])]
            status={'mode':'keyword fallback','topic':plan['search_topic'],'eligible_records':len(rows),
                    'matched_records':len(selected),'reason':str(exc)}
            missing.append('Semantic search unavailable: '+str(exc)+'. Keyword results are shown; related wording may be missed.')
        status['view']=view;retrieval_status.append(status)
        return selected

    def evidence(view, rows):
        for row in rows:
            row=dict(row)
            row['view']=view
            row['evidence_id']='E'+str(len(facts)+1)
            facts.append(row)
        return facts[-len(rows):] if rows else []

    def q(sql, params=(),db='intelligence.db'):
        return query(db,sql,params)

    def conditions(name='description',entity_columns=('buyer','supplier'),entities=None,contract_filters=True,category_filter=True,semantic=False):
        clauses,params=[],[]
        entities=plan['entities'] if entities is None else entities
        if entities:
            terms=[]
            for entity in entities:
                terms.append('('+' OR '.join(f"{column} LIKE ? ESCAPE '\\'" for column in entity_columns)+')')
                params += [like(entity)]*len(entity_columns)
            clauses.append('('+' OR '.join(terms)+')')
        if plan['roles'] and not (semantic and hybrid_enabled and not plan.get('explicit_search_topic')):
            terms=[]
            for role in plan['roles']:
                for word in ROLE_WORDS[role]:
                    terms.append(f"{name} LIKE ? ESCAPE '\\'")
                    params.append(like(word))
            clauses.append('('+' OR '.join(terms)+')')
        for role in plan.get('exclude_roles',[]):
            for word in ROLE_WORDS[role]:
                clauses.append(f"COALESCE({name},'') NOT LIKE ? ESCAPE '\\'")
                params.append(like(word))
        if plan['staffing_only']:
            if category_filter:
                clauses.append("category_family IN ('ICT services','labour hire and recruitment')")
            # Category codes alone can label equipment or an assessment as labour hire.
            excluded=['equipment','hardware','licence','license','software as a service','saas','subscription']
        else:
            excluded=[]
        mappings={'equipment':['equipment','hardware'],'licences':['licence','license'],
                  'saas':['saas','software as a service'],'assessments':['assessment','audit']}
        for label in plan['exclude']:
            excluded += mappings[label]
        for term in dict.fromkeys(excluded):
            clauses.append(f"COALESCE({name},'') NOT LIKE ? ESCAPE '\\'")
            params.append(like(term))
        if contract_filters:
            if plan.get('abns'):
                clauses.append('supplier_abn IN ('+','.join('?' for _ in plan['abns'])+')')
                params+=plan['abns']
            for bound,operator in [('min_value','>='),('max_value','<=')]:
                if bound in plan:
                    if not plan.get(bound+'_inclusive',True):operator=operator[0]
                    clauses.append('current_value '+operator+' ?');params.append(plan[bound])
        return (' AND '.join(clauses) or '1=1'),params

    if 'summary' in plan['tasks'] or 'quality' in plan['tasks']:
        counts={table:q('SELECT COUNT(*) AS n FROM '+table)[0]['n'] for table in
                ['opportunities','renewals','panel_members','mmr_primes','today']}
        contract=q('SELECT COUNT(*) AS contracts,MAX(first_published) AS latest_publication,'
                   'MAX(retrieved_at) AS latest_retrieval FROM contracts',db='contract_api.db')[0]
        build=q('SELECT built_at FROM build_info LIMIT 1')[0]
        rows=evidence('coverage',[{'counts':counts,**contract,**build}])
        labels={'opportunities':'opportunity records','renewals':'contracts in the renewal view',
                'panel_members':'panel member records','mmr_primes':'MMR supplier matches','today':'TODAY actions'}
        sections.append('Stored coverage: '+', '.join(f'{labels[key]}: {value:,}' for key,value in counts.items())+
                        f"; contracts: {contract['contracts']:,}. [{rows[0]['evidence_id']}]")
        if 'quality' in plan['tasks']:
            missing.append('Record totals do not prove completeness against source totals. No live completeness reconciliation was performed.')
            import json
            from pathlib import Path
            audit_path=Path(__file__).parent/'reports'/'data-quality.json'
            if audit_path.is_file():
                audit=json.loads(audit_path.read_text(encoding='utf-8'))
                if audit.get('build_timestamp')==build['built_at']:
                    quality=evidence('data_quality_audit',[audit])[0]
                    sections.append(f"Quality audit at {audit['audited_at_utc']}: {audit['contracts']['missing_supplier_abn']:,} contracts lack a supplier ABN; "
                                    f"{audit['contracts']['end_before_start']} have an end date before their start; "
                                    f"{audit['panels']['expired_member_records']} panel member rows are historical by deed date. "
                                    f"Missing ABNs may include foreign suppliers; no identity is guessed. [{quality['evidence_id']}]")
                else:
                    missing.append('The saved quality audit predates this database build; rerun audit_data.py before relying on its counts.')

    company_searches=plan['entities'] or (['ABN '+', '.join(plan['abns'])] if plan.get('abns') else [])
    if 'company' in plan['tasks'] and not company_searches:
        missing.append('Specify a company, agency or supplier ABN for a contract profile; no organisation is guessed.')
    for entity in company_searches if 'company' in plan['tasks'] or 'comparison' in plan['tasks'] else []:
        predicate,params=conditions(entities=[] if entity.startswith('ABN ') else [entity])
        params=tuple(params)
        aggregates=q('SELECT supplier,supplier_abn,COUNT(*) AS stored_contracts,'
            "SUM(CASE WHEN date(current_end_date)>=date(?) THEN 1 ELSE 0 END) AS active_by_end_date,"
            "SUM(CASE WHEN category_family='labour hire and recruitment' THEN 1 ELSE 0 END) AS labour_hire_coded_contracts,"
            "SUM(CASE WHEN date(current_end_date)>=date(?) AND category_family IN ('ICT services','labour hire and recruitment') THEN 1 ELSE 0 END) AS active_services_contracts "
            'FROM contracts WHERE '+predicate+' GROUP BY supplier,supplier_abn ORDER BY stored_contracts DESC',
            (today.isoformat(),today.isoformat())+params,db='contract_api.db')
        rows=evidence('company_totals',[{'search_entity':entity,**row,'basis':'Full stored contract table with query filters; groups use exact supplier name and ABN.'}
                                      for row in aggregates[:12]])
        total=sum(row['stored_contracts'] for row in aggregates)
        abns={r['supplier_abn'] for r in aggregates if r['supplier_abn']}
        metrics[entity]={'contracts':total,'legal_abns':len(abns),
                         'labour_hire_coded':sum(r['labour_hire_coded_contracts'] or 0 for r in aggregates),
                         'active_services':sum(r['active_services_contracts'] or 0 for r in aggregates)}
        sections.append(f'{entity}: {total:,} matching stored contracts across {len(abns)} recorded ABNs. '+
                        ('Supplier ABNs are matched exactly. ' if plan.get('abns') else 'This is a name search, not a verified corporate-group consolidation. ')+
                        ' '.join('['+r['evidence_id']+']' for r in rows[:3]))
        sample=q('SELECT cn_id,buyer,supplier,supplier_abn,description,category_family,current_value,'
                 'current_end_date,start_date,first_published,retrieved_at,page_url,source_url FROM contracts WHERE '+predicate+
                 ' ORDER BY first_published DESC LIMIT 4',params,db='contract_api.db')
        evidence('company_contract_examples',sample)

    if 'fact_check' in plan['tasks']:
        for cn in plan['contract_ids']:
            rows=evidence('named_contract',q('SELECT cn_id,buyer,supplier,supplier_abn,description,category_family,'
                'current_value,current_end_date,start_date,amendment_count,retrieved_at,page_url,source_url FROM contracts WHERE cn_id=?',
                (cn,),db='contract_api.db'))
            if rows:
                r=rows[0]
                sections.append(f"{cn}: {r['supplier']} supplies {r['buyer']} for {r['description']}; "
                                f"recorded value {money(r['current_value'])}; end date {r['current_end_date']}. [{r['evidence_id']}]")
                if plan['entities'] and not any(e.casefold() in (r['buyer']+' '+r['supplier']).casefold() for e in plan['entities']):
                    missing.append(f'{cn}: its recorded buyer/supplier does not match the requested organisation filter. No association with that organisation is inferred.')
            else:
                missing.append(f'{cn} was not found in the stored contract table; no details are invented.')

    if 'renewals' in plan['tasks']:
        where,params=conditions(semantic=True)
        params=[start.isoformat(),end.isoformat()]+params
        predicate='date(current_end_date) BETWEEN date(?) AND date(?) AND '+where
        total=q('SELECT COUNT(*) AS n FROM renewals WHERE '+predicate,params)[0]['n']
        sort='current_value DESC,current_end_date' if plan['sort']=='value' else 'current_end_date,current_value DESC'
        # Retrieve the complete bounded window before selecting; no AI sample determines the rank.
        rows=q('SELECT cn_id,buyer,supplier,supplier_abn,description,category_family,current_value,current_end_date,'
               'contact_safety,page_url,api_url FROM renewals WHERE '+predicate+' ORDER BY '+sort,params)
        if hybrid_enabled:
            rows=hybrid_select(rows,'description','renewals');total=len(rows)
        for row in rows:
            row['days_until_end']=(date_value(row['current_end_date'])-today).days
            explicit=bool(re.search(r'\b(labour hire|contractor|personnel|analyst|architect|developer|engineer|staff|recruitment)\b',row['description'] or '',re.I))
            row['staffing_fit']='Explicit staffing/role wording; availability and demand unconfirmed' if explicit else 'Services signal only; staffing suitability unconfirmed'
        if plan['staffing_only'] and plan['sort']!='value':
            rows.sort(key=lambda r:(not r['staffing_fit'].startswith('Explicit'),
                -r.get('retrieval',{}).get('fusion_score',0) if hybrid_enabled else 0,
                r['days_until_end'],-(r['current_value'] or 0)))
        if hybrid_enabled and plan['sort']=='value':rows.sort(key=lambda r:-(r['current_value'] or 0))
        selected=evidence('renewals',rows[:max(8,plan['top'])])
        role=' OR '.join(plan['roles']) or 'all roles'
        if plan.get('exclude_roles'):role+='; excluding '+', '.join(plan['exclude_roles'])
        sections.append(f"Renewals: {total:,} matching stored contracts ({role}), from {start} through {end}, "
                        f"inclusive, using Australia/Canberra dates. Ranking: {'recorded value within relevant results' if plan['sort']=='value' else 'explicit staffing wording first, then hybrid relevance and end date' if hybrid_enabled and plan['staffing_only'] else 'hybrid relevance within exact constraints' if hybrid_enabled else 'explicit staffing wording first, then nearest end date' if plan['staffing_only'] else 'nearest end date'}. "
                        'An end date does not confirm a renewal or vacancy.')
        for row in selected[:plan['top']]:
            sections.append(f"{row['cn_id']} — {row['supplier']} / {row['buyer']}: {row['description']}; "
                            f"{money(row['current_value'])}; ends {row['current_end_date']} ({row['days_until_end']} days). "
                            f"Fit: {row['staffing_fit']}. [{row['evidence_id']}]")
        for entity in metrics:
            metrics[entity]['renewals']=sum(1 for row in rows if entity.casefold() in (row['supplier']+' '+row['buyer']).casefold())
        if 'comparison' in plan['tasks'] and not plan['entities']:
            shortlist=selected[:plan['top']]
            sections.append('Shortlist comparison: '+ '; '.join(
                f"{r['supplier']}: {r['days_until_end']} days, {money(r['current_value'])}, {r['staffing_fit']} [{r['evidence_id']}]"
                for r in shortlist)+'. This prioritises investigation; no candidate readiness or buyer demand is established.')

    if 'opportunities' in plan['tasks']:
        where,params=conditions('title',('buyer',),contract_filters=False,semantic=True)
        if plan.get('abns') or 'min_value' in plan or 'max_value' in plan:
            missing.append('Contract-value and supplier ABN filters apply to contracts; opportunity notices do not expose comparable contract values or awarded suppliers here.')
        open_predicate="(lower(status_effective)='open' AND closes_at IS NOT NULL AND julianday(closes_at)>julianday('now'))"
        status=plan.get('opportunity_status','both')
        status_predicate=open_predicate if status=='open' else "lower(status_effective)='planned'" if status=='planned' else "(lower(status_effective)='planned' OR "+open_predicate+')'
        predicate='is_relevant=1 AND '+status_predicate+' AND '+where
        if plan.get('explicit_window') and status!='planned':
            predicate+=" AND (lower(status_effective)='planned' OR (julianday(closes_at)>=julianday(?) AND julianday(closes_at)<julianday(?)))"
            zone=ZoneInfo('Australia/Canberra')
            params+=[datetime.combine(start,datetime.min.time(),zone).isoformat(),
                     datetime.combine(end+timedelta(days=1),datetime.min.time(),zone).isoformat()]
        total=q('SELECT COUNT(*) AS n FROM opportunities WHERE '+predicate,params)[0]['n']
        opportunity_rows=q('SELECT source,source_id,title,buyer,role_families,category_family,'
            'closes_at,expected_from,expected_to,status_effective,contact_safety,url,retrieved_at FROM opportunities WHERE '+predicate+
            " ORDER BY CASE WHEN lower(status_effective)='planned' THEN 1 ELSE 0 END,closes_at"+('' if hybrid_enabled else ' LIMIT 8'),params)
        if hybrid_enabled:opportunity_rows=hybrid_select(opportunity_rows,'title','opportunities');total=len(opportunity_rows)
        rows=evidence('opportunities',opportunity_rows[:8])
        label='open notices' if status=='open' else 'stored plans' if status=='planned' else 'open notices or stored plans'
        sections.append(f'Opportunities: {total:,} relevant {label} match the filters. Closed timestamps are excluded; dates missing from open notices are not treated as current.')
        for row in rows[:plan['top']]:
            sections.append(f"{row['source_id']} — {row['buyer']}: {row['title']}; stored status {row['status_effective']}; "
                            f"closing time {row['closes_at'] or 'not recorded'}; contact rule {row['contact_safety']}. [{row['evidence_id']}]")

    if 'panels' in plan['tasks']:
        clauses,params=[],[]
        for entity in plan['entities']:
            clauses.append("member LIKE ? ESCAPE '\\'");params.append(like(entity))
        for panel in plan['panel_ids']:
            clauses.append('son_id=?');params.append(panel)
        rows=q('SELECT son_id,panel,member,abn,deed_start,deed_end,panel_url FROM panel_members WHERE '+
               ('('+' OR '.join(clauses)+')' if clauses else '1=1'),params)
        if plan.get('abns'):rows=[r for r in rows if str(r['abn']).replace(' ','') in plan['abns']]
        if plan['roles']:missing.append('Panel membership does not verify role capability; role filters are applied to contract/opportunity descriptions, not inferred from membership.')
        for row in rows:
            start_date,end_date=date_value(row['deed_start']),date_value(row['deed_end'])
            row['date_status']='expired by recorded deed end' if end_date and end_date<today else \
                'not yet started by recorded deed date' if start_date and start_date>today else \
                'within recorded deed dates; not verified live' if start_date and end_date else 'dates incomplete; current membership unknown'
        if plan.get('panel_status')=='expired':
            rows=[r for r in rows if r['date_status'].startswith('expired')]
        elif plan.get('panel_status')=='current':
            rows=[r for r in rows if r['date_status'].startswith('within')]
        chosen=evidence('panels',rows[:12])
        sections.append(f'Panel records: {len(rows):,} matches. Deed dates are checked before calling membership current.')
        for row in chosen[:min(6,plan['top'])]:
            sections.append(f"{row['member']} — {row['panel']} ({row['son_id']}): {row['date_status']}; "
                            f"{row['deed_start']} to {row['deed_end']}. [{row['evidence_id']}]")
        for entity in metrics:
            metrics[entity]['panels_in_date']=len({r['son_id'] for r in rows if entity.casefold() in r['member'].casefold() and r['date_status'].startswith('within')})

    if 'mmr' in plan['tasks']:
        clauses,params=[],[]
        for entity in plan['entities']:
            clauses.append("(organisation LIKE ? ESCAPE '\\' OR group_name LIKE ? ESCAPE '\\')")
            params.extend([like(entity),like(entity)])
        predicate='('+' OR '.join(clauses)+')' if clauses else '1=1'
        if plan.get('abns'):
            predicate+=' AND abn IN ('+','.join('?' for _ in plan['abns'])+')';params+=plan['abns']
        basis=plan.get('mmr_count_basis','mmr_contracts')
        if basis not in ('mmr_contracts','group_mmr_contracts'):raise ValueError('Unsupported MMR count basis')
        if plan.get('min_mmr_count') is not None:
            predicate+=' AND '+basis+(' >= ?' if plan.get('min_mmr_inclusive') else ' > ?')
            params.append(plan['min_mmr_count'])
        all_rows=q('SELECT organisation,group_name,abn,mmr_contracts,group_mmr_contracts,list_date,'
            'match_review FROM mmr_primes WHERE '+predicate+' ORDER BY '+basis+' DESC,organisation',params)
        rows=evidence('mmr',all_rows[:12])
        sections.append(f'MMR evidence: {len({r["organisation"] for r in all_rows})} distinct listed organisations match; counts are as at the displayed NIAA list date, not independently confirmed today. Repeated ABN match rows are not summed as extra MMR contracts.')
        for row in rows:
            sections.append(f"{row['organisation']} (ABN {row['abn']}): NIAA-listed count {row['mmr_contracts']}; "
                            f"group count {row['group_mmr_contracts']}; list date {row['list_date']}. [{row['evidence_id']}]")
        missing.append('The NIAA list does not identify which public contracts carry MMRs or where a prime is behind. A $7.5m+ contract is only a candidate, not a confirmed obligation.')

    if 'exemptions' in plan['tasks']:
        where,params=conditions()
        cutoff=today-timedelta(days=730)
        predicate='date(first_published)>=date(?) AND '+where
        params=[cutoff.isoformat()]+params
        rows=q('SELECT buyer,COUNT(DISTINCT cn_id) AS contract_count,SUM(current_value) AS recorded_value,'
               'MAX(first_published) AS latest_publication FROM exemption_use WHERE '+predicate+
               ' GROUP BY buyer ORDER BY contract_count DESC',params)
        if 'renewals' in plan['tasks']:
            renewal_where,renewal_params=conditions()
            renewal_counts=q('SELECT buyer,COUNT(*) AS n,MIN(current_end_date) AS earliest_end FROM renewals '
                'WHERE date(current_end_date) BETWEEN date(?) AND date(?) AND '+renewal_where+' GROUP BY buyer',
                [start.isoformat(),end.isoformat()]+renewal_params)
            by_buyer={r['buyer']:r for r in renewal_counts}
            rows=[{**r,'matching_renewals':by_buyer[r['buyer']]['n'],
                   'earliest_renewal_end':by_buyer[r['buyer']]['earliest_end']}
                  for r in rows if r['buyer'] in by_buyer]
            for row in rows:
                live_where,live_params=conditions('title',('buyer',),entities=[row['buyer']],contract_filters=False)
                row['matching_live_notices']=q("SELECT COUNT(*) AS n FROM opportunities WHERE lower(status_effective)='open' "
                    "AND julianday(closes_at)>julianday('now') AND "+live_where,live_params)[0]['n']
                row['contact_route']='FORMAL_CHANNEL_ONLY for matching live requirements' if row['matching_live_notices'] else 'No matching live notice found in stored view; recheck live before a general introduction'
            rows.sort(key=lambda r:(r['matching_live_notices']>0,-r['contract_count'],-r['matching_renewals']))
        selected=evidence('exemptions',rows[:plan['top']])
        sections.append(f'Exemption history: {len(rows)} matching buyers in the rolling 730-day window; counts are contracts, not future purchasing commitments.')
        for row in selected:
            examples=q('SELECT cn_id,description,page_url,api_url FROM exemption_use WHERE '+predicate+
                       ' AND buyer=? ORDER BY first_published DESC LIMIT 2',params+[row['buyer']])
            evidence('exemption_examples',examples)
            sections.append(f"{row['buyer']}: {row['contract_count']} recorded direct-purchase contracts; "
                            f"value {money(row['recorded_value'])}; latest publication {row['latest_publication']}. [{row['evidence_id']}]")
            if 'matching_renewals' in row:
                sections.append(f"{row['buyer']}: {row['matching_renewals']} matching renewals within {plan['days']} days; "
                                f"first ends {row['earliest_renewal_end']}; {row['matching_live_notices']} matching live notices. "
                                f"Contact route: {row['contact_route']}. [{row['evidence_id']}]")

    if 'rates' in plan['tasks']:
        where,params=conditions('role_title',('buyer','supplier'),contract_filters=False,category_filter=False)
        if plan.get('abns') or 'min_value' in plan or 'max_value' in plan:
            missing.append('Supplier ABN and total contract-value constraints are not applied to the estimated-rate view. It does not establish an estimated daily-rate ceiling or floor.')
        rows=q('SELECT cn_id,buyict_id,role_title,buyer,supplier,estimated_day_rate,rate_note,'
               'buyict_url,page_url FROM buyict_links WHERE '+where,params)
        unique={r['cn_id']:r for r in rows}
        valid=[r for r in unique.values() if isinstance(r['estimated_day_rate'],(int,float)) and r['estimated_day_rate']>0]
        valid.sort(key=lambda r:r['estimated_day_rate'])
        evidence('estimated_rates',valid[:8])
        if valid:
            import statistics
            sections.append(f"Estimated rates: {len(valid)} distinct linked contracts across all stored history; median {money(statistics.median(r['estimated_day_rate'] for r in valid))}/day; "
                            f"range {money(valid[0]['estimated_day_rate'])}–{money(valid[-1]['estimated_day_rate'])}/day. These are contract-value estimates, not candidate pay rates.")
        else:
            sections.append('No positive estimated rates match these filters.')

    if 'today' in plan['tasks']:
        rows=evidence('today',q('SELECT rank,organisation,priority,score,action_type,why_now,owner,due,contact_safety,evidence,assumptions '
                              'FROM today ORDER BY rank LIMIT 8'))
        for row in rows[:plan['top']]:
            sections.append(f"{row['organisation']}: {row['action_type']}; owner {row['owner']}; due {row['due']}; "
                            f"reason: {row['why_now']}. [{row['evidence_id']}]")
        missing.append('TODAY scores and recommendations are stored engine outputs; bench and relationship assumptions may be neutral or unconfirmed.')

    if 'contacts' in plan['tasks']:
        if plan['entities']:
            clauses=["organisation LIKE ? ESCAPE '\\'" for _ in plan['entities']]
            rows=q('SELECT organisation,name,email,phone,source_url,verified,confidence,stale,opted_out,'
                   'contact_safety,usable_for_outreach FROM agency_contacts WHERE '+' OR '.join(clauses),
                   [like(e) for e in plan['entities']])
            safe=[r for r in rows if r['usable_for_outreach'] and not r['opted_out'] and not r['stale']]
            selected=evidence('contacts',safe[:4])
            sections.append(f'{len(safe)} stored usable agency contacts match the organisation filter. These are not verified warm introductions or company hiring contacts.')
            # A relevant live notice requires formal channels, even if the contacts table is permissive.
            predicates,params=[],[]
            for entity in plan['entities']:
                predicates.append("buyer LIKE ? ESCAPE '\\'");params.append(like(entity))
            live=q("SELECT source_id,title,buyer,url,contact_safety FROM opportunities WHERE lower(status_effective)='open' "
                   "AND julianday(closes_at)>julianday('now') AND ("+' OR '.join(predicates)+')',params)
            if live:
                evidence('contact_safety_live_notices',live[:4])
                missing.append('Matching buyer has live notices. Do not discuss a live requirement informally; use formal Q&A. Relevance must be checked before any general introduction.')
        else:
            missing.append('Specify an agency or company to check a contact path; no recipient is guessed.')

    if plan.get('research_requested') or (plan['entities'] and any(t in plan['tasks'] for t in ['company','contacts','comparison'])):
        from public_research import findings
        research_rows=findings(plan['entities'],plan['roles'])
        available_research=research_rows[:]
        if hybrid_enabled and plan.get('research_requested'):
            research_rows=[{**r,'search_text':r.get('title','')+'. '+r.get('finding','')} for r in research_rows]
            research_rows=hybrid_select(research_rows,'search_text','public_research')
        research=evidence('public_research',research_rows)
        for row in research:
            sections.append(f"Public evidence — {row['organisation']}: {row['title']}. {row['finding']} "
                            f"{row.get('period_status','')} Scope: {row['limit']} "
                            f"Reviewed {row['reviewed_at_utc']}; {row['review_status']}. [{row['evidence_id']}]")
            if row.get('email') and 'contacts' in plan['tasks']:
                sections.append(f"Published route: {row['email']}; {row['locator']}. This is not a warm relationship. [{row['evidence_id']}]")
        if plan.get('research_requested'):
            missing=[x for x in missing if not x.startswith('Current RAP commitments and action numbers')]
            covered={r['organisation'].casefold() for r in available_research if r['kind']=='RAP' and r.get('review_age_days',999)<=30}
            for entity in plan['entities']:
                if not any(org in entity.casefold() or entity.casefold() in org for org in covered):
                    missing.append(f'{entity}: no recent RAP review is available in the public research register. A current plan and procurement commitments remain to be checked.')
            missing.append('RAP commitments and public contact routes do not prove unmet obligations, current subcontracting demand or an existing relationship. No general live web search runs inside this pilot.')

    if 'comparison' in plan['tasks']:
        if len(metrics)<2 and plan['entities']:
            missing.append('A comparison needs at least two identifiable organisations.')
        elif len(metrics)>=2:
            for entity,value in metrics.items():
                value['research_score']=round(10*math.log1p(value['active_services'])+
                    5*math.log1p(value.get('renewals',0))+3*value.get('panels_in_date',0),1)
            ranked=sorted(metrics,key=lambda x:metrics[x]['research_score'],reverse=True)
            rows=evidence('comparison',[{'organisation':e,**metrics[e],
                'score_definition':'10*ln(1+active service-coded contracts)+5*ln(1+matching renewals)+3*distinct panels within recorded dates',
                'score_limit':'Research prioritisation only; not delivery fit, current demand or win probability.'} for e in ranked])
            sections.append('Research priority, using the stated provisional formula: '+
                            '; '.join(f"{r['organisation']} {r['research_score']} [{r['evidence_id']}]" for r in rows)+
                            '. Missing bench, warm access, current demand and RAP facts could reverse this ranking.')

    if 'draft' in plan['tasks']:
        hooks=[r for r in facts if r['view'] in ('named_contract','renewals','company_contract_examples')]
        if hooks and not any(r.get('contact_safety')=='FORMAL_CHANNEL_ONLY' for r in facts):
            r=hooks[0]
            draft=(f"Hello,\n\nI’m Sirish, co-founder of Illoura Services. Your published contract {r['cn_id']} "
                   f"with {r['buyer']} refers to {r['description']}. We would welcome an exploratory conversation "
                   "about whether there may be a suitable future ICT staffing partnership. We can confirm our "
                   "capability and candidate availability against any requirement you share.\n\n"
                   "Would you be the right person to speak with, or could you direct us to your supplier onboarding team?\n\n"
                   "Regards,\nSirish, Illoura Services\nIf you prefer not to receive further introductions, please let us know.")
            sections.append('Draft for human review; recipient not inferred; copy Lane when sending:\n'+draft+
                            f"\nEvidence for the contract reference: [{r['evidence_id']}].")
        else:
            missing.append('No safe, sourced contract hook is available for a draft. Live procurement requires formal Q&A; no email is sent.')

    if not facts:
        sections.append('No matching evidence was retrieved. This does not establish absence of work or demand.')
    for row in facts:
        if row.get('start_date') and row.get('current_end_date') and date_value(row['start_date']) and date_value(row['current_end_date']):
            if date_value(row['current_end_date'])<date_value(row['start_date']):
                row['data_quality_issue']='Recorded end date precedes start date; term planning is unconfirmed.'
                missing.append(f"{row.get('cn_id','Contract')}: recorded end date precedes start date. Check the official notice; no replacement date is guessed.")
    return {'facts':facts,'sections':sections,'missing':list(dict.fromkeys(missing)),
            'metrics':metrics,'as_of_date':today.isoformat(),'timezone':'Australia/Canberra',
            'retrieval_status':retrieval_status}

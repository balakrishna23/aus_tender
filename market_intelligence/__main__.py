import argparse
import json
import sys
from datetime import date
from sqlalchemy import text
from .db import engine, migrate
from .ingest import import_snapshot
from .matching import bridge_buyers
from .review import decide
from .review_report import read_queue,write_queue
from .procurement import import_procurement
from .analytics import build_report,write_report
from .panels import worklist,write_worklist
from .quality import report as quality_report
from .registers import SOURCES as REGISTER_SOURCES,import_register
from .supplier_checks import build_checks,write_checks
from .buyict import build_buyict,write_buyict
from .levers import build_levers,write_levers
from .watchlist import seed_watchlist,export_watchlist,mark_watchlist,set_profile,show_profile,category_summary
from .aps import import_aps,build_aps_report,write_aps_report
from .demand import SOURCES as DEMAND_SOURCES,import_demand,build_demand,write_demand
from .panel_access import record_panel_evidence,build_panel_access,write_panel_access
from .forward import import_forward,build_forward,write_forward
from .research import import_research,build_rap,write_rap,build_state,write_state
from .targets import build_targets,write_targets
from .inputs import INPUT_FIELDS,import_input,templates,build_bench,write_bench
from .brief import build_brief,write_brief
from .geography import LAYERS,import_geography,build_geography,write_geography
from .geography import import_remoteness,build_remoteness,write_remoteness
from .changes import build_changes,write_changes
from .readiness import readiness
from .crm import build_crm,export_crm


def main(argv=None):
    parser=argparse.ArgumentParser(description='Isolated federal intelligence staging; no outreach or collector writes')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('migrate')
    commands.add_parser('status')
    commands.add_parser('freshness-report', help='Informational publisher checks and imported editions')
    research_refresh=commands.add_parser('research-refresh', help='Refresh publishers and private inboxes; never sends')
    research_refresh.add_argument('--data-root',required=True)
    research_refresh.add_argument('--source',action='append')
    research_refresh.add_argument('--force',action='store_true')
    commands.add_parser('inbox-status',help='Private manual inbox file counts').add_argument('--data-root',required=True)
    quality=commands.add_parser('quality')
    quality.add_argument('--as-of',required=True,type=date.fromisoformat)
    queue=commands.add_parser('review-queue')
    queue.add_argument('--output',help='Save a read-only HTML/JSON review packet')
    procurement=commands.add_parser('import-procurement')
    procurement.add_argument('--database',required=True)
    report=commands.add_parser('buyer-report')
    report.add_argument('--as-of',required=True,type=date.fromisoformat)
    report.add_argument('--output',required=True)
    report.add_argument('--scoring-config')
    panels=commands.add_parser('panel-worklist')
    panels.add_argument('--as-of',required=True,type=date.fromisoformat)
    panels.add_argument('--output',required=True)
    review=commands.add_parser('review')
    review.add_argument('--buyer-key',required=True)
    review.add_argument('--decision',required=True,choices=['approved','rejected'])
    review.add_argument('--organisation-id')
    review.add_argument('--reviewer',required=True)
    review.add_argument('--evidence-url',required=True)
    review.add_argument('--notes',default='')
    ingest=commands.add_parser('import')
    ingest.add_argument('--manifest',required=True)
    ingest.add_argument('--source',choices=['agor','government_directory'],required=True)
    bridge=commands.add_parser('bridge-buyers')
    bridge.add_argument('--contracts-db',required=True)
    register=commands.add_parser('import-register',help='ABN/ASIC (procurement ABNs, or --national), ACNC or ORIC')
    register.add_argument('--manifest',required=True)
    register.add_argument('--source',choices=REGISTER_SOURCES,required=True)
    register.add_argument('--national',action='store_true',
                          help='ABN/ASIC/business names: every active record plus every procurement ABN (streamed)')
    roles=commands.add_parser('import-directory-roles',help='Senior role holders of an imported Government Directory file')
    roles.add_argument('--manifest',required=True)
    checks=commands.add_parser('supplier-checks',help='Contract supplier ABNs against the national registers')
    checks.add_argument('--as-of',required=True,type=date.fromisoformat)
    checks.add_argument('--output',required=True)
    for name,text_help in [('buyict-report','BuyICT buyer universe (phase 2a)'),('levers-report','Indigenous procurement levers (phase 2d)')]:
        sub=commands.add_parser(name,help=text_help)
        sub.add_argument('--as-of',required=True,type=date.fromisoformat)
        sub.add_argument('--output',required=True)
    seed=commands.add_parser('watchlist-seed',help='Add candidate panels from data; never overwrites what people entered')
    seed.add_argument('--as-of',required=True,type=date.fromisoformat)
    commands.add_parser('watchlist-export',help='CSV for marking relevance and Illoura status').add_argument('--output',required=True)
    mark=commands.add_parser('watchlist-mark',help='Apply a reviewed watchlist CSV (relevance, status, notes)')
    mark.add_argument('--csv',required=True)
    mark.add_argument('--by',required=True)
    commands.add_parser('profile-show')
    profile=commands.add_parser('profile-set')
    profile.add_argument('--key',required=True)
    profile.add_argument('--value',required=True)
    profile.add_argument('--evidence-url')
    profile.add_argument('--by',required=True)
    commands.add_parser('category-summary')
    commands.add_parser('import-aps',help='APS agency headcount (APSC Employment Release Tables)').add_argument('--manifest',required=True)
    aps=commands.add_parser('aps-report',help='APS agency size, ICT workforce and our-category spend')
    aps.add_argument('--as-of',required=True,type=date.fromisoformat)
    aps.add_argument('--output',required=True)
    demand=commands.add_parser('import-demand',help='Manifest-verified JSA vacancy, shortage or projection workbook')
    demand.add_argument('--manifest',required=True)
    demand.add_argument('--source',choices=DEMAND_SOURCES,required=True)
    demand_report=commands.add_parser('demand-report')
    demand_report.add_argument('--as-of',required=True,type=date.fromisoformat)
    demand_report.add_argument('--output',required=True)
    panel_evidence=commands.add_parser('panel-verify',help='Record checked panel facts with document hash and named reviewer')
    panel_evidence.add_argument('--json',required=True)
    panel_evidence.add_argument('--by',required=True)
    access=commands.add_parser('panel-access')
    access.add_argument('--as-of',required=True,type=date.fromisoformat)
    access.add_argument('--output',required=True)
    forward_import=commands.add_parser('import-forward',help='Contact-free read-only Tender Feed export')
    forward_import.add_argument('--database',required=True)
    forward=commands.add_parser('forward-report')
    forward.add_argument('--as-of',required=True,type=date.fromisoformat)
    forward.add_argument('--output',required=True)
    research_import=commands.add_parser('import-research',help='Strict contact-free retained RAP or state research bundle')
    research_import.add_argument('--json',required=True)
    research_import.add_argument('--rap-collection',help='Required retained responses for automated RAP directory clearance')
    rap_acquire=commands.add_parser('acquire-rap',help='Official directory metadata only; retains private HTML')
    rap_acquire.add_argument('--output',required=True)
    rap_review=commands.add_parser('review-rap',help='Directory-only service account review and independent verifier')
    rap_review.add_argument('--collection',required=True)
    rap_review.add_argument('--output',required=True)
    rap_review.add_argument('--by',required=True)
    rap_verify=commands.add_parser('verify-rap',help='Replay directory evidence, identity checks and exact payload digest')
    rap_verify.add_argument('--collection',required=True)
    rap_verify.add_argument('--json',required=True)
    for name in ['rap-report','state-report','targets-report']:
        sub=commands.add_parser(name)
        sub.add_argument('--as-of',required=True,type=date.fromisoformat)
        sub.add_argument('--output',required=True)
    business=commands.add_parser('import-input',help='Strict contact-free bench, mapping, CRM, invitation or bid CSV')
    business.add_argument('--kind',required=True,choices=INPUT_FIELDS)
    business.add_argument('--csv',required=True)
    business.add_argument('--source-url',required=True)
    business.add_argument('--as-of',required=True,type=date.fromisoformat)
    business.add_argument('--by',required=True)
    commands.add_parser('input-templates').add_argument('--output',required=True)
    bench=commands.add_parser('bench-report')
    bench.add_argument('--as-of',required=True,type=date.fromisoformat)
    bench.add_argument('--output',required=True)
    digest=commands.add_parser('market-digest',help='Internal evidence-linked research package; never sends')
    digest.add_argument('--as-of',required=True,type=date.fromisoformat)
    digest.add_argument('--output',required=True)
    geo=commands.add_parser('import-geography')
    geo.add_argument('--manifest',required=True)
    geo.add_argument('--layer',choices=LAYERS,required=True)
    geo_report=commands.add_parser('geography-report')
    geo_report.add_argument('--as-of',required=True,type=date.fromisoformat)
    geo_report.add_argument('--output',required=True)
    ready=commands.add_parser('readiness')
    ready.add_argument('--as-of',required=True,type=date.fromisoformat)
    changes=commands.add_parser('changes-report')
    changes.add_argument('--since',required=True,type=date.fromisoformat)
    changes.add_argument('--as-of',required=True,type=date.fromisoformat)
    changes.add_argument('--output',required=True)
    crm=commands.add_parser('crm-export',help='Contact-free per-lane account export with CRM relationship reconciliation')
    crm.add_argument('--as-of',required=True,type=date.fromisoformat)
    crm.add_argument('--output',required=True)
    crm.add_argument('--limit',type=int,default=20)
    commands.add_parser('import-remoteness',help='Verified official ABS inputs; conservative derived RA references').add_argument('--manifest',required=True)
    remoteness=commands.add_parser('remoteness-report')
    remoteness.add_argument('--as-of',required=True,type=date.fromisoformat)
    remoteness.add_argument('--output',required=True)
    health=commands.add_parser('acquire-health',help='Server-side official quarterly Star Ratings acquisition')
    health.add_argument('--output-root',required=True)
    commands.add_parser('import-aged-care',help='Verified quarterly Star Ratings manifest').add_argument('--manifest',required=True)
    aged=commands.add_parser('aged-care-report',help='Published quality evidence and care-minutes facts')
    aged.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='inbox-status':
            from .research_refresh import inbox_status
            print(json.dumps(inbox_status(args.data_root),indent=2))
            return 0
        if args.command=='acquire-health':
            from .acquire_health import main as acquire_health
            return acquire_health(['--output-root',args.output_root])
        if args.command=='acquire-rap':
            from .acquire_rap import acquire
            print(json.dumps(acquire(args.output),indent=2))
            return 0
        if args.command=='input-templates':
            print(json.dumps(templates(args.output),indent=2))
            return 0
        db=engine()
        if args.command=='freshness-report':
            from .freshness import report
            result=report(db)
        elif args.command=='research-refresh':
            from .research_refresh import refresh
            result=refresh(db,args.data_root,args.source,force=args.force)
        elif args.command=='review-rap':
            from .rap_review import review as review_directory
            result=review_directory(db,args.collection,args.output,args.by)
        elif args.command=='verify-rap':
            from .rap_review import verify
            result=verify(db,args.collection,args.json)
        elif args.command=='migrate':
            migrate(db)
            result={'schema':'024_national_registers','status':'ready'}
        elif args.command=='import-aged-care':
            from .aged_care import import_aged_care
            result=import_aged_care(db,args.manifest)
        elif args.command=='aged-care-report':
            from .aged_care import build_aged_care_report,write_aged_care_report
            result=write_aged_care_report(build_aged_care_report(db),args.output)
        elif args.command=='import':
            result=import_snapshot(db,args.manifest,args.source)
        elif args.command=='import-register':
            if args.national:
                from .national_registers import import_national
                result=import_national(db,args.manifest,args.source)
            else:
                result=import_register(db,args.manifest,args.source)
        elif args.command=='import-directory-roles':
            from .directory_roles import import_directory_roles
            result=import_directory_roles(db,args.manifest)
        elif args.command=='supplier-checks':
            result=write_checks(build_checks(db,args.as_of),args.output)
        elif args.command=='buyict-report':
            result=write_buyict(build_buyict(db,args.as_of),args.output)
        elif args.command=='levers-report':
            result=write_levers(build_levers(db,args.as_of),args.output)
        elif args.command=='watchlist-seed':
            result=seed_watchlist(db,args.as_of)
        elif args.command=='watchlist-export':
            result=export_watchlist(db,args.output)
        elif args.command=='watchlist-mark':
            result=mark_watchlist(db,args.csv,args.by)
        elif args.command=='profile-show':
            result=show_profile(db)
        elif args.command=='profile-set':
            result=set_profile(db,args.key,args.value,args.evidence_url,args.by)
        elif args.command=='category-summary':
            result=category_summary(db)
        elif args.command=='import-aps':
            result=import_aps(db,args.manifest)
        elif args.command=='import-demand':
            result=import_demand(db,args.manifest,args.source)
        elif args.command=='demand-report':
            result=write_demand(build_demand(db,args.as_of),args.output)
        elif args.command=='panel-verify':
            result=record_panel_evidence(db,args.json,args.by)
        elif args.command=='panel-access':
            result=write_panel_access(build_panel_access(db,args.as_of),args.output)
        elif args.command=='import-forward':
            result=import_forward(db,args.database)
        elif args.command=='forward-report':
            result=write_forward(build_forward(db,args.as_of),args.output)
        elif args.command=='import-research':
            result=import_research(db,args.json,args.rap_collection)
        elif args.command=='rap-report':
            result=write_rap(build_rap(db,args.as_of),args.output)
        elif args.command=='state-report':
            result=write_state(build_state(db,args.as_of),args.output)
        elif args.command=='targets-report':
            result=write_targets(build_targets(db,args.as_of),args.output)
        elif args.command=='import-input':
            result=import_input(db,args.kind,args.csv,args.source_url,args.as_of,args.by)
        elif args.command=='bench-report':
            result=write_bench(build_bench(db,args.as_of),args.output)
        elif args.command=='market-digest':
            result=write_brief(build_brief(db,args.as_of),args.output)
        elif args.command=='import-remoteness':
            result=import_remoteness(db,args.manifest)
        elif args.command=='remoteness-report':
            result=write_remoteness(build_remoteness(db,args.as_of),args.output)
        elif args.command=='import-geography':
            result=import_geography(db,args.manifest,args.layer)
        elif args.command=='geography-report':
            result=write_geography(build_geography(db,args.as_of),args.output)
        elif args.command=='readiness':
            result=readiness(db,args.as_of)
        elif args.command=='changes-report':
            result=write_changes(build_changes(db,args.since,args.as_of),args.output)
        elif args.command=='crm-export':
            result=export_crm(build_crm(db,args.as_of,args.limit),args.output)
        elif args.command=='aps-report':
            result=write_aps_report(build_aps_report(db,args.as_of),args.output)
        elif args.command=='bridge-buyers':
            result=bridge_buyers(db,args.contracts_db)
        elif args.command=='review':
            result=decide(db,args.buyer_key,args.decision,args.organisation_id,args.reviewer,args.evidence_url,args.notes)
        elif args.command=='import-procurement':
            result=import_procurement(db,args.database)
        elif args.command=='buyer-report':
            result=write_report(build_report(db,args.as_of,args.scoring_config),args.output)
        elif args.command=='panel-worklist':
            result=write_worklist(worklist(build_report(db,args.as_of)),args.output)
        elif args.command=='quality':
            result=quality_report(db,args.as_of)
        elif args.command=='review-queue':
            result=read_queue(db)
            if args.output:
                result=write_queue(result,args.output)
        else:
            with db.connect() as conn:
                result={'sources':[dict(r) for r in conn.execute(text('''SELECT s.source,s.effective_date,s.effective_date_basis,
                    s.retrieved_at,s.row_count,s.sha256 FROM market.current_snapshots c JOIN market.snapshots s ON s.id=c.snapshot_id''')).mappings()],
                    'organisation_count':conn.execute(text('SELECT count(*) FROM market.organisations')).scalar(),
                    'proposal_counts':[dict(r) for r in conn.execute(text('SELECT match_status,count(*) FROM market.current_buyer_matches GROUP BY match_status')).mappings()]}
        print(json.dumps(result,default=str,indent=2))
        return 1 if isinstance(result,dict) and result.get('status')=='blocked' else 0
    except Exception as exc:
        # Connection errors may include credentials; expose only the error class.
        print(json.dumps({'status':'failed','error_code':type(exc).__name__}),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())

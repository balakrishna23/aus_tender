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


def main(argv=None):
    parser=argparse.ArgumentParser(description='Isolated federal intelligence staging; no outreach or collector writes')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('migrate')
    commands.add_parser('status')
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
    register=commands.add_parser('import-register',help='ABN/ASIC (scoped to procurement ABNs), ACNC or ORIC')
    register.add_argument('--manifest',required=True)
    register.add_argument('--source',choices=REGISTER_SOURCES,required=True)
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
    args=parser.parse_args(argv)
    try:
        db=engine()
        if args.command=='migrate':
            migrate(db)
            result={'schema':'010_watchlist_profile','status':'ready'}
        elif args.command=='import':
            result=import_snapshot(db,args.manifest,args.source)
        elif args.command=='import-register':
            result=import_register(db,args.manifest,args.source)
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
        return 0
    except Exception as exc:
        # Connection errors may include credentials; expose only the error class.
        print(json.dumps({'status':'failed','error_code':type(exc).__name__}),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())

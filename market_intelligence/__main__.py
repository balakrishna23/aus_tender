import argparse
import json
import sys
from datetime import date
from sqlalchemy import text
from .db import engine, migrate
from .ingest import import_snapshot
from .matching import bridge_buyers
from .review import decide
from .procurement import import_procurement
from .analytics import build_report,write_report
from .quality import report as quality_report


def main(argv=None):
    parser=argparse.ArgumentParser(description='Isolated federal intelligence staging; no outreach or collector writes')
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('migrate')
    commands.add_parser('status')
    quality=commands.add_parser('quality')
    quality.add_argument('--as-of',required=True,type=date.fromisoformat)
    commands.add_parser('review-queue')
    procurement=commands.add_parser('import-procurement')
    procurement.add_argument('--database',required=True)
    report=commands.add_parser('buyer-report')
    report.add_argument('--as-of',required=True,type=date.fromisoformat)
    report.add_argument('--output',required=True)
    report.add_argument('--scoring-config')
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
    args=parser.parse_args(argv)
    try:
        db=engine()
        if args.command=='migrate':
            migrate(db)
            result={'schema':'008_website_privacy','status':'ready'}
        elif args.command=='import':
            result=import_snapshot(db,args.manifest,args.source)
        elif args.command=='bridge-buyers':
            result=bridge_buyers(db,args.contracts_db)
        elif args.command=='review':
            result=decide(db,args.buyer_key,args.decision,args.organisation_id,args.reviewer,args.evidence_url,args.notes)
        elif args.command=='import-procurement':
            result=import_procurement(db,args.database)
        elif args.command=='buyer-report':
            result=write_report(build_report(db,args.as_of,args.scoring_config),args.output)
        elif args.command=='quality':
            result=quality_report(db,args.as_of)
        elif args.command=='review-queue':
            with db.connect() as conn:
                result={'rows':[dict(r) for r in conn.execute(text("""SELECT buyer_key,buyer_name,match_status,review_status,
                    register_context_stale,candidate_evidence,evidence FROM market.buyer_resolution
                    WHERE review_status IN ('unreviewed','needs_recheck') ORDER BY buyer_name""")).mappings()]}
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

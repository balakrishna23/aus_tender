"""Read-only identity review packets; rendering never records a decision."""
import html
import json
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import text


def read_queue(engine):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        rows = [dict(r) for r in conn.execute(text('''
            SELECT run_id,buyer_key,buyer_name,buyer_abn,match_status,organisation_id,
                   review_status,confirmed_organisation_id,register_context_stale,
                   candidate_ids,candidate_evidence,evidence,snapshots
            FROM market.buyer_resolution
            WHERE review_status IN ('unreviewed','needs_recheck')
            ORDER BY buyer_name,buyer_key''')).mappings()]
        sources = [dict(r) for r in conn.execute(text('''
            SELECT s.id,s.source,s.source_url,s.effective_date,s.effective_date_basis,
                   s.retrieved_at,s.sha256,s.parser_revision
            FROM market.current_snapshots c JOIN market.snapshots s ON s.id=c.snapshot_id
            ORDER BY s.source''')).mappings()]
        run = conn.execute(text('''SELECT r.id,r.source_sha256,r.source_metadata,r.snapshots,
                r.finished_at FROM market.buyer_match_runs r
                JOIN market.current_buyer_match_run c ON r.id=c.run_id''')).mappings().first()
    return dict(status='draft_for_human_review',rows=rows,register_sources=sources,
                proposal_generation=dict(run) if run else None,
                limitations=[
                    'Machine proposals are not human confirmations; shared ABNs do not merge government bodies.',
                    'Source disagreements require evidence review; do not silently replace a published ABN.',
                    'A saved packet is a snapshot. Recheck the live queue before recording a decision.'])


def _source_link(url):
    try:
        parts=urlsplit(url or '')
        safe=parts.scheme=='https' and parts.hostname and not parts.username and not parts.password
    except ValueError:
        safe=False
    return ('<a href="'+html.escape(url,quote=True)+'">Register source</a>') if safe else 'Source URL unavailable or requires review'


def write_queue(queue, directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    packet=directory/'identity_review.json'
    page=directory/'identity_review.html'
    packet.write_text(json.dumps(queue,default=str,indent=2),encoding='utf-8')
    order={'conflict':0,'ambiguous':1,'unmatched':2,'exact_name':3,'exact_abn_name':4,'exact_abn':5}
    rows=sorted(queue['rows'],key=lambda r:(r['review_status']!='needs_recheck',
                not r['register_context_stale'],order.get(r['match_status'],6),r['buyer_name'].casefold(),r['buyer_key']))
    cards=[]
    for row in rows:
        candidates=[]
        for candidate in row['candidate_evidence']:
            candidates.append('<li><strong>'+html.escape(candidate['name'])+'</strong><br>'
                              +html.escape(candidate['organisation_id'])+' · ABN '
                              +html.escape(candidate['abn'] or 'Unknown')+' · '
                              +html.escape(candidate['source'])+'<br>'
                              +_source_link(candidate['source_url'])+'<br>Snapshot '
                              +html.escape(str(candidate['snapshot_id']))+'</li>')
        warning='Register snapshots changed; rebuild proposals before reviewing.' if row['register_context_stale'] else ''
        if row['review_status']=='needs_recheck':
            warning+=' Previously reviewed identity evidence changed; human recheck required.'
        cards.append('<article><h2>'+html.escape(row['buyer_name'])+'</h2><dl><dt>Published buyer ABN</dt><dd>'
                     +html.escape(row['buyer_abn'] or 'Unknown')+'</dd><dt>Proposal and review state</dt><dd>'
                     +html.escape(row['match_status'].replace('_',' ')+' / '+row['review_status'].replace('_',' '))
                     +'</dd><dt>Proposal evidence</dt><dd>'+html.escape(row['evidence'].replace('_',' '))
                     +'</dd><dt>Proposed organisation</dt><dd>'+html.escape(row['organisation_id'] or 'Unresolved')
                     +'</dd><dt>Buyer review key</dt><dd><code>'+html.escape(row['buyer_key'])+'</code></dd></dl><p>'
                     +html.escape(warning)+'</p><h3>Candidate register observations</h3><ul>'
                     +(''.join(candidates) or '<li>No supported register candidate. Research the current official record.</li>')
                     +'</ul></article>')
    page.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<title>Buyer identity review queue</title><style>body{font:16px/1.5 system-ui;max-width:1050px;margin:32px auto;'
                    'padding:0 20px;color:#163144;background:#f4f6f8;overflow-wrap:anywhere}article{background:white;padding:20px;'
                    'margin:18px 0;border:1px solid #d9e0e5;border-radius:10px}dt{font-weight:600}dd{margin:0 0 12px}'
                    'li{margin:12px 0}a{color:#075c86}</style><h1>Buyer identity review queue</h1><p>'
                    +str(len(rows))+' identities require review. Changed register context and conflicts appear first.</p><p>'
                    +html.escape(' '.join(queue['limitations']))+'</p>'
                    +(''.join(cards) or '<p>No pending identities in this snapshot.</p>')
                    +'<p>Full source hashes, proposal generation and register observations: identity_review.json.</p></html>',encoding='utf-8')
    return {'identities':len(rows),'json':str(packet),'html':str(page)}

"""Incrementally index local descriptions and reviewed findings; no source writes."""
import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import assistant
from hybrid import INDEX,MODEL,chunks,key,model_digest,embed,pack
from public_research import findings


def build(index=INDEX, batch_size=32):
    before=assistant.read_rows('intelligence.db','SELECT built_at FROM build_info LIMIT 1')[0]['built_at']
    renewals=assistant.read_rows('intelligence.db','SELECT description FROM renewals')
    opportunities=assistant.read_rows('intelligence.db',"SELECT title FROM opportunities WHERE is_relevant=1 AND (lower(status_effective)='planned' OR (lower(status_effective)='open' AND julianday(closes_at)>julianday('now')))")
    research=findings([])
    texts=[r['description'] for r in renewals]+[r['title'] for r in opportunities]+[
        r.get('title','')+'. '+r.get('finding','') for r in research]
    corpus={key(part):part for text in texts for part in chunks(text)}
    digest=model_digest()
    index.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(index)) as connection:
        connection.executescript('CREATE TABLE IF NOT EXISTS embeddings(hash TEXT PRIMARY KEY,text TEXT NOT NULL,vector BLOB NOT NULL); CREATE TABLE IF NOT EXISTS metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL);')
        metadata=dict(connection.execute('SELECT name,value FROM metadata'))
        if metadata and (metadata.get('model')!=MODEL or metadata.get('model_digest')!=digest):
            raise ValueError('Model changed: choose a new ILLOURA_HYBRID_INDEX path; existing cache preserved')
        connection.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',[('model',MODEL),('model_digest',digest)])
        connection.commit()
        cached={r[0] for r in connection.execute('SELECT hash FROM embeddings')}
        pending=[(hashed,text) for hashed,text in corpus.items() if hashed not in cached]
        for offset in range(0,len(pending),batch_size):
            batch=pending[offset:offset+batch_size]
            vectors=embed([text for _,text in batch],timeout=60)
            connection.executemany('INSERT OR REPLACE INTO embeddings VALUES (?,?,?)',
                [(hashed,text,pack(vector)) for (hashed,text),vector in zip(batch,vectors)])
            connection.commit()
            print(f'Embedded {min(offset+batch_size,len(pending))}/{len(pending)} new chunks',flush=True)
        after=assistant.read_rows('intelligence.db','SELECT built_at FROM build_info LIMIT 1')[0]['built_at']
        if before!=after:raise ValueError('Source build changed; vectors retained, rerun to complete the current build')
        manifest={'embedding_model':MODEL,'model_digest':digest,'source_build':after,
            'completed_at_utc':datetime.now(timezone.utc).isoformat(),'renewal_records':len(renewals),
            'current_opportunity_records':len(opportunities),'reviewed_research_records':len(research),
            'current_unique_chunks':len(corpus),'new_chunks':len(pending),
            'scope':'Renewal view (up to one year), relevant open/planned opportunity titles, reviewed research findings. Not full contract history or full RAP PDFs.'}
        connection.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',[(name,str(value)) for name,value in manifest.items()])
        connection.commit()
    reports=Path(__file__).parent/'reports';reports.mkdir(exist_ok=True)
    (reports/'hybrid-index.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest),flush=True)
    return manifest


if __name__=='__main__':
    if (Path(__file__).parent/'LOCAL_AI_DISABLED').exists():
        print(json.dumps({'status':'skipped','reason':'Local AI disabled by user'}))
        raise SystemExit(0)
    parser=argparse.ArgumentParser();parser.add_argument('--batch-size',type=int,default=32)
    args=parser.parse_args()
    if not 1<=args.batch_size<=128:parser.error('batch size must be between 1 and 128')
    build(batch_size=args.batch_size)

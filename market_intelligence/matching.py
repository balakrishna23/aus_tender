"""Evidence-preserving buyer proposals; fuzzy names never become identity links."""
from collections import defaultdict
from contextlib import closing
from difflib import SequenceMatcher
import hashlib
import json
import sqlite3
from pathlib import Path
import uuid

from sqlalchemy import text

from .parsers import name_key, valid_abn, SourceError
from .ingest import file_hash


def propose(name, abn, organisations):
    candidates = list(organisations)
    try:
        number = valid_abn(abn)
    except SourceError:
        return dict(match_status='conflict',organisation_id=None,candidate_ids=[],evidence='invalid_source_abn')
    by_number = {o['organisation_id'] for o in candidates if number and o['abn']==number}
    by_name = {o['organisation_id'] for o in candidates if o['name_key']==name_key(name)}
    identity_candidates = (by_number&by_name) or (by_number|by_name)
    conflicted = {o['organisation_id'] for o in candidates if o.get('register_conflict') and o['organisation_id'] in identity_candidates}
    if conflicted:
        return dict(match_status='conflict',organisation_id=None,candidate_ids=sorted(by_number|by_name),evidence='register_sources_disagree; review_current_identity')
    if number and by_name and not by_number:
        known_name_abns = {o['abn'] for o in candidates if o['organisation_id'] in by_name and o['abn']}
        disagrees = bool(known_name_abns and number not in known_name_abns)
        return dict(match_status='conflict' if disagrees else 'ambiguous',organisation_id=None,candidate_ids=sorted(by_name),
                    evidence='name_abn_disagree' if disagrees else 'valid_abn_not_in_register; name_candidate_requires_review')
    if number and by_name and by_name.isdisjoint(by_number):
        return dict(match_status='conflict',organisation_id=None,candidate_ids=sorted(by_number|by_name),evidence='name_abn_disagree')
    if len(by_number)==1:
        return dict(match_status='exact_abn',organisation_id=next(iter(by_number)),candidate_ids=sorted(by_number),evidence='valid_abn_unique_in_current_register')
    if len(by_number)>1:
        intersection = by_number & by_name
        if len(intersection)==1 and len(by_name)==1:
            return dict(match_status='exact_abn_name',organisation_id=next(iter(intersection)),candidate_ids=sorted(by_number),
                        evidence='shared_abn_disambiguated_by_unique_exact_official_name; bodies_not_merged')
        return dict(match_status='ambiguous',organisation_id=None,candidate_ids=sorted(by_number),evidence='shared_abn; government_bodies_not_merged')
    if len(by_name)==1 and not number:
        return dict(match_status='exact_name',organisation_id=next(iter(by_name)),candidate_ids=sorted(by_name),evidence='unique_normalised_name; proposal_not_human_confirmation')
    if by_name:
        return dict(match_status='ambiguous',organisation_id=None,candidate_ids=sorted(by_name),evidence='name_not_unique')
    similar = {o['organisation_id'] for o in candidates if SequenceMatcher(None,name_key(name),o['name_key']).ratio()>=0.88}
    return dict(match_status='ambiguous' if similar else 'unmatched',organisation_id=None,
                candidate_ids=sorted(similar),evidence='fuzzy_candidates_review_only' if similar else 'no_supported_match')


def bridge_buyers(engine, database):
    path = Path(database).resolve(strict=True)
    digest = file_hash(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source:
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        buyers = source.execute("SELECT DISTINCT buyer,buyer_abn FROM contracts WHERE buyer IS NOT NULL AND trim(buyer)<>''").fetchall()
        has_metadata = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='export_metadata'").fetchone()
        metadata = json.loads(source.execute('SELECT payload FROM export_metadata').fetchone()[0]) if has_metadata else {'source_file':path.name}
    if file_hash(path) != digest:
        raise ValueError('Source changed while reading buyer snapshot')
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.buyer_match_runs(id,status,source_sha256,source_metadata,snapshots) VALUES(:id,'running',:digest,CAST(:metadata AS jsonb),'[]')"),
                     dict(id=run_id,digest=digest,metadata=json.dumps(metadata)))
    try:
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
            conn.execute(text('SELECT pg_advisory_xact_lock(191126002)'))
            organisations = [dict(r) for r in conn.execute(text("SELECT o.*,f.organisation_id IS NOT NULL AS register_conflict FROM market.organisations o LEFT JOIN market.register_identity_conflicts f USING(organisation_id) WHERE o.kind='body' AND NOT o.issues @> '[\"publisher_placeholder\"]'::jsonb")).mappings()]
            if not organisations:
                raise ValueError('Import government registers before building buyer proposals')
            if not buyers:
                raise ValueError('Empty buyer snapshot; preserving previous generation')
            snapshots = sorted(str(r[0]) for r in conn.execute(text('SELECT snapshot_id FROM market.current_snapshots')))
            evidence_observations=[dict(r) for r in conn.execute(text("SELECT * FROM market.organisation_observations WHERE kind='body'")).mappings()]
            totals = defaultdict(int)
            for name, abn in buyers:
                abn = None if abn is None else str(abn)
                result = propose(name, abn, organisations)
                key = hashlib.sha256((name+'\0'+(abn or '')).encode()).hexdigest()
                candidate_evidence = [{k:str(o[k]) if k=='snapshot_id' else o[k] for k in
                    ['organisation_id','name','abn','snapshot_id','source','source_url']} for o in evidence_observations
                    if o['organisation_id'] in result['candidate_ids']]
                conn.execute(text('''INSERT INTO market.buyer_match_proposals(run_id,buyer_key,buyer_name,buyer_abn,match_status,
                    organisation_id,candidate_ids,evidence,snapshots,candidate_evidence) VALUES(:run,:key,:name,:abn,:match_status,:organisation_id,
                    CAST(:candidates AS jsonb),:evidence,CAST(:snapshots AS jsonb),CAST(:candidate_evidence AS jsonb))'''),
                    dict(run=run_id,key=key,name=name,abn=abn,**{k:v for k,v in result.items() if k!='candidate_ids'},
                         candidates=json.dumps(result['candidate_ids']),snapshots=json.dumps(snapshots),candidate_evidence=json.dumps(candidate_evidence)))
                totals[result['match_status']] += 1
            conn.execute(text("UPDATE market.buyer_match_runs SET status='complete',finished_at=now(),snapshots=CAST(:snapshots AS jsonb),metrics=CAST(:metrics AS jsonb) WHERE id=:id"),
                         dict(id=run_id,snapshots=json.dumps(snapshots),metrics=json.dumps(dict(totals))))
            conn.execute(text('''INSERT INTO market.current_buyer_match_run(run_id) VALUES(:id)
                ON CONFLICT(singleton) DO UPDATE SET run_id=EXCLUDED.run_id'''),dict(id=run_id))
        return {'run_id':str(run_id),'source_buyer_pairs':len(buyers),'matches':dict(totals),'source_sha256':digest,
                'mode':'proposals_only; human_decisions_preserved'}
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.buyer_match_runs SET status='failed',finished_at=now(),error_code=:error WHERE id=:id"),dict(id=run_id,error=type(exc).__name__))
        raise

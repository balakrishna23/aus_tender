"""Explicit human approve/reject actions, with immutable decision history."""
import json
from urllib.parse import urlsplit
import uuid

from sqlalchemy import text


def decide(engine, buyer_key, decision, organisation_id, reviewer, evidence_url, notes=''):
    if decision not in ['approved','rejected']:
        raise ValueError('Decision must be approved or rejected')
    if (decision=='approved') != bool(organisation_id):
        raise ValueError('Only approved decisions require an organisation ID')
    url=urlsplit(evidence_url)
    if url.scheme!='https' or not url.hostname or url.username or url.password:
        raise ValueError('Evidence must be an HTTPS URL without credentials')
    if not reviewer.strip():
        raise ValueError('Reviewer identifier is required')
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':'market-review:'+buyer_key})
        proposal=conn.execute(text('SELECT * FROM market.current_buyer_matches WHERE buyer_key=:key'),{'key':buyer_key}).mappings().first()
        if not proposal:
            raise ValueError('Buyer is not in the current proposal generation')
        fingerprint=None
        if organisation_id:
            organisation=conn.execute(text("SELECT organisation_id,name_key,abn FROM market.organisations WHERE organisation_id=:id AND kind='body' AND NOT issues @> '[\"publisher_placeholder\"]'::jsonb"),{'id':organisation_id}).mappings().first()
            if not organisation:
                raise ValueError('Approved organisation must be a current real body')
            fingerprint=conn.execute(text('SELECT fingerprint FROM market.register_identity_fingerprints WHERE organisation_id=:id'),{'id':organisation_id}).scalar()
        previous=conn.execute(text('SELECT * FROM market.review_decisions WHERE buyer_key=:key'),{'key':buyer_key}).mappings().first()
        snapshots=sorted(str(r[0]) for r in conn.execute(text('SELECT snapshot_id FROM market.current_snapshots')))
        values=dict(key=buyer_key,decision=decision,organisation_id=organisation_id,reviewer=reviewer,
                    evidence_url=evidence_url,notes=notes,fingerprint=fingerprint,snapshots=json.dumps(snapshots))
        conn.execute(text('''INSERT INTO market.review_events(id,buyer_key,decision,organisation_id,reviewer,evidence_url,notes,
            previous_decision,identity_fingerprint,reviewed_snapshots) VALUES(:id,:key,:decision,:organisation_id,:reviewer,
            :evidence_url,:notes,CAST(:previous AS jsonb),:fingerprint,CAST(:snapshots AS jsonb))'''),
            dict(values,id=uuid.uuid4(),previous=json.dumps(dict(previous),default=str) if previous else None))
        conn.execute(text('''INSERT INTO market.review_decisions(buyer_key,decision,organisation_id,reviewer,evidence_url,notes,
            identity_fingerprint,reviewed_snapshots) VALUES(:key,:decision,:organisation_id,:reviewer,:evidence_url,:notes,
            :fingerprint,CAST(:snapshots AS jsonb)) ON CONFLICT(buyer_key) DO UPDATE SET decision=EXCLUDED.decision,
            organisation_id=EXCLUDED.organisation_id,reviewer=EXCLUDED.reviewer,evidence_url=EXCLUDED.evidence_url,
            notes=EXCLUDED.notes,identity_fingerprint=EXCLUDED.identity_fingerprint,reviewed_snapshots=EXCLUDED.reviewed_snapshots,reviewed_at=now()'''),values)
    return {'buyer_key':buyer_key,'decision':decision,'organisation_id':organisation_id,'audit_event_written':True}

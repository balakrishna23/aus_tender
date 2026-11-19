"""Keep conflicting register claims visible and quarantine legacy amendment rows."""
from alembic import op

revision='005_evidence_quality'
down_revision='004_procurement_facts'


def upgrade():
    op.execute('''
CREATE VIEW market.register_identity_conflicts AS
 SELECT organisation_id,count(DISTINCT name_key)>1 AS name_conflict,
 count(DISTINCT abn)>1 AS abn_conflict
 FROM market.organisation_observations WHERE kind='body' GROUP BY organisation_id
 HAVING count(DISTINCT name_key)>1 OR count(DISTINCT abn)>1;
CREATE VIEW market.current_analytical_contracts AS SELECT * FROM market.current_contracts
 WHERE NOT issues @> '["legacy_amendment_identity_requires_review"]'::jsonb;
CREATE VIEW market.directory_edges AS
 SELECT 'gov:'||child.publisher_id AS child_id,child.name AS child_name,child.kind AS child_kind,
 'gov:'||parent.publisher_id AS parent_id,parent.name AS parent_name,parent.kind AS parent_kind,
 COALESCE(NULLIF(child.payload->>'parent_directory_structure',''),NULLIF(child.payload->>'parent_organisation','')) AS parent_content_id,
 child.snapshot_id,child.payload->>'content_id' AS content_id
 FROM market.observations child JOIN market.current_snapshots s ON s.snapshot_id=child.snapshot_id AND s.source='government_directory'
 LEFT JOIN market.observations parent ON parent.snapshot_id=child.snapshot_id
 AND parent.payload->>'content_id'=COALESCE(NULLIF(child.payload->>'parent_directory_structure',''),NULLIF(child.payload->>'parent_organisation',''));
CREATE VIEW market.agency_unit_map AS
 WITH RECURSIVE paths AS (
 SELECT child_id AS unit_id,child_name AS unit_name,parent_id AS ancestor_id,parent_kind,
 ARRAY[child_id,parent_id]::text[] AS visited,1 AS depth,snapshot_id
 FROM market.directory_edges WHERE child_kind='unit' AND parent_id IS NOT NULL
 UNION ALL
 SELECT p.unit_id,p.unit_name,e.parent_id,e.parent_kind,p.visited||e.parent_id,p.depth+1,p.snapshot_id
 FROM paths p JOIN market.directory_edges e ON e.child_id=p.ancestor_id
 WHERE p.parent_kind='unit' AND p.depth<30 AND e.parent_id IS NOT NULL AND NOT e.parent_id=ANY(p.visited)
 ) SELECT DISTINCT ON(unit_id) unit_id,unit_name,ancestor_id AS agency_id,depth,snapshot_id
 FROM paths WHERE parent_kind='body' ORDER BY unit_id,depth;
''')


def downgrade():
    raise RuntimeError('Use a forward migration or a validated backup')

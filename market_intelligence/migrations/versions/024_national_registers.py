"""National business registers and Government Directory role holders.

1. Registers may now hold the whole national active register (scope_kind 'national_active': every active
   ABN / ASIC company / business name plus every procurement ABN whatever its status). A `legal_key`
   column (name key without a leading 'the' or trailing legal-form words) and name-key indexes let matching
   look names up instead of reading the register whole. Views that read the register are rewritten so they
   only touch the rows they use.
2. Senior role holders published by the Government Directory (name, role title, unit) for the ICT, digital,
   people/workforce and procurement functions and agency heads, attached to an existing Directory snapshot.
   Contact details (phone, email, addresses) are never stored.
"""
import re
import unicodedata

from alembic import op
from sqlalchemy import text

revision = '024_national_registers'
down_revision = '023_research_freshness'

# Frozen copy of parsers.legal_key at the time of this migration (migrations must not change with later code).
_SUFFIXES = frozenset('pty ltd limited inc incorporated co company corp corporation plc llc llp'.split())


def _legal_key(value):
    tokens = re.findall(r'\w+', unicodedata.normalize('NFKC', value or '').casefold(), flags=re.UNICODE)
    if tokens[:1] == ['the']:
        tokens.pop(0)
    while tokens and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return ' '.join(tokens)


def upgrade():
    op.execute('''
ALTER TABLE market.register_snapshots DROP CONSTRAINT register_snapshots_scope_kind_check;
ALTER TABLE market.register_snapshots ADD CONSTRAINT register_snapshots_scope_kind_check
 CHECK(scope_kind IN ('complete','procurement_abns','national_active'));
ALTER TABLE market.register_entities ADD COLUMN legal_key text;
ALTER TABLE market.register_names ADD COLUMN legal_key text;
''')
    bind = op.get_bind()
    names = [r[0] for r in bind.execute(text('''SELECT name FROM market.register_entities WHERE name IS NOT NULL
        UNION SELECT name FROM market.register_names'''))]
    bind.execute(text('CREATE TEMP TABLE legal_key_backfill(name text PRIMARY KEY, legal_key text NOT NULL) ON COMMIT DROP'))
    for i in range(0, len(names), 5000):
        bind.execute(text('INSERT INTO legal_key_backfill(name,legal_key) VALUES(:name,:legal_key)'),
                     [dict(name=n, legal_key=_legal_key(n)) for n in names[i:i + 5000]])
    op.execute('''
UPDATE market.register_entities e SET legal_key=k.legal_key FROM legal_key_backfill k WHERE e.name=k.name;
UPDATE market.register_names n SET legal_key=k.legal_key FROM legal_key_backfill k WHERE n.name=k.name;
CREATE INDEX register_entities_name_key ON market.register_entities(name_key);
CREATE INDEX register_entities_legal_key ON market.register_entities(legal_key);
CREATE INDEX register_names_name_key ON market.register_names(name_key);
CREATE INDEX register_names_legal_key ON market.register_names(legal_key);
CREATE INDEX register_entities_snapshot_abn ON market.register_entities(snapshot_id, abn);

-- e.* now also carries legal_key (appended), so the column list only grows.
CREATE OR REPLACE VIEW market.current_register_entities AS
 SELECT s.source, s.effective_date, s.scope_kind, e.*
 FROM market.register_entities e JOIN market.current_register_snapshots c ON c.snapshot_id=e.snapshot_id
 JOIN market.register_snapshots s ON s.id=e.snapshot_id;

-- Same columns as migration 018; register rows are looked up only for the profiled suppliers.
CREATE OR REPLACE VIEW market.v_supplier_profile AS
 WITH totals AS (SELECT supplier_abn,count(*) AS contracts,count(DISTINCT buyer_key) AS agencies_served,
 sum(original_value) AS original_awarded_value,array_agg(DISTINCT payload->>'category_tag') AS categories
 FROM market.current_analytical_contracts WHERE supplier_abn IS NOT NULL GROUP BY supplier_abn),
 entity AS (SELECT DISTINCT ON(abn) abn,name,registered_on,is_individual,source
 FROM market.current_register_entities WHERE abn IN (SELECT supplier_abn FROM totals)
 ORDER BY abn,(source='abn') DESC,(source='asic_companies') DESC,record_id)
 SELECT t.*,CASE WHEN NOT e.is_individual THEN e.name END AS organisation,
 EXISTS(SELECT 1 FROM market.current_register_entities r WHERE r.abn=t.supplier_abn AND r.source='oric') AS has_oric_record,
 NULL::boolean AS is_small,'private'::text AS lane,'Research supplier and partner fit; turnover and readiness unknown'::text AS why_now,
 t.categories AS target_roles FROM totals t LEFT JOIN entity e ON e.abn=t.supplier_abn;

-- Same rows as migration 022: equal names always have equal name keys, so one indexed key join suffices.
CREATE OR REPLACE VIEW market.v_aged_care_provider_proposals AS
 SELECT o.snapshot_id,o.row_identity,o.provider_name,r.source AS register_source,
 r.snapshot_id AS register_snapshot_id,r.record_id,r.abn,r.name AS register_name,
 CASE WHEN o.provider_name=r.name THEN 'exact_name' ELSE 'normalised_name' END AS match_basis,
 'proposal_only; human verification required'::text AS review_status,
 count(*) OVER(PARTITION BY o.snapshot_id,o.row_identity) AS candidate_records
 FROM market.aged_care_observations o JOIN market.current_aged_care_snapshot c ON c.snapshot_id=o.snapshot_id
 JOIN market.current_register_entities r ON NOT r.is_individual AND o.provider_name_key=r.name_key;

CREATE TABLE market.directory_role_imports (
 snapshot_id uuid NOT NULL REFERENCES market.snapshots(id),
 parser_revision text NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT now(),
 row_count integer NOT NULL CHECK(row_count>=0),
 metrics jsonb NOT NULL DEFAULT '{}',
 PRIMARY KEY(snapshot_id,parser_revision)
);
-- Only what the Directory publishes about a senior role: the role, its unit and the holder's name.
-- Phone numbers, emails and addresses are never stored.
CREATE TABLE market.directory_roles (
 snapshot_id uuid NOT NULL, parser_revision text NOT NULL,
 role_record_id text NOT NULL CHECK(role_record_id ~ '^[A-Z]-[0-9]+$'),
 role_type text NOT NULL CHECK(role_type IN ('directory_role','single_executive_role')),
 role_title text NOT NULL,
 functions text[] NOT NULL CHECK(cardinality(functions)>0
   AND functions <@ ARRAY['ict','digital','workforce','procurement','agency_head']::text[]),
 belongs_to_publisher_id text CHECK(belongs_to_publisher_id IS NULL OR belongs_to_publisher_id ~ '^[A-Z]-[0-9]+$'),
 holder_name text CHECK(holder_name IS NULL OR (holder_name !~ '@' AND holder_name !~ '[0-9]{3}')),
 is_acting boolean, importance integer, publisher_updated text,
 PRIMARY KEY(snapshot_id,parser_revision,role_record_id),
 FOREIGN KEY(snapshot_id,parser_revision) REFERENCES market.directory_role_imports(snapshot_id,parser_revision)
);

-- Spec v_agency_org_map: agency x unit (ICT, workforce, procurement, digital) x publicly listed role holder.
CREATE VIEW market.v_agency_org_map AS
 WITH current_import AS (
  SELECT DISTINCT ON (i.snapshot_id) i.snapshot_id,i.parser_revision
  FROM market.directory_role_imports i JOIN market.current_snapshots c
    ON c.snapshot_id=i.snapshot_id AND c.source='government_directory'
  ORDER BY i.snapshot_id,i.imported_at DESC)
 SELECT COALESCE(m.agency_id, CASE WHEN unit.kind='body' THEN 'gov:'||unit.publisher_id END) AS agency_id,
  agency.name AS agency,
  'gov:'||r.belongs_to_publisher_id AS unit_id, unit.name AS unit, unit.kind AS unit_kind,
  f.function, r.role_title, r.holder_name, r.is_acting, r.importance, r.role_type, r.role_record_id,
  'federal'::text AS lane,
  'Publicly listed role holder for this function; confirm the current holder before any approach'::text AS why_now,
  r.role_title AS target_roles,
  s.source_url, s.licence, s.retrieved_at AS last_checked, r.publisher_updated, r.snapshot_id
 FROM market.directory_roles r
 JOIN current_import ci ON ci.snapshot_id=r.snapshot_id AND ci.parser_revision=r.parser_revision
 JOIN market.snapshots s ON s.id=r.snapshot_id
 CROSS JOIN LATERAL unnest(r.functions) AS f(function)
 LEFT JOIN market.observations unit ON unit.snapshot_id=r.snapshot_id AND unit.publisher_id=r.belongs_to_publisher_id
 LEFT JOIN market.agency_unit_map m ON m.unit_id='gov:'||r.belongs_to_publisher_id
 LEFT JOIN market.organisations agency
   ON agency.organisation_id=COALESCE(m.agency_id, CASE WHEN unit.kind='body' THEN 'gov:'||unit.publisher_id END);
''')


def downgrade():
    raise RuntimeError('Register and Directory snapshot history must be retained; use a forward migration')

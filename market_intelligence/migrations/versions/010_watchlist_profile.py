"""ASIC business names, category index, panel watchlist with an audit trail, and Illoura's own profile."""
from alembic import op

revision = '010_watchlist_profile'
down_revision = '009_business_registers'


def upgrade():
    op.execute('''
ALTER TABLE market.register_snapshots DROP CONSTRAINT register_snapshots_source_check;
ALTER TABLE market.register_snapshots ADD CONSTRAINT register_snapshots_source_check
 CHECK(source IN ('abn','asic_companies','asic_business_names','acnc','oric'));
CREATE INDEX procurement_category_tag ON market.procurement_contracts(snapshot_id,(payload->>'category_tag'));
CREATE INDEX procurement_panel_son ON market.procurement_panel_links(snapshot_id,son_id);

-- Spec phase 2c: a maintained table, not an ingest. Fields people fill stay NULL until checked.
CREATE TABLE market.panel_watchlist (
 son_id text PRIMARY KEY, panel_name text NOT NULL, jurisdiction text NOT NULL DEFAULT 'Commonwealth',
 owner_agency text, portal text, categories jsonb NOT NULL DEFAULT '[]',
 start_date date, current_expiry date, extension_options text, max_possible_expiry date,
 open_for_new_suppliers text CHECK(open_for_new_suppliers IS NULL OR open_for_new_suppliers IN ('always','scheduled_refresh','closed')),
 next_refresh_or_intake_date date, refresh_evidence_url text, how_to_join text, eligibility_requirements text,
 illoura_status text NOT NULL DEFAULT 'unknown' CHECK(illoura_status IN ('unknown','member','applied','not_applied','not_eligible')),
 marked_relevant boolean, spend_through_panel_12m numeric, contracts_36m integer, notes text NOT NULL DEFAULT '',
 source_url text, last_verified_at timestamptz, needs_manual_check boolean NOT NULL DEFAULT true,
 seeded_from text NOT NULL, seeded_at timestamptz NOT NULL DEFAULT now(), updated_by text, updated_at timestamptz
);
CREATE TABLE market.watchlist_events (
 id bigserial PRIMARY KEY, son_id text NOT NULL REFERENCES market.panel_watchlist(son_id),
 field text NOT NULL, old_value text, new_value text, changed_by text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
);

-- Spec: illoura_profile. Values are entered by people with evidence; nothing is assumed.
CREATE TABLE market.illoura_profile (
 key text PRIMARY KEY, value text, evidence_url text, updated_by text, updated_at timestamptz, note text NOT NULL DEFAULT ''
);
CREATE TABLE market.profile_events (
 id bigserial PRIMARY KEY, key text NOT NULL REFERENCES market.illoura_profile(key),
 old_value text, new_value text, evidence_url text, changed_by text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO market.illoura_profile(key,note) VALUES
 ('abn','Illoura Services ABN'),
 ('supply_nation_status','Certified, or certification in progress: project records and the 19 Nov spec disagree'),
 ('indigenous_ownership_percent','IPP eligibility from 1 July 2026 needs 51 per cent or more'),
 ('oric_registered','yes or no; ORIC registration also meets IPP eligibility'),
 ('labour_hire_licences','States and territories where licensed'),
 ('insurances','Public liability, professional indemnity, workers compensation'),
 ('disp_status','Defence Industry Security Program membership level or pending'),
 ('security_clearances_held','Counts by level: Baseline, NV1, NV2'),
 ('panels_joined','Standing offer IDs of panels Illoura is on'),
 ('panels_applied','Standing offer IDs applied for'),
 ('categories_sold','ict_labour, ict_services, recruitment, labour_hire_general, healthcare_staffing, nursing_agency');
''')


def downgrade():
    raise RuntimeError('Watchlist and profile history must be retained; restore a validated backup')

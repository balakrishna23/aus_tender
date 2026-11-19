"""Source references can repeat across buyers; retain source observations separately."""
from alembic import op
revision='016_forward_identity'
down_revision='015_retained_research'


def upgrade():
    op.execute('''
ALTER TABLE market.forward_observations ADD COLUMN observation_key text;
UPDATE market.forward_observations SET observation_key=md5(source||'|'||source_id||'|'||COALESCE(buyer,'')||'|'||COALESCE(source_url,''));
ALTER TABLE market.forward_observations ALTER COLUMN observation_key SET NOT NULL;
ALTER TABLE market.forward_observations DROP CONSTRAINT forward_observations_pkey;
ALTER TABLE market.forward_observations ADD PRIMARY KEY(snapshot_id,observation_key);
-- CREATE OR REPLACE cannot append a column in the middle of an existing view.
DROP VIEW market.current_forward;
CREATE VIEW market.current_forward AS SELECT o.*,s.exported_at,s.metadata FROM market.forward_observations o
 JOIN market.current_forward_snapshot c ON c.snapshot_id=o.snapshot_id JOIN market.forward_snapshots s ON s.id=o.snapshot_id;
''')


def downgrade():raise RuntimeError('Forward source identity history must be preserved')

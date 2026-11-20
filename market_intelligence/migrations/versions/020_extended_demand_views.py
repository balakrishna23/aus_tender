"""Expose REOS and TNV without merging their measurement bases."""
from alembic import op
revision='020_extended_demand'
down_revision='019_rap_extraction'


def upgrade():
    op.execute('''CREATE OR REPLACE VIEW market.v_demand_signals AS
 SELECT * FROM (SELECT d.*,row_number() OVER(PARTITION BY source,classification,occupation_code,occupation_title,region,measure ORDER BY period DESC) AS recency
 FROM market.current_demand d WHERE d.source IN ('jsa_ivi','jsa_shortages','jsa_reos','jsa_tnv')) d WHERE recency=1''')


def downgrade():raise RuntimeError('Use a forward view change')

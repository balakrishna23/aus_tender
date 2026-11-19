"""Bound hierarchy lookups and date-window research queries."""
from alembic import op

revision='007_query_indexes'
down_revision='006_review_identity_evidence'


def upgrade():
    op.execute("CREATE INDEX observations_content_id ON market.observations(snapshot_id,(payload->>'content_id'))")
    op.execute('CREATE INDEX procurement_category_publication ON market.procurement_contracts(snapshot_id,category_family,first_published)')


def downgrade():
    op.execute('DROP INDEX market.procurement_category_publication')
    op.execute('DROP INDEX market.observations_content_id')

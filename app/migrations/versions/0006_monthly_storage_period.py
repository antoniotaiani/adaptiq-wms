"""Stoccaggio mensile: mese di riferimento degli addebiti di fine mese

billing_charges.period (YYYY-MM) è valorizzato solo per gli addebiti dichiarati a fine
mese (source_type MONTHLY): un solo addebito per mandante, servizio e mese.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("billing_charges", sa.Column("period", sa.String(length=7), nullable=True))
    op.create_index(
        "uq_billing_charges_monthly", "billing_charges", ["merchant_id", "service_id", "period"],
        unique=True, postgresql_where=sa.text("period IS NOT NULL"),
    )


def downgrade():
    op.drop_index("uq_billing_charges_monthly", table_name="billing_charges")
    op.drop_column("billing_charges", "period")

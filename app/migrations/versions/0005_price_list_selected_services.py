"""Listini a voci scelte: anche i servizi a consuntivo fanno parte del listino

Prima i servizi a consuntivo erano imputabili a tutti i mandanti senza stare nei listini;
ora un servizio è imputabile solo se è una voce del listino del mandante. Le voci a
consuntivo hanno unit_price NULL. Per non cambiare il comportamento dei listini esistenti,
i servizi a consuntivo vi vengono aggiunti come voci.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("price_list_lines", "unit_price", existing_type=sa.Numeric(12, 4), nullable=True)
    op.execute(
        "INSERT INTO price_list_lines (price_list_id, service_id, unit_price) "
        "SELECT pl.id, s.id, NULL FROM price_lists pl CROSS JOIN billing_services s "
        "WHERE s.basis = 'MANUAL_IMPORTO' AND NOT EXISTS ("
        "SELECT 1 FROM price_list_lines l WHERE l.price_list_id = pl.id AND l.service_id = s.id)"
    )


def downgrade():
    op.execute("DELETE FROM price_list_lines WHERE unit_price IS NULL")
    op.alter_column("price_list_lines", "unit_price", existing_type=sa.Numeric(12, 4), nullable=False)

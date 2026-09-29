"""Un documento può essere evaso una sola volta per mandante

Se in produzione esistessero già duplicati (stesso mandante, stesso numero ignorando
maiuscole/minuscole) la creazione dell'indice fallisce e l'app non parte: vanno
rinominati a mano prima del deploy. Query di verifica:
  SELECT merchant_id, lower(order_number), count(*) FROM dispatch_orders
  GROUP BY 1, 2 HAVING count(*) > 1;

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "uq_dispatch_orders_merchant_order",
        "dispatch_orders",
        ["merchant_id", sa.text("lower(order_number)")],
        unique=True,
    )


def downgrade():
    op.drop_index("uq_dispatch_orders_merchant_order", table_name="dispatch_orders")

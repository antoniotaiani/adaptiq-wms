"""Schema di partenza, identico a quello creato finora da Base.metadata.create_all

I database già esistenti (locale e produzione) vengono solo marcati a questa revisione
da app/db_migrations.py, senza eseguire upgrade(): le tabelle ci sono già.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "merchants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_code", sa.String(50), nullable=False),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("pin_hash", sa.String(255), nullable=False),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
    )
    op.create_index("ix_merchants_id", "merchants", ["id"])
    op.create_index("ix_merchants_account_code", "merchants", ["account_code"], unique=True)

    op.create_table(
        "items",
        sa.Column("sku", sa.String(80), primary_key=True),
        sa.Column("barcode", sa.String(80), nullable=False),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("bin_location", sa.String(50), nullable=False),
        sa.Column("on_hand_qty", sa.Integer(), nullable=False),
        sa.UniqueConstraint("merchant_id", "barcode", name="uq_items_merchant_barcode"),
    )
    op.create_index("ix_items_sku", "items", ["sku"])
    op.create_index("ix_items_barcode", "items", ["barcode"])
    op.create_index("ix_items_bin_location", "items", ["bin_location"])

    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.String(50), nullable=False),
        sa.Column("sku", sa.String(80), sa.ForeignKey("items.sku"), nullable=False),
        sa.Column("transaction_type", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("doc_reference", sa.String(100), nullable=True),
    )
    op.create_index("ix_inventory_transactions_id", "inventory_transactions", ["id"])

    op.create_table(
        "dispatch_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_number", sa.String(100), nullable=False),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("processed_at", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=False),
    )
    op.create_index("ix_dispatch_orders_id", "dispatch_orders", ["id"])
    op.create_index("ix_dispatch_orders_order_number", "dispatch_orders", ["order_number"])

    op.create_table(
        "dispatch_order_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("dispatch_orders.id"), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("barcode", sa.String(80), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("bin_location", sa.String(50), nullable=False),
        sa.Column("expected_qty", sa.Integer(), nullable=False),
        sa.Column("picked_qty", sa.Integer(), nullable=False),
    )
    op.create_index("ix_dispatch_order_lines_id", "dispatch_order_lines", ["id"])

    op.create_table(
        "smtp_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("smtp_host", sa.String(150), nullable=False),
        sa.Column("smtp_port", sa.Integer(), nullable=False),
        sa.Column("smtp_user", sa.String(150), nullable=False),
        sa.Column("smtp_password", sa.String(255), nullable=False),
        sa.Column("sender_email", sa.String(150), nullable=False),
        sa.Column("sender_name", sa.String(150), nullable=False),
        sa.Column("use_tls", sa.Boolean(), nullable=False),
        sa.Column("portal_base_url", sa.String(200), nullable=False),
    )
    op.create_index("ix_smtp_settings_id", "smtp_settings", ["id"])

    op.create_table(
        "operator_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(50), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
    )
    op.create_index("ix_operator_users_id", "operator_users", ["id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("timestamp", sa.String(50), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("details", sa.String(500), nullable=True),
    )
    op.create_index("ix_audit_log_id", "audit_log", ["id"])


def downgrade():
    for table in ("audit_log", "operator_users", "smtp_settings", "dispatch_order_lines",
                  "dispatch_orders", "inventory_transactions", "items", "merchants"):
        op.drop_table(table)

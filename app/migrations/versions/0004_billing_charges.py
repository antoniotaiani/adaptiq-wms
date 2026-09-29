"""Scheda costi: testate dei carichi merce e addebiti ai mandanti

I carichi registrati prima di questa migrazione restano senza testata
(inventory_transactions.inbound_receipt_id NULL): non hanno costi associabili.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('inbound_receipts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('merchant_id', sa.Integer(), nullable=False),
    sa.Column('doc_reference', sa.String(length=100), nullable=False),
    sa.Column('received_at', sa.String(length=50), nullable=False),
    sa.Column('total_units', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_inbound_receipts_merchant_id'), 'inbound_receipts', ['merchant_id'], unique=False)
    op.create_table('billing_charges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('merchant_id', sa.Integer(), nullable=False),
    sa.Column('charge_date', sa.String(length=50), nullable=False),
    sa.Column('source_type', sa.String(length=20), nullable=False),
    sa.Column('dispatch_order_id', sa.Integer(), nullable=True),
    sa.Column('inbound_receipt_id', sa.Integer(), nullable=True),
    sa.Column('service_id', sa.Integer(), nullable=False),
    sa.Column('phase_id', sa.Integer(), nullable=False),
    sa.Column('service_code', sa.String(length=40), nullable=False),
    sa.Column('service_name', sa.String(length=150), nullable=False),
    sa.Column('phase_name', sa.String(length=100), nullable=False),
    sa.Column('unit_label', sa.String(length=40), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('unit_price', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('notes', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.String(length=50), nullable=False),
    sa.Column('created_by', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['dispatch_order_id'], ['dispatch_orders.id'], ),
    sa.ForeignKeyConstraint(['inbound_receipt_id'], ['inbound_receipts.id'], ),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchants.id'], ),
    sa.ForeignKeyConstraint(['phase_id'], ['billing_phases.id'], ),
    sa.ForeignKeyConstraint(['service_id'], ['billing_services.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_billing_charges_charge_date'), 'billing_charges', ['charge_date'], unique=False)
    op.create_index(op.f('ix_billing_charges_dispatch_order_id'), 'billing_charges', ['dispatch_order_id'], unique=False)
    op.create_index(op.f('ix_billing_charges_inbound_receipt_id'), 'billing_charges', ['inbound_receipt_id'], unique=False)
    op.create_index(op.f('ix_billing_charges_merchant_id'), 'billing_charges', ['merchant_id'], unique=False)
    op.add_column('inventory_transactions', sa.Column('inbound_receipt_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_inventory_transactions_inbound_receipt_id'), 'inventory_transactions', ['inbound_receipt_id'], unique=False)
    op.create_foreign_key('inventory_transactions_inbound_receipt_id_fkey', 'inventory_transactions', 'inbound_receipts', ['inbound_receipt_id'], ['id'])


def downgrade():
    op.drop_constraint('inventory_transactions_inbound_receipt_id_fkey', 'inventory_transactions', type_='foreignkey')
    op.drop_index(op.f('ix_inventory_transactions_inbound_receipt_id'), table_name='inventory_transactions')
    op.drop_column('inventory_transactions', 'inbound_receipt_id')
    op.drop_index(op.f('ix_billing_charges_merchant_id'), table_name='billing_charges')
    op.drop_index(op.f('ix_billing_charges_inbound_receipt_id'), table_name='billing_charges')
    op.drop_index(op.f('ix_billing_charges_dispatch_order_id'), table_name='billing_charges')
    op.drop_index(op.f('ix_billing_charges_charge_date'), table_name='billing_charges')
    op.drop_table('billing_charges')
    op.drop_index(op.f('ix_inbound_receipts_merchant_id'), table_name='inbound_receipts')
    op.drop_table('inbound_receipts')

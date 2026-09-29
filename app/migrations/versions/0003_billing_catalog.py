"""Modulo costi: fasi, servizi addebitabili, listini e listino associato al mandante

Precarica fasi e servizi del contratto quadro di micrologistica (Allegato A) e un
"Listino Standard" con le relative tariffe, non associato ad alcun mandante.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PHASES = [
    ("INGRESSI", "Ingressi & Ricezione", 10),
    ("STOCCAGGIO", "Stoccaggio", 20),
    ("PUTAWAY", "Controllo analitico & Put-away", 30),
    ("PICKING", "Preparazione ordine (Picking)", 40),
    ("PACKING", "Packing", 50),
    ("AMMINISTRATIVA", "Gestione amministrativa", 60),
    ("MATERIALI", "Materiali d'imballo", 70),
    ("EXTRA", "Servizi accessori extra", 80),
]

# (codice, nome, fase, base, unità, descrizione, prezzo nel Listino Standard o None)
SERVICES = [
    ("ING-PALLET", "Scarico merce a pallet", "INGRESSI", "INBOUND_PALLET", "pallet",
     "Scarico, spunta quantitativa su DDT, controllo visivo colli", 10.00),
    ("ING-COLLO", "Scarico collo sciolto", "INGRESSI", "INBOUND_COLLO", "collo",
     "Scarico, spunta quantitativa su DDT, controllo visivo colli", 2.00),
    ("STO-POSTO-PALLET", "Stoccaggio a posto pallet", "STOCCAGGIO", "STORAGE_POSTO_PALLET", "posto pallet/mese",
     "Stoccaggio a scaffale e custodia in magazzino coperto e protetto", 8.00),
    ("STO-MQ", "Stoccaggio a m² frontale dedicato", "STOCCAGGIO", "STORAGE_MQ", "m²/mese",
     "Superficie frontale dedicata [L x H] al mese", 15.00),
    ("PUT-PEZZO", "Controllo analitico e messa a scaffale", "PUTAWAY", "INBOUND_PEZZO", "pezzo",
     "Apertura cartoni, spunta analitica a pezzo, verifica SKU/EAN, collocazione a scaffale", 1.00),
    ("PICK-COLLO", "Picking a collo", "PICKING", "OUTBOUND_COLLO", "collo",
     "Presa in carico ordine", 2.00),
    ("PICK-PALLET", "Picking a pallet", "PICKING", "OUTBOUND_PALLET", "pallet",
     "Presa in carico ordine", 10.00),
    ("PICK-PEZZO", "Picking articolo singolo", "PICKING", "OUTBOUND_PEZZO", "pezzo",
     "Presa in carico ordine", 0.50),
    ("PACK-REF-EXTRA", "Prelievo referenza aggiuntiva", "PACKING", "OUTBOUND_RIGA_EXTRA", "referenza",
     "Prelievo di ogni referenza aggiuntiva oltre la prima", None),
    ("ADM-LDV", "Elaborazione LDV e tracking", "AMMINISTRATIVA", "OUTBOUND_ORDINE", "ordine",
     "Elaborazione LDV su vettori Cliente, trasmissione flussi e tracking", None),
    ("MAT-IMBALLO", "Materiali d'imballo", "MATERIALI", "MANUAL_IMPORTO", "a consuntivo",
     "Forniti dal Cliente o fatturati a parte in base alla tipologia impiegata", None),
    ("EXTRA-ORA", "Servizi accessori (ora operatore)", "EXTRA", "MANUAL_ORA", "ora",
     "Etichettatura personalizzata, imballaggio particolare, inventario straordinario, resi / re-entry", 25.00),
]

STANDARD_LIST = "Listino Standard"


def upgrade():
    op.create_table(
        "billing_phases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
    )
    op.create_table(
        "billing_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("phase_id", sa.Integer(), sa.ForeignKey("billing_phases.id"), nullable=False),
        sa.Column("basis", sa.String(40), nullable=False),
        sa.Column("unit_label", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "price_lists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False, unique=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "price_list_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("price_list_id", sa.Integer(), sa.ForeignKey("price_lists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("service_id", sa.Integer(), sa.ForeignKey("billing_services.id"), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 4), nullable=False),
        sa.UniqueConstraint("price_list_id", "service_id", name="uq_price_list_lines_list_service"),
    )
    op.add_column("merchants", sa.Column("price_list_id", sa.Integer(), nullable=True))
    op.create_foreign_key("merchants_price_list_id_fkey", "merchants", "price_lists", ["price_list_id"], ["id"])

    conn = op.get_bind()
    for code, name, order in PHASES:
        conn.execute(
            sa.text("INSERT INTO billing_phases (code, name, sort_order) VALUES (:c, :n, :o)"),
            {"c": code, "n": name, "o": order},
        )
    for code, name, phase, basis, unit, desc, _price in SERVICES:
        conn.execute(
            sa.text(
                "INSERT INTO billing_services (code, name, description, phase_id, basis, unit_label, active) "
                "SELECT :c, :n, :d, id, :b, :u, true FROM billing_phases WHERE code = :p"
            ),
            {"c": code, "n": name, "d": desc, "b": basis, "u": unit, "p": phase},
        )
    conn.execute(
        sa.text("INSERT INTO price_lists (name, notes, active) VALUES (:n, :notes, true)"),
        {"n": STANDARD_LIST, "notes": "Tariffe del contratto quadro di micrologistica (Allegato A)."},
    )
    for code, *_rest, price in SERVICES:
        if price is None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO price_list_lines (price_list_id, service_id, unit_price) "
                "SELECT l.id, s.id, :p FROM price_lists l, billing_services s WHERE l.name = :l AND s.code = :s"
            ),
            {"p": price, "l": STANDARD_LIST, "s": code},
        )


def downgrade():
    op.drop_constraint("merchants_price_list_id_fkey", "merchants", type_="foreignkey")
    op.drop_column("merchants", "price_list_id")
    op.drop_table("price_list_lines")
    op.drop_table("price_lists")
    op.drop_table("billing_services")
    op.drop_table("billing_phases")

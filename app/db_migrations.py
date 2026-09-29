from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BASELINE_REVISION = "0001"


def upgrade_to_head(sync_conn):
    """Porta lo schema all'ultima migrazione. Da chiamare con AsyncConnection.run_sync().

    I DB creati prima dell'introduzione di Alembic (con create_all) hanno già le tabelle
    della baseline ma non la tabella alembic_version: vanno solo marcati alla baseline,
    poi si applicano le migrazioni successive come su qualunque altro DB.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["connection"] = sync_conn

    tables = inspect(sync_conn).get_table_names()
    if "alembic_version" not in tables and "merchants" in tables:
        command.stamp(cfg, BASELINE_REVISION)

    command.upgrade(cfg, "head")

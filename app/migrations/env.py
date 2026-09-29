import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import DATABASE_URL
from app.database import Base
import app.models  # noqa: F401 - registra i modelli su Base.metadata per l'autogenerate

config = context.config
target_metadata = Base.metadata


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    engine = create_async_engine(DATABASE_URL)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


# Avvio dell'app: la connessione (sincrona, ottenuta con run_sync) arriva da app/db_migrations.py.
# Riga di comando: nessuna connessione passata, ne apriamo una noi.
connection = config.attributes.get("connection")
if connection is not None:
    do_run_migrations(connection)
else:
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)
    asyncio.run(run_async_migrations())

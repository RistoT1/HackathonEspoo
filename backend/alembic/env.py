"""Alembic-migraatiot sovelluksen omille tauluille.

Yhteys tulee Settingsista (.env DATABASE_URL), ellei kutsuja aseta sqlalchemy.url:ia.
Vain oletusskeema: demo- ja dev-skeemat eivat ole sovelluksen hallinnoimia.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import hae_asetukset
from app.tietokanta import Base

config = context.config

# Sovelluksen kaynnistyksessa lokitusta ei konfiguroida uudelleen (uvicornin lokit sailyvat).
if config.config_file_name is not None and config.attributes.get("konfiguroi_lokitus", True):
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", hae_asetukset().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

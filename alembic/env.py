"""Ambiente do Alembic.

Roda em modo **síncrono** com psycopg 3 (``postgresql://``), enquanto a
aplicação usa o mesmo driver em modo async (``postgresql+psycopg://``). Um único
driver para os dois mundos -- diferente do spike, que misturava psycopg2 na app.

A URL vem de ``Settings``; nunca do ``alembic.ini``.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings

# Importa todos os modelos para popular Base.metadata.
from app.models import Base  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

target_metadata = Base.metadata


#: Índices escritos à mão nas migrations. São **índices de expressão** (chamam
#: funções como `imutavel_unaccent`), algo que o SQLAlchemy não consegue declarar
#: de forma que o autogenerate reconheça. Sem este filtro, todo `alembic
#: revision --autogenerate` proporia removê-los.
SUFIXOS_INDICES_MANUAIS = ("_trgm",)
PREFIXOS_INDICES_MANUAIS = ("hnsw_",)


def incluir_objeto(objeto: object, nome: str | None, tipo: str, reflexo: bool, _cmp: object) -> bool:
    """Filtra o que o autogenerate considera.

    Constraints EXCLUDE também são escritas à mão, mas o Alembic já as ignora
    por não ter suporte a `ExcludeConstraint` no autogenerate.
    """
    if tipo == "index" and nome:
        if nome.startswith(PREFIXOS_INDICES_MANUAIS) or nome.endswith(SUFIXOS_INDICES_MANUAIS):
            return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url_sync,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=incluir_objeto,
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
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=incluir_objeto,
            # Migrations em transação: um erro no meio não deixa o schema
            # pela metade.
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Extensões do Postgres e tabela de parâmetros de sistema

Revision ID: 0001
Revises:
Create Date: 2026-08-12

Cria as extensões de que o schema depende antes de qualquer tabela:

* ``vector``      -- embeddings das transcrições (Fase 5)
* ``btree_gist``  -- necessária para as constraints EXCLUDE de agenda, que
                     combinam igualdade (``profissional_id WITH =``) com
                     sobreposição de range (``WITH &&``). Sem ela o Postgres
                     recusa o índice.
* ``pgcrypto``    -- ``gen_random_uuid()`` para as PKs
* ``unaccent`` e ``pg_trgm`` -- busca por nome/especialidade tolerante a acento
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSOES = ("vector", "btree_gist", "pgcrypto", "unaccent", "pg_trgm")


def upgrade() -> None:
    for extensao in EXTENSOES:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extensao}"')

    op.create_table(
        "parametros_sistema",
        sa.Column("chave", sa.String(length=100), nullable=False),
        sa.Column("valor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False, server_default="str"),
        sa.Column("descricao", sa.Text(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atualizado_por_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("chave", name="pk_parametros_sistema"),
    )
    # A FK para `usuarios` é criada na 0002, quando a tabela existir.


def downgrade() -> None:
    op.drop_table("parametros_sistema")
    # As extensões não são removidas: outros schemas do mesmo banco podem
    # depender delas, e removê-las derrubaria índices alheios.

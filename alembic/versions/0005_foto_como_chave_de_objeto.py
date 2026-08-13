"""foto como chave de objeto

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-13 13:04:46.321669+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # RENAME, não drop+add: o autogenerate propôs remover `foto_url` e criar
    # `foto_chave`, o que apagaria a foto de todo profissional já cadastrado.
    # O conteúdo da coluna muda de significado (era URL, agora é chave do
    # objeto), mas em produção o valor antigo seria migrado, não descartado.
    op.alter_column("perfis_profissional", "foto_url", new_column_name="foto_chave")


def downgrade() -> None:
    op.alter_column("perfis_profissional", "foto_chave", new_column_name="foto_url")

"""Mixins comuns aos modelos."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDPk:
    """Chave primária UUID gerada pelo banco.

    UUID em vez de serial porque os IDs aparecem em URLs (``/sessao/{id}``) e um
    inteiro sequencial permitiria enumerar consultas alheias e inferir o volume
    da plataforma.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class Timestamps:
    """``criado_em`` / ``atualizado_em`` mantidos pelo banco.

    ``server_default``/``onupdate`` no servidor garante que a coluna esteja
    correta mesmo em UPDATE feito fora do ORM (migration, script, psql).
    """

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

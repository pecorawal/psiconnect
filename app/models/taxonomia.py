"""Taxonomias: especialidades, sintomas e a ponte entre eles.

O matching do produto vive aqui. O paciente não fala "quero TCC para TAG" — ele
diz "não consigo dormir" e "meu coração dispara". ``Sintoma`` guarda a linguagem
do paciente; ``Especialidade`` guarda a do profissional; ``SintomaEspecialidade``
liga as duas com um peso.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk


class Especialidade(UUIDPk, Timestamps, Base):
    __tablename__ = "especialidades"

    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text)
    categoria: Mapped[str | None] = mapped_column(String(60))
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    ordem_exibicao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")

    def __repr__(self) -> str:
        return f"<Especialidade {self.slug}>"


class Sintoma(UUIDPk, Timestamps, Base):
    __tablename__ = "sintomas"

    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    nome: Mapped[str] = mapped_column(String(120), nullable=False)
    #: Como o paciente descreveria, sem jargão clínico. É o texto exibido na UI.
    descricao_leiga: Mapped[str] = mapped_column(String(255), nullable=False)
    categoria: Mapped[str | None] = mapped_column(String(60))
    #: Dispara o protocolo de risco: alerta ao profissional e destaque do CVV.
    bandeira_risco: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    ordem_exibicao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")

    def __repr__(self) -> str:
        return f"<Sintoma {self.slug}>"


class SintomaEspecialidade(Base):
    """Ponte do matching. ``peso`` 1..5 = quão indicada é a especialidade."""

    __tablename__ = "sintomas_especialidades"

    sintoma_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sintomas.id", ondelete="CASCADE"),
        primary_key=True,
    )
    especialidade_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("especialidades.id", ondelete="CASCADE"),
        primary_key=True,
    )
    peso: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")

    sintoma: Mapped[Sintoma] = relationship(lazy="joined")
    especialidade: Mapped[Especialidade] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("peso BETWEEN 1 AND 5", name="peso_1_5"),
        Index("ix_sintomas_especialidades_especialidade_id", "especialidade_id"),
    )


class PacienteSintoma(Base):
    """O que o paciente relatou sentir.

    **Dado pessoal sensível de saúde (LGPD art. 11).** Leitura restrita ao
    próprio paciente e ao profissional que o atende; nunca ao admin. Toda leitura
    passa por ``AutorizacaoService``.
    """

    __tablename__ = "pacientes_sintomas"

    paciente_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_paciente.usuario_id", ondelete="CASCADE"),
        primary_key=True,
    )
    sintoma_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sintomas.id", ondelete="CASCADE"),
        primary_key=True,
    )
    intensidade: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    registrado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    sintoma: Mapped[Sintoma] = relationship(lazy="joined")

    __table_args__ = (CheckConstraint("intensidade BETWEEN 1 AND 5", name="intensidade_1_5"),)

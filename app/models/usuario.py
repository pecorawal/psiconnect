"""Identidade e acesso."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk
from app.models.enums import OrigemSessaoLogin, Papel, TipoToken

if TYPE_CHECKING:
    from app.models.perfil import PerfilPaciente, PerfilProfissional


class Usuario(UUIDPk, Timestamps, Base):
    __tablename__ = "usuarios"

    # citext (case-insensitive) evita que "Joao@x.com" e "joao@x.com" virem duas
    # contas. A extensão é criada na migration 0003, que também converte a coluna.
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    senha_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    papel: Mapped[Papel] = mapped_column(
        Enum(Papel, name="papel", native_enum=True), nullable=False
    )
    nome_completo: Mapped[str] = mapped_column(String(150), nullable=False)
    telefone_e164: Mapped[str | None] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="America/Sao_Paulo"
    )
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    email_verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telefone_verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_login_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    perfil_profissional: Mapped[PerfilProfissional | None] = relationship(
        back_populates="usuario", uselist=False, lazy="selectin"
    )
    perfil_paciente: Mapped[PerfilPaciente | None] = relationship(
        back_populates="usuario", uselist=False, lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Usuario {self.email} {self.papel}>"

    @property
    def primeiro_nome(self) -> str:
        return self.nome_completo.split()[0] if self.nome_completo else ""


class SessaoLogin(UUIDPk, Base):
    """Sessão de autenticação.

    Nome deliberadamente distinto de ``Sessao`` (o atendimento). São conceitos
    diferentes e confundi-los produziria bugs difíceis de enxergar.

    Guarda-se apenas o SHA-256 do token: um dump do banco não permite se passar
    por ninguém. Ver ADR 0002.
    """

    __tablename__ = "sessoes_login"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    origem: Mapped[OrigemSessaoLogin] = mapped_column(
        Enum(OrigemSessaoLogin, name="origem_sessao_login", native_enum=True),
        nullable=False,
        server_default=OrigemSessaoLogin.WEB.value,
    )
    criada_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ultimo_acesso_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Rastro exigido pela LGPD para sistemas com dado sensível.
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    revogada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    usuario: Mapped[Usuario] = relationship(lazy="joined")

    __table_args__ = (Index("ix_sessoes_login_usuario_id", "usuario_id"),)

    def __repr__(self) -> str:
        return f"<SessaoLogin {self.usuario_id} expira={self.expira_em:%Y-%m-%d}>"


class TokenVerificacao(UUIDPk, Base):
    """Verificação de e-mail e reset de senha. Também guarda só o hash."""

    __tablename__ = "tokens_verificacao"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[TipoToken] = mapped_column(
        Enum(TipoToken, name="tipo_token", native_enum=True), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_tokens_verificacao_usuario_id", "usuario_id"),)

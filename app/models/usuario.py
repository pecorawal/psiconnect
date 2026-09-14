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
    from app.models.autorizacao import Role
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

    #: Permissões administrativas. **Nulo para paciente e profissional**: as
    #: regras deles são de domínio ("só vejo o que é meu"), não CRUD por módulo.
    #: Ver app/models/autorizacao.py.
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("roles.id", ondelete="RESTRICT")
    )

    # --- Reservado para evolução, sem migração disruptiva depois -----------
    twofa_secret_cifrado: Mapped[bytes | None] = mapped_column()
    twofa_habilitado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    openid_sub: Mapped[str | None] = mapped_column(String(255))
    openid_provider: Mapped[str | None] = mapped_column(String(60))
    email_verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telefone_verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultimo_login_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role: Mapped[Role | None] = relationship(back_populates="usuarios", lazy="selectin")
    # passive_deletes: a PK do perfil É a FK para usuarios, e sem isto o ORM
    # tenta "anular" a chave primária ao apagar o usuário, em vez de deixar o
    # ON DELETE CASCADE do banco fazer o trabalho.
    perfil_profissional: Mapped[PerfilProfissional | None] = relationship(
        back_populates="usuario",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    # foreign_keys explícito: PerfilPaciente tem DUAS FKs para usuarios --
    # `usuario_id` (o próprio paciente) e `responsavel_usuario_id` (quem responde
    # por ele, quando é menor). Sem isto o SQLAlchemy não sabe qual usar.
    perfil_paciente: Mapped[PerfilPaciente | None] = relationship(
        back_populates="usuario",
        uselist=False,
        lazy="selectin",
        foreign_keys="PerfilPaciente.usuario_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    #: Menores sob responsabilidade deste usuário (LGPD art. 14).
    dependentes: Mapped[list[PerfilPaciente]] = relationship(
        back_populates="responsavel",
        lazy="selectin",
        foreign_keys="PerfilPaciente.responsavel_usuario_id",
        # SET NULL no banco: apagar o responsável não apaga o dependente.
        passive_deletes=True,
    )

    __table_args__ = (Index("ix_usuarios_role", "role_id"),)

    def __repr__(self) -> str:
        return f"<Usuario {self.email} {self.papel}>"

    @property
    def primeiro_nome(self) -> str:
        return self.nome_completo.split()[0] if self.nome_completo else ""

    def permite(self, modulo: str, operacao: str) -> bool:
        """Se este usuário pode ``operacao`` no ``modulo``.

        Sem papel administrativo, não há permissão de módulo — o que **não**
        impede paciente e profissional de usarem o produto: as rotas deles são
        de domínio e não passam por esta checagem.
        """
        return self.role is not None and self.role.permite(modulo, operacao)


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

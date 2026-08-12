"""Termos, consentimento e auditoria.

O ponto central: o consentimento registrado é do **texto exato** que a pessoa
leu, provado pelo hash SHA-256. "Aceitou os termos" sem saber qual versão é uma
prova frágil — se o texto mudar depois, não há como reconstruir o que foi aceito.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk
from app.models.enums import TipoTermo


class TermoVersionado(UUIDPk, Timestamps, Base):
    __tablename__ = "termos_versionados"

    tipo: Mapped[TipoTermo] = mapped_column(
        Enum(TipoTermo, name="tipo_termo", native_enum=True), nullable=False
    )
    versao: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    conteudo_md: Mapped[str] = mapped_column(Text, nullable=False)
    #: Hash do conteúdo. É o que torna o aceite uma prova, e não uma afirmação.
    hash_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    vigente_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    vigente_ate: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tipo", "versao", name="uq_termo_tipo_versao"),
        Index("ix_termos_versionados_tipo_vigente", "tipo", "vigente_ate"),
        # Índice parcial: no máximo um termo vigente por tipo. Impede que duas
        # versões da política de privacidade fiquem válidas ao mesmo tempo -- o
        # que tornaria ambígua a pergunta "qual texto esta pessoa aceitou?".
        Index(
            "uq_termo_vigente_por_tipo",
            "tipo",
            unique=True,
            postgresql_where=text("vigente_ate IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<TermoVersionado {self.tipo} v{self.versao}>"


class AceiteTermo(UUIDPk, Base):
    """Prova de consentimento.

    ``sessao_id`` é preenchido no disclaimer por sessão (o checkbox de ciência da
    transcrição), que é um consentimento pontual e não global.
    """

    __tablename__ = "aceites_termo"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    termo_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("termos_versionados.id", ondelete="RESTRICT"),
        nullable=False,
    )
    aceito_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    sessao_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sessoes.id", ondelete="SET NULL")
    )
    #: LGPD: consentimento é revogável a qualquer momento.
    revogado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    termo: Mapped[TermoVersionado] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_aceites_termo_usuario", "usuario_id"),
        Index("ix_aceites_termo_sessao", "sessao_id"),
    )


class LogAuditoria(UUIDPk, Base):
    """Trilha append-only.

    Em produção, o role da aplicação deve ter DELETE e UPDATE revogados nesta
    tabela -- caso contrário ela é auditoria só no nome.
    """

    __tablename__ = "logs_auditoria"

    usuario_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    acao: Mapped[str] = mapped_column(String(80), nullable=False)
    entidade: Mapped[str | None] = mapped_column(String(60))
    entidade_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(40))
    dados_antes: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    dados_depois: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_logs_auditoria_usuario_criado", "usuario_id", "criado_em"),
        Index("ix_logs_auditoria_entidade", "entidade", "entidade_id"),
    )

"""Sessão de atendimento e sua trilha de eventos."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk
from app.models.agenda import Agendamento
from app.models.enums import StatusSessao, TipoEventoSessao


class Sessao(UUIDPk, Timestamps, Base):
    """O atendimento em si: a sala de vídeo e seu ciclo de vida.

    Tokens do provedor de vídeo **não são persistidos** -- são emitidos sob
    demanda com TTL curto no momento da entrada. Um token guardado no banco é um
    token que vaza junto com o banco.
    """

    __tablename__ = "sessoes"

    agendamento_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agendamentos.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    provedor_video: Mapped[str] = mapped_column(String(30), nullable=False)
    sala_nome: Mapped[str | None] = mapped_column(String(120))
    sala_url: Mapped[str | None] = mapped_column(String(500))
    sala_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[StatusSessao] = mapped_column(
        Enum(StatusSessao, name="status_sessao", native_enum=True),
        nullable=False,
        server_default=StatusSessao.AGENDADA.value,
    )

    link_enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    profissional_entrou_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paciente_entrou_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paciente_admitido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    iniciada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    encerrada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duracao_real_min: Mapped[int | None] = mapped_column(SmallInteger)

    # As "chaves de acesso aleatórias" do requisito original. Geradas com
    # secrets.token_urlsafe(32), exibidas UMA vez, guardadas só como hash.
    chave_acesso_paciente_hash: Mapped[str | None] = mapped_column(String(64))
    chave_acesso_profissional_hash: Mapped[str | None] = mapped_column(String(64))

    #: Consentimento do paciente para a transcrição, dado no disclaimer.
    transcricao_consentida: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    agendamento: Mapped[Agendamento] = relationship(lazy="joined")
    eventos: Mapped[list[EventoSessao]] = relationship(
        back_populates="sessao",
        cascade="all, delete-orphan",
        order_by="EventoSessao.ocorrido_em",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_sessoes_status", "status"),)

    def __repr__(self) -> str:
        return f"<Sessao {self.agendamento_id} {self.status}>"


class EventoSessao(UUIDPk, Base):
    """Trilha append-only da sessão.

    É a **fonte de verdade** para pontualidade (R10), tolerância de 15 minutos
    (R7) e no-show. Derivar essas regras de campos mutáveis seria frágil e não
    auditável; aqui cada fato tem hora e ator.
    """

    __tablename__ = "eventos_sessao"

    sessao_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sessoes.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[TipoEventoSessao] = mapped_column(
        Enum(TipoEventoSessao, name="tipo_evento_sessao", native_enum=True), nullable=False
    )
    ator_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    ocorrido_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadados: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    sessao: Mapped[Sessao] = relationship(back_populates="eventos")

    __table_args__ = (Index("ix_eventos_sessao_sessao_ocorrido", "sessao_id", "ocorrido_em"),)

    def __repr__(self) -> str:
        return f"<EventoSessao {self.tipo} {self.ocorrido_em:%H:%M:%S}>"

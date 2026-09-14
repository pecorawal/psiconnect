"""Notificações (outbox), avaliação e gamificação."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk
from app.models.enums import CanalNotificacao, StatusNotificacao, TipoPontuacao


class Notificacao(UUIDPk, Timestamps, Base):
    """Outbox de notificações.

    Nada é enviado dentro da transação do caso de uso: se o envio falhasse, o
    agendamento faria rollback junto. O service grava uma linha; o worker envia.

    É também assim que "o link chega 20 minutos antes" (R8) deixa de ser uma
    promessa e vira uma linha com ``agendada_para`` -- testável e reentrante.
    O spike anterior devolvia "Notificações enviadas para WhatsApp e E-mail" sem
    enviar coisa alguma.
    """

    __tablename__ = "notificacoes"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    canal: Mapped[CanalNotificacao] = mapped_column(
        Enum(CanalNotificacao, name="canal_notificacao", native_enum=True), nullable=False
    )
    template: Mapped[str] = mapped_column(String(80), nullable=False)
    destino: Mapped[str] = mapped_column(String(255), nullable=False)
    contexto: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    status: Mapped[StatusNotificacao] = mapped_column(
        Enum(StatusNotificacao, name="status_notificacao", native_enum=True),
        nullable=False,
        server_default=StatusNotificacao.PENDENTE.value,
    )
    agendada_para: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tentativas: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    proxima_tentativa_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provedor_msg_id: Mapped[str | None] = mapped_column(String(120))
    erro: Mapped[str | None] = mapped_column(Text)
    #: Impede reenviar a mesma notificação se o worker rodar duas vezes.
    chave_idempotencia: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)

    __table_args__ = (
        # Índice do worker: pega o que está pendente e já venceu.
        Index("ix_notificacoes_status_agendada", "status", "agendada_para"),
        Index("ix_notificacoes_usuario", "usuario_id"),
    )

    def __repr__(self) -> str:
        return f"<Notificacao {self.canal} {self.template} {self.status}>"


class Avaliacao(UUIDPk, Timestamps, Base):
    """As 3 perguntas obrigatórias do fim da sessão.

    Exatamente as do requisito original: sobre a plataforma, sobre o
    profissional, e sobre o próprio cuidado do paciente.
    """

    __tablename__ = "avaliacoes"

    agendamento_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agendamentos.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    nota_plataforma: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    nota_profissional: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    nota_proprio_cuidado: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comentario_plataforma: Mapped[str | None] = mapped_column(Text)
    comentario_profissional: Mapped[str | None] = mapped_column(Text)
    comentario_proprio_cuidado: Mapped[str | None] = mapped_column(Text)
    respondida_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("nota_plataforma BETWEEN 1 AND 5", name="nota_plataforma_1_5"),
        CheckConstraint("nota_profissional BETWEEN 1 AND 5", name="nota_profissional_1_5"),
        CheckConstraint("nota_proprio_cuidado BETWEEN 1 AND 5", name="nota_cuidado_1_5"),
    )


class EventoPontuacao(UUIDPk, Base):
    """Evento de gamificação.

    ``UNIQUE (usuario_id, tipo, referencia_id)`` torna a pontuação **idempotente
    por construção**: reprocessar uma sessão não pontua duas vezes. O saldo é
    ``SUM(pontos)``, não um contador que pode divergir.
    """

    __tablename__ = "eventos_pontuacao"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    tipo: Mapped[TipoPontuacao] = mapped_column(
        Enum(TipoPontuacao, name="tipo_pontuacao", native_enum=True), nullable=False
    )
    #: Pode ser negativo (no-show, cancelamento tardio).
    pontos: Mapped[int] = mapped_column(Integer, nullable=False)
    referencia_tipo: Mapped[str | None] = mapped_column(String(40))
    referencia_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    descricao: Mapped[str | None] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("usuario_id", "tipo", "referencia_id", name="uq_pontuacao_idempotente"),
        Index("ix_eventos_pontuacao_usuario", "usuario_id"),
    )

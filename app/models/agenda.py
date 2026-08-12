"""Agenda: disponibilidade (regra), bloqueios e agendamento (fato).

Ver ADR 0006. O spike anterior tinha uma única tabela ``agendas`` cumprindo os
dois papéis, o que impedia representar férias e fazia a edição da agenda semanal
mexer em consultas já marcadas.

As constraints EXCLUDE estão declaradas na migration 0002, não aqui: o
``--autogenerate`` do Alembic não as compreende.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
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
from app.models.enums import StatusAgendamento
from app.models.perfil import PerfilPaciente, PerfilProfissional
from app.models.taxonomia import Especialidade

MINUTOS_POR_DIA = 1440


class DisponibilidadeRecorrente(UUIDPk, Timestamps, Base):
    """A **regra**: "toda terça, das 14h às 18h".

    Guarda hora **local de parede**, não UTC. O Brasil aboliu o horário de verão
    em 2019, mas isso é reversível por decreto; com a regra em UTC, o retorno do
    DST deslocaria a agenda de todos em 1 hora. "Terça 14h" precisa continuar
    sendo 14h.

    As janelas são minutos desde a meia-noite (0..1440) em vez de ``TIME`` porque
    só assim é possível usar ``int4range`` na constraint EXCLUDE -- o tipo
    ``TIME`` não tem operador de range no Postgres.
    """

    __tablename__ = "disponibilidades_recorrentes"

    profissional_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_profissional.usuario_id", ondelete="CASCADE"),
        nullable=False,
    )
    #: 0 = segunda .. 6 = domingo (igual a ``date.weekday()``).
    dia_semana: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    inicio_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fim_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    vigencia_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    vigencia_fim: Mapped[date | None] = mapped_column(Date)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    profissional: Mapped[PerfilProfissional] = relationship()

    __table_args__ = (
        CheckConstraint("dia_semana BETWEEN 0 AND 6", name="dia_semana_0_6"),
        CheckConstraint(
            f"inicio_min >= 0 AND fim_min <= {MINUTOS_POR_DIA} AND fim_min > inicio_min",
            name="janela_valida",
        ),
        CheckConstraint(
            "vigencia_fim IS NULL OR vigencia_fim >= vigencia_inicio", name="vigencia_coerente"
        ),
        Index("ix_disponibilidades_profissional_dia", "profissional_id", "dia_semana"),
        # EXCLUDE contra sobreposição: migration 0002.
    )

    def __repr__(self) -> str:
        return f"<Disponibilidade dia={self.dia_semana} {self.inicio}-{self.fim}>"

    @property
    def inicio(self) -> time:
        return time(self.inicio_min // 60, self.inicio_min % 60)

    @property
    def fim(self) -> time:
        # 1440 não é uma hora válida; representamos como 23:59 só para exibição.
        if self.fim_min >= MINUTOS_POR_DIA:
            return time(23, 59)
        return time(self.fim_min // 60, self.fim_min % 60)

    @property
    def duracao_min(self) -> int:
        return self.fim_min - self.inicio_min


class BloqueioAgenda(UUIDPk, Timestamps, Base):
    """Exceção à regra: férias, feriado, "essa quinta não".

    Diferente da disponibilidade, é um intervalo **absoluto** (tem data), e por
    isso guardado em UTC.
    """

    __tablename__ = "bloqueios_agenda"

    profissional_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_profissional.usuario_id", ondelete="CASCADE"),
        nullable=False,
    )
    inicio_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fim_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        CheckConstraint("fim_utc > inicio_utc", name="intervalo_valido"),
        Index("ix_bloqueios_agenda_profissional_inicio", "profissional_id", "inicio_utc"),
    )


class Agendamento(UUIDPk, Timestamps, Base):
    """O **fato**: uma consulta marcada.

    Imutável em essência. Mudar a disponibilidade do profissional não move nem
    apaga agendamentos existentes -- por isso ``disponibilidade_origem_id`` é
    ``ON DELETE SET NULL`` e serve apenas para auditoria e para sugerir
    recorrência.
    """

    __tablename__ = "agendamentos"

    paciente_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_paciente.usuario_id", ondelete="RESTRICT"),
        nullable=False,
    )
    profissional_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_profissional.usuario_id", ondelete="RESTRICT"),
        nullable=False,
    )
    especialidade_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("especialidades.id", ondelete="RESTRICT"), nullable=False
    )

    inicio_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fim_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duracao_min: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    status: Mapped[StatusAgendamento] = mapped_column(
        Enum(StatusAgendamento, name="status_agendamento", native_enum=True),
        nullable=False,
        server_default=StatusAgendamento.PENDENTE_PAGAMENTO.value,
    )

    disponibilidade_origem_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("disponibilidades_recorrentes.id", ondelete="SET NULL"),
    )
    #: Agrupa a série sugerida de "mesmo horário, até 2x por semana" (R3).
    serie_recorrencia_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    valor_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Enquanto PENDENTE_PAGAMENTO, o slot fica travado até este instante.
    reserva_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cancelado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelado_por_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    motivo_cancelamento: Mapped[str | None] = mapped_column(Text)

    paciente: Mapped[PerfilPaciente] = relationship(lazy="joined")
    profissional: Mapped[PerfilProfissional] = relationship(lazy="joined")
    especialidade: Mapped[Especialidade] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("fim_utc > inicio_utc", name="intervalo_valido"),
        CheckConstraint("duracao_min > 0", name="duracao_positiva"),
        CheckConstraint("valor_centavos >= 0", name="valor_nao_negativo"),
        Index("ix_agendamentos_profissional_inicio", "profissional_id", "inicio_utc"),
        Index("ix_agendamentos_paciente_inicio", "paciente_id", "inicio_utc"),
        Index("ix_agendamentos_status_reserva", "status", "reserva_expira_em"),
        # Duas constraints EXCLUDE (profissional e paciente): migration 0002.
    )

    def __repr__(self) -> str:
        return f"<Agendamento {self.inicio_utc:%Y-%m-%d %H:%M} {self.status}>"

    @property
    def ocupa_agenda(self) -> bool:
        from app.models.enums import STATUS_OCUPAM_AGENDA

        return self.status in STATUS_OCUPAM_AGENDA

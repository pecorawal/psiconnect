"""Perfis de profissional e paciente."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps
from app.models.enums import Conselho, StatusCadastro, StatusPaciente
from app.models.taxonomia import Especialidade

if TYPE_CHECKING:
    from app.models.responsavel import VerificacaoResponsavel
    from app.models.usuario import Usuario

LIMITE_ESPECIALIDADES = 5
LIMITE_DESCRICAO = 500


class PerfilProfissional(Timestamps, Base):
    __tablename__ = "perfis_profissional"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    nome_exibicao: Mapped[str] = mapped_column(String(150), nullable=False)
    foto_url: Mapped[str | None] = mapped_column(String(500))

    # --- Registro no conselho ---------------------------------------------
    conselho: Mapped[Conselho] = mapped_column(
        Enum(Conselho, name="conselho", native_enum=True), nullable=False
    )
    registro_numero: Mapped[str] = mapped_column(String(30), nullable=False)
    registro_uf: Mapped[str] = mapped_column(String(2), nullable=False)
    # Verificação contra o cadastro público do CFP: manual na Fase 1.
    # (A Res. CFP 9/2024 revogou a 11/2018 e descontinuou o e-Psi; o que vale
    # hoje é registro ATIVO no CRP.)
    registro_verificado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registro_verificado_por_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))

    descricao: Mapped[str | None] = mapped_column(String(LIMITE_DESCRICAO))

    # --- Parâmetros de atendimento (sobrescrevem o padrão do sistema) ------
    duracao_sessao_min: Mapped[int | None] = mapped_column(SmallInteger)
    intervalo_entre_sessoes_min: Mapped[int | None] = mapped_column(SmallInteger)
    limite_horas_dia: Mapped[int | None] = mapped_column(SmallInteger)
    antecedencia_minima_agendamento_h: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="2"
    )

    status_cadastro: Mapped[StatusCadastro] = mapped_column(
        Enum(StatusCadastro, name="status_cadastro", native_enum=True),
        nullable=False,
        server_default=StatusCadastro.RASCUNHO.value,
    )

    # --- Mercado Pago (Fase 2) --------------------------------------------
    mp_user_id: Mapped[str | None] = mapped_column(String(50))
    mp_refresh_token_cifrado: Mapped[bytes | None] = mapped_column()
    cpf_cnpj_cifrado: Mapped[bytes | None] = mapped_column()

    #: Denormalizado para exibição; a verdade é SUM(EventoPontuacao.pontos).
    pontos_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    usuario: Mapped[Usuario] = relationship(back_populates="perfil_profissional", lazy="joined")
    especialidades: Mapped[list[ProfissionalEspecialidade]] = relationship(
        back_populates="profissional",
        cascade="all, delete-orphan",
        order_by="ProfissionalEspecialidade.ordem",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            f"descricao IS NULL OR char_length(descricao) <= {LIMITE_DESCRICAO}",
            name="descricao_max_500",
        ),
        CheckConstraint("char_length(registro_uf) = 2", name="uf_2_letras"),
        UniqueConstraint("conselho", "registro_uf", "registro_numero", name="uq_registro_conselho"),
    )

    def __repr__(self) -> str:
        return f"<PerfilProfissional {self.nome_exibicao} {self.registro_completo}>"

    @property
    def registro_completo(self) -> str:
        """``CRP 06/123456`` -- o formato que o profissional reconhece."""
        return f"{self.conselho.value} {self.registro_uf}/{self.registro_numero}"

    @property
    def cadastro_completo(self) -> bool:
        return bool(self.foto_url and self.descricao and self.especialidades)


class ProfissionalEspecialidade(Base):
    """Especialidade escolhida pelo profissional, com faixa de preço.

    **A regra R4 (máximo 5 especialidades) é garantida pelo banco**, não só pela
    aplicação: ``ordem`` só aceita 1..5 e é única por profissional, logo existem
    no máximo 5 linhas. A sexta é impossível mesmo por acesso direto ao banco ou
    por um bug futuro num endpoint.
    """

    __tablename__ = "profissionais_especialidades"

    profissional_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_profissional.usuario_id", ondelete="CASCADE"),
        primary_key=True,
    )
    especialidade_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("especialidades.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    ordem: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    preco_min_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    preco_padrao_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    preco_max_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    profissional: Mapped[PerfilProfissional] = relationship(back_populates="especialidades")
    especialidade: Mapped[Especialidade] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint(f"ordem BETWEEN 1 AND {LIMITE_ESPECIALIDADES}", name="ordem_1_5"),
        UniqueConstraint("profissional_id", "ordem", name="uq_profissional_ordem"),
        CheckConstraint(
            "preco_min_centavos > 0 "
            "AND preco_min_centavos <= preco_padrao_centavos "
            "AND preco_padrao_centavos <= preco_max_centavos",
            name="faixa_preco_coerente",
        ),
        Index("ix_profissionais_especialidades_especialidade_id", "especialidade_id"),
    )


class PerfilPaciente(Timestamps, Base):
    __tablename__ = "perfis_paciente"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    cpf_cifrado: Mapped[bytes | None] = mapped_column()

    #: Menor de 18 nasce PENDENTE_RESPONSAVEL e não agenda nada até o
    #: responsável confirmar (LGPD art. 14). Ver models/responsavel.py.
    status: Mapped[StatusPaciente] = mapped_column(
        Enum(StatusPaciente, name="status_paciente", native_enum=True),
        nullable=False,
        server_default=StatusPaciente.ATIVO.value,
    )
    responsavel_legal_nome: Mapped[str | None] = mapped_column(String(150))
    responsavel_legal_cpf_cifrado: Mapped[bytes | None] = mapped_column()
    #: Conta do responsável, quando ele também é usuário da plataforma.
    responsavel_usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL")
    )
    responsavel_confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contato_emergencia_nome: Mapped[str | None] = mapped_column(String(150))
    contato_emergencia_telefone: Mapped[str | None] = mapped_column(String(20))
    preferencia_canal_notificacao: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="EMAIL"
    )
    observacoes: Mapped[str | None] = mapped_column(Text)
    pontos_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    usuario: Mapped[Usuario] = relationship(
        back_populates="perfil_paciente", lazy="joined", foreign_keys=[usuario_id]
    )
    responsavel: Mapped[Usuario | None] = relationship(
        back_populates="dependentes", lazy="joined", foreign_keys=[responsavel_usuario_id]
    )
    verificacoes: Mapped[list[VerificacaoResponsavel]] = relationship(
        back_populates="paciente", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_perfis_paciente_status", "status"),
        Index("ix_perfis_paciente_responsavel", "responsavel_usuario_id"),
    )

    def __repr__(self) -> str:
        return f"<PerfilPaciente {self.usuario_id} {self.status}>"

    @property
    def eh_menor(self) -> bool:
        """Menor de 18 na data de hoje."""
        if self.data_nascimento is None:
            return False
        from app.core.tempo import agora_utc

        hoje = agora_utc().date()
        idade = (
            hoje.year
            - self.data_nascimento.year
            - ((hoje.month, hoje.day) < (self.data_nascimento.month, self.data_nascimento.day))
        )
        return idade < 18

    @property
    def pode_agendar(self) -> bool:
        return self.status is StatusPaciente.ATIVO

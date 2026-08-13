"""Planos, créditos e pagamento."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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
from app.models.enums import (
    MetodoPagamento,
    SlugPlano,
    StatusCompra,
    StatusCredito,
    StatusPagamento,
)


class Plano(UUIDPk, Timestamps, Base):
    __tablename__ = "planos"

    slug: Mapped[SlugPlano] = mapped_column(
        Enum(SlugPlano, name="slug_plano", native_enum=True), nullable=False, unique=True
    )
    nome: Mapped[str] = mapped_column(String(80), nullable=False)
    descricao: Mapped[str | None] = mapped_column(Text)
    quantidade_sessoes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    validade_dias: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="180")
    desconto_percentual: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="0"
    )
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    ordem_exibicao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")

    __table_args__ = (
        CheckConstraint("quantidade_sessoes > 0", name="quantidade_positiva"),
        CheckConstraint(
            "desconto_percentual >= 0 AND desconto_percentual < 100", name="desconto_0_100"
        ),
    )

    def __repr__(self) -> str:
        return f"<Plano {self.slug} x{self.quantidade_sessoes}>"


class CompraPlano(UUIDPk, Timestamps, Base):
    """Compra de um plano por um paciente, com um profissional específico.

    Os créditos são **atrelados ao profissional**. Sem isso, o paciente compraria
    10 sessões com o profissional mais barato e as usaria com o mais caro.
    (Questão em aberto: o que fazer com créditos restantes se o profissional
    deixar a plataforma. Ver docs/05-roadmap.md.)
    """

    __tablename__ = "compras_plano"

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
    plano_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("planos.id", ondelete="RESTRICT"), nullable=False
    )
    especialidade_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("especialidades.id", ondelete="RESTRICT"), nullable=False
    )

    quantidade_total: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    valor_sessao_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    valor_total_centavos: Mapped[int] = mapped_column(Integer, nullable=False)

    #: CONGELADO no momento da compra. Mudar o parâmetro global depois NÃO
    #: reprecifica esta compra nem os repasses já calculados (ADR 0005).
    percentual_comissao_aplicado: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)

    status: Mapped[StatusCompra] = mapped_column(
        Enum(StatusCompra, name="status_compra", native_enum=True),
        nullable=False,
        server_default=StatusCompra.PENDENTE.value,
    )
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    creditos: Mapped[list[CreditoSessao]] = relationship(
        back_populates="compra", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint("quantidade_total > 0", name="quantidade_positiva"),
        CheckConstraint("valor_total_centavos >= 0", name="valor_nao_negativo"),
        Index("ix_compras_plano_paciente", "paciente_id"),
        Index("ix_compras_plano_profissional", "profissional_id"),
    )


class CreditoSessao(UUIDPk, Timestamps, Base):
    """Direito a uma sessão.

    Uma **linha por crédito**, não um contador. Isso torna o consumo um
    ``UPDATE ... WHERE status='DISPONIVEL'`` atômico, e deixa um rastro
    auditável de qual crédito pagou qual sessão -- coisa que um
    ``creditos_restantes -= 1`` nunca daria.
    """

    __tablename__ = "creditos_sessao"

    compra_plano_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("compras_plano.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[StatusCredito] = mapped_column(
        Enum(StatusCredito, name="status_credito", native_enum=True),
        nullable=False,
        server_default=StatusCredito.DISPONIVEL.value,
    )
    agendamento_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("agendamentos.id", ondelete="SET NULL"),
        unique=True,
    )
    consumido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    compra: Mapped[CompraPlano] = relationship(back_populates="creditos")

    __table_args__ = (Index("ix_creditos_sessao_compra_status", "compra_plano_id", "status"),)


class Pagamento(UUIDPk, Timestamps, Base):
    """Uma cobrança junto ao provedor.

    A separação entre ``taxa_provedor``, ``comissao_plataforma`` e
    ``liquido_profissional`` é explícita de propósito: no crédito a taxa (~4,98%)
    é MAIOR que a comissão padrão (5%), e a política de quem absorve isso precisa
    ser visível, não implícita no cálculo.
    """

    __tablename__ = "pagamentos"

    compra_plano_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("compras_plano.id", ondelete="RESTRICT"), nullable=False
    )
    provedor: Mapped[str] = mapped_column(String(30), nullable=False)
    provedor_pagamento_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    metodo: Mapped[MetodoPagamento] = mapped_column(
        Enum(MetodoPagamento, name="metodo_pagamento", native_enum=True), nullable=False
    )
    status: Mapped[StatusPagamento] = mapped_column(
        Enum(StatusPagamento, name="status_pagamento", native_enum=True),
        nullable=False,
        server_default=StatusPagamento.CRIADO.value,
    )

    valor_bruto_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    taxa_provedor_centavos: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    comissao_plataforma_centavos: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    imposto_retido_centavos: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    liquido_profissional_centavos: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    moeda: Mapped[str] = mapped_column(String(3), nullable=False, server_default="BRL")

    pix_qrcode: Mapped[str | None] = mapped_column(Text)
    pix_copia_cola: Mapped[str | None] = mapped_column(Text)
    pix_expira_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: O agendamento que originou a compra. Num pacote de 5 sessões, é a
    #: primeira delas -- as outras quatro consomem créditos depois. Existe para
    #: o webhook saber o que confirmar quando o Pix é pago minutos mais tarde.
    agendamento_origem_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("agendamentos.id", ondelete="SET NULL")
    )

    #: Impede cobrar duas vezes se o usuário der duplo-clique em "pagar".
    chave_idempotencia: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    payload_bruto: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    aprovado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    compra: Mapped[CompraPlano] = relationship()

    __table_args__ = (
        CheckConstraint("valor_bruto_centavos >= 0", name="bruto_nao_negativo"),
        # A invariante da repartição (soma == bruto) é testada em
        # regras/precificacao.py; aqui garantimos só o básico.
        CheckConstraint(
            "taxa_provedor_centavos >= 0 AND comissao_plataforma_centavos >= 0 "
            "AND imposto_retido_centavos >= 0 AND liquido_profissional_centavos >= 0",
            name="parcelas_nao_negativas",
        ),
        Index("ix_pagamentos_compra", "compra_plano_id"),
    )


class EventoWebhook(UUIDPk, Timestamps, Base):
    """Registro de todo webhook recebido, para idempotência e auditoria.

    O Mercado Pago **reenvia** quando não recebe 200 rápido o bastante, e pode
    entregar fora de ordem ou em duplicata. Sem esta tabela, um reenvio de
    "pagamento aprovado" creditaria a sessão duas vezes.

    A `UNIQUE` em ``(provedor, evento_id_externo)`` é o que garante isso -- não a
    checagem em Python, que perde a corrida quando dois reenvios chegam juntos.
    A inserção acontece **antes** do processamento: se o processamento falhar,
    a linha fica com ``processado_em`` nulo e o erro registrado, o que dá uma
    fila de reprocessamento em vez de um evento perdido em silêncio.
    """

    __tablename__ = "eventos_webhook"

    provedor: Mapped[str] = mapped_column(String(30), nullable=False)
    evento_id_externo: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[str] = mapped_column(String(60), nullable=False)
    provedor_pagamento_id: Mapped[str | None] = mapped_column(String(100))

    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erro: Mapped[str | None] = mapped_column(Text)
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("provedor", "evento_id_externo", name="uq_evento_webhook"),
        Index("ix_eventos_webhook_pagamento", "provedor_pagamento_id"),
        # Fila de reprocessamento: eventos que chegaram mas não completaram.
        Index(
            "ix_eventos_webhook_pendentes",
            "criado_em",
            postgresql_where=text("processado_em IS NULL"),
        ),
    )

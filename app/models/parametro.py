"""Parâmetros de sistema.

Regras de negócio que o admin precisa mudar sem deploy. A comissão vive aqui, e
não no ``.env``, porque o requisito é explicitamente "configurável" (ADR 0005) e
porque as duas fontes documentadas discordavam entre si: 5% no
``funcionalidades.md`` e 3% no mapa mental.

Importante: mudar um parâmetro **não** reprecifica o passado. ``CompraPlano``
congela o percentual aplicado no momento da compra.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChaveParametro:
    """Chaves conhecidas. Constantes em vez de strings soltas pelo código."""

    COMISSAO_PERCENTUAL = "comissao.percentual_padrao"
    DURACAO_SESSAO_MIN = "sessao.duracao_padrao_min"
    INTERVALO_ENTRE_SESSOES_MIN = "sessao.intervalo_entre_min"
    LIMITE_HORAS_DIA = "agenda.limite_horas_dia_profissional"
    LIMITE_SESSOES_SEMANA = "agenda.limite_sessoes_semana_paciente"
    SUGESTAO_MAX_SEMANA = "agenda.sugestao_max_semana"
    TOLERANCIA_ATRASO_MIN = "sessao.tolerancia_atraso_min"
    ANTECEDENCIA_LINK_MIN = "sessao.antecedencia_link_min"
    JANELA_PONTUALIDADE_MIN = "sessao.janela_pontualidade_min"
    TTL_RESERVA_PAGAMENTO_MIN = "agendamento.ttl_reserva_pagamento_min"
    MAX_ESPECIALIDADES = "profissional.max_especialidades"
    TAXA_PIX_PERCENTUAL = "pagamento.taxa_pix_percentual"
    TAXA_CREDITO_PERCENTUAL = "pagamento.taxa_credito_percentual"
    TAXA_DEBITO_PERCENTUAL = "pagamento.taxa_debito_percentual"


class ParametroSistema(Base):
    __tablename__ = "parametros_sistema"

    chave: Mapped[str] = mapped_column(String(100), primary_key=True)
    valor: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # server_default (não `default=`) para bater com a migration: `alembic check`
    # compara o schema real, e um default só do lado do Python geraria diff eterno.
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, server_default="str")
    descricao: Mapped[str | None] = mapped_column(Text)
    atualizado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A tabela nasceu na migration 0001, antes de `usuarios` existir; a FK só foi
    # adicionada na 0003. Daí `use_alter=True`: o SQLAlchemy sabe que a constraint
    # é criada depois, e não tenta ordenar a criação das tabelas por causa dela.
    atualizado_por_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL", use_alter=True),
    )

    def __repr__(self) -> str:
        return f"<ParametroSistema {self.chave}={self.valor!r}>"

    # --- Leitura tipada ----------------------------------------------------

    def como_int(self) -> int:
        return int(self.valor)

    def como_decimal(self) -> Decimal:
        return Decimal(str(self.valor))

    def como_bool(self) -> bool:
        return bool(self.valor)

    def como_str(self) -> str:
        return str(self.valor)

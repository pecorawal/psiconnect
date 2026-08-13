"""Verificação do responsável legal por paciente menor de 18 anos.

LGPD art. 14: o tratamento de dados de crianças e adolescentes exige
consentimento **específico e em destaque** de ao menos um dos pais ou do
responsável legal. O §5 do mesmo artigo pede que o controlador faça "todos os
esforços razoáveis para verificar" que o consentimento partiu de quem tem
legitimidade — daí exigir documento **e** confirmação por um canal do próprio
responsável.

Dois caminhos, ambos terminando aqui:

* **o menor se cadastra** → fica ``PENDENTE_RESPONSAVEL`` e um convite é enviado
  ao responsável;
* **o responsável se cadastra** e declara que quer cadastrar um menor → o
  consentimento é dado no ato, e o cadastro do menor já nasce ativo.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk
from app.models.enums import TipoDocumentoResponsavel

if TYPE_CHECKING:
    from app.models.perfil import PerfilPaciente


class VerificacaoResponsavel(UUIDPk, Timestamps, Base):
    """Um convite pendente (ou já confirmado) de responsável legal.

    O documento é guardado **cifrado** e por tempo limitado: uma cópia de RG é
    dado sensível por si só, e mantê-la depois de verificada só aumenta o
    estrago de um eventual vazamento. Depois da conferência, o service apaga a
    imagem e mantém apenas o registro de que houve verificação, com o tipo do
    documento e os últimos dígitos.
    """

    __tablename__ = "verificacoes_responsavel"

    paciente_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("perfis_paciente.usuario_id", ondelete="CASCADE"),
        nullable=False,
    )

    # --- Dados de contato do responsável ------------------------------------
    responsavel_nome: Mapped[str] = mapped_column(String(150), nullable=False)
    responsavel_email: Mapped[str | None] = mapped_column(String(255))
    responsavel_telefone_e164: Mapped[str | None] = mapped_column(String(20))
    #: Preenchido quando o responsável tem conta na plataforma.
    responsavel_usuario_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL")
    )
    parentesco: Mapped[str | None] = mapped_column(String(40))

    # --- Convite -------------------------------------------------------------
    #: Só o hash: quem tem o link é quem consegue confirmar.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    enviado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tentativas_envio: Mapped[int] = mapped_column(nullable=False, server_default="0")

    # --- Confirmação ---------------------------------------------------------
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmado_ip: Mapped[str | None] = mapped_column(String(45))
    confirmado_user_agent: Mapped[str | None] = mapped_column(Text)

    documento_tipo: Mapped[TipoDocumentoResponsavel | None] = mapped_column(
        Enum(TipoDocumentoResponsavel, name="tipo_documento_responsavel", native_enum=True)
    )
    #: Chave do objeto no storage (MinIO). O arquivo sobe **cifrado**
    #: (AES-256-GCM, chave do app) num bucket privado — nem o operador do
    #: storage consegue lê-lo. Apagado após a conferência.
    documento_chave: Mapped[str | None] = mapped_column(String(300))
    documento_content_type: Mapped[str | None] = mapped_column(String(60))
    #: Últimos dígitos, para o registro permanecer conferível sem guardar a imagem.
    documento_final: Mapped[str | None] = mapped_column(String(8))
    documento_removido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    recusado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motivo_recusa: Mapped[str | None] = mapped_column(Text)

    paciente: Mapped[PerfilPaciente] = relationship(back_populates="verificacoes", lazy="joined")

    __table_args__ = (
        Index("ix_verificacoes_responsavel_paciente", "paciente_id"),
        Index("ix_verificacoes_responsavel_expira", "expira_em", "confirmado_em"),
    )

    def __repr__(self) -> str:
        estado = "confirmada" if self.confirmado_em else "pendente"
        return f"<VerificacaoResponsavel {self.paciente_id} {estado}>"

    @property
    def pendente(self) -> bool:
        return self.confirmado_em is None and self.recusado_em is None

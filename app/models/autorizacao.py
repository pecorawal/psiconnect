"""Papéis e permissões — esquema adotado do Pectec Nexos.

Em vez de papéis fixos no código, o admin cria **papéis** e marca, por módulo,
o que aquele papel pode fazer (criar, ler, atualizar, deletar). A política de
acesso fica numa tabela auditável, não espalhada por decoradores.

## Como isto convive com ``Usuario.papel``

São duas coisas diferentes, e misturá-las causaria confusão:

* **``Papel``** (PACIENTE / PROFISSIONAL / ADMIN) é o **tipo de conta**. Define
  qual perfil existe e qual fluxo a pessoa percorre. Um paciente não vira
  profissional por ganhar uma permissão.
* **``Role``** é um **conjunto de permissões administrativas**. Só faz sentido
  para quem opera a plataforma. Paciente e profissional não precisam de Role:
  as regras deles são de domínio ("só vejo o que é meu"), não CRUD por módulo.

Por isso ``Usuario.role_id`` é **nulo** para paciente e profissional.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import Timestamps, UUIDPk

if TYPE_CHECKING:
    from app.models.usuario import Usuario

#: Módulos protegíveis. Um endpoint administrativo novo precisa entrar aqui e
#: no mapa de prefixos em ``app/core/permissoes.py`` — senão fica sem política.
MODULOS_SISTEMA = (
    "usuarios",  # gestão de contas e papéis (tipicamente só admin)
    "profissionais",  # aprovação de cadastro e verificação de registro
    "pacientes",  # suporte a contas de paciente
    "agendamentos",  # consultas: remarcar, cancelar
    "financeiro",  # pagamentos, repasses, notas fiscais
    "taxonomias",  # especialidades e sintomas
    "configuracoes",  # ParametroSistema, incluindo a comissão
    "termos",  # publicação de novas versões de termos
    "auditoria",  # trilha de acesso e logs
    "documentos",  # documentos de identificação enviados
)

OPERACOES_CRUD = ("create", "read", "update", "delete")

#: operação -> coluna de RolePermission
COLUNA_DA_OPERACAO = {
    "create": "pode_criar",
    "read": "pode_ler",
    "update": "pode_atualizar",
    "delete": "pode_deletar",
}


class Role(UUIDPk, Timestamps, Base):
    __tablename__ = "roles"

    nome: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    descricao: Mapped[str | None] = mapped_column(String(200))
    #: Papel de sistema não pode ser excluído nem ter as permissões esvaziadas
    #: pela API — evita alguém remover o próprio acesso de administrador.
    sistema: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    permissoes: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )
    usuarios: Mapped[list[Usuario]] = relationship(back_populates="role", lazy="noload")

    def __repr__(self) -> str:
        return f"<Role {self.nome}>"

    def permite(self, modulo: str, operacao: str) -> bool:
        coluna = COLUNA_DA_OPERACAO.get(operacao)
        if coluna is None:
            return False
        for permissao in self.permissoes:
            if permissao.modulo == modulo:
                return bool(getattr(permissao, coluna))
        # Ausência de linha = ausência de permissão. Negar por omissão é o
        # padrão correto: um módulo novo não nasce liberado para todo mundo.
        return False


class RolePermission(UUIDPk, Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    modulo: Mapped[str] = mapped_column(String(40), nullable=False)

    pode_criar: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    pode_ler: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    pode_atualizar: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    pode_deletar: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    role: Mapped[Role] = relationship(back_populates="permissoes")

    __table_args__ = (
        UniqueConstraint("role_id", "modulo", name="uq_role_modulo"),
        Index("ix_role_permissions_role", "role_id"),
    )

    def __repr__(self) -> str:
        flags = "".join(
            letra
            for letra, valor in (
                ("C", self.pode_criar),
                ("R", self.pode_ler),
                ("U", self.pode_atualizar),
                ("D", self.pode_deletar),
            )
            if valor
        )
        return f"<RolePermission {self.modulo} [{flags or '-'}]>"


class AcessoDocumento(UUIDPk, Base):
    """Registro de quem abriu qual documento de identificação.

    Documento de identidade é dado sensível: além de restringir o acesso ao
    dono e ao admin, é preciso saber **quem** olhou e **quando**. Sem isso, o
    controle é uma promessa sem prova.
    """

    __tablename__ = "acessos_documento"

    usuario_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True
    )
    recurso: Mapped[str] = mapped_column(String(60), nullable=False)
    recurso_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: "dono" ou "admin" — por qual regra o acesso foi permitido.
    motivo: Mapped[str] = mapped_column(String(20), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    acessado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_acessos_documento_recurso", "recurso", "recurso_id"),
        Index("ix_acessos_documento_usuario", "usuario_id", "acessado_em"),
    )

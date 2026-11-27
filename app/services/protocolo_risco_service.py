"""Protocolo de risco: avisar o profissional de um paciente em sinal de crise.

Pendência 5 de compliance. O paciente que marca um sintoma com
``Sintoma.bandeira_risco`` (ideação suicida, automutilação, violência) já vê o
CVV 188 em destaque; aqui o **profissional que o atende** também fica sabendo.

Quem recebe: só profissionais com vínculo de atendimento (consulta confirmada,
em andamento, realizada ou com falta do paciente). Uma reserva ainda pendente
de pagamento não é vínculo, então não expõe dado de saúde a quem o paciente
apenas olhou.

Dois momentos disparam o alerta, e os dois passam por ``alertar_profissionais``:

1. o paciente registra sintomas com bandeira de risco, já tendo profissional;
2. uma consulta é confirmada com um paciente que já tinha sinalizado risco.

O e-mail não carrega conteúdo clínico (o corpo passa por servidores que não
controlamos): diz só que há um aviso no painel. O detalhe — quais sintomas —
aparece no painel, atrás do login do profissional vinculado.

A chave de idempotência inclui o conjunto de sintomas de risco: o mesmo quadro
não gera um segundo e-mail a cada vez que o paciente salva o formulário, mas um
sinal novo gera.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Agendamento,
    CanalNotificacao,
    PacienteSintoma,
    PerfilPaciente,
    PerfilProfissional,
    Sintoma,
    StatusAgendamento,
    Usuario,
)
from app.services.notificacao_service import NotificacaoService

log = get_logger(__name__)

TEMPLATE_ALERTA_RISCO = "alerta_risco"

#: Estados de agendamento que configuram vínculo de atendimento.
VINCULO = (
    StatusAgendamento.CONFIRMADO,
    StatusAgendamento.EM_ANDAMENTO,
    StatusAgendamento.REALIZADO,
    StatusAgendamento.NO_SHOW_PACIENTE,
)


@dataclass(frozen=True, slots=True)
class AlertaRisco:
    """Um paciente do profissional com sinal de risco ativo."""

    paciente_id: uuid.UUID
    paciente_nome: str
    sintomas: list[str]
    registrado_em: datetime


class ProtocoloRiscoService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    async def sintomas_de_risco(self, paciente_id: uuid.UUID) -> list[tuple[Sintoma, datetime]]:
        linhas = await self.sessao.execute(
            select(Sintoma, PacienteSintoma.registrado_em)
            .join(PacienteSintoma, PacienteSintoma.sintoma_id == Sintoma.id)
            .where(
                PacienteSintoma.paciente_id == paciente_id,
                Sintoma.bandeira_risco.is_(True),
            )
            .order_by(Sintoma.ordem_exibicao)
        )
        return [(s, quando) for s, quando in linhas.all()]

    async def profissionais_vinculados(self, paciente_id: uuid.UUID) -> list[PerfilProfissional]:
        ids = select(Agendamento.profissional_id).where(
            Agendamento.paciente_id == paciente_id,
            Agendamento.status.in_(VINCULO),
        )
        return list(
            (
                await self.sessao.execute(
                    select(PerfilProfissional).where(PerfilProfissional.usuario_id.in_(ids))
                )
            )
            .scalars()
            .all()
        )

    async def alertar_profissionais(self, paciente: PerfilPaciente) -> int:
        """Enfileira o aviso para cada profissional vinculado. Devolve quantos
        avisos novos saíram (zero se não há risco, vínculo ou novidade)."""
        risco = await self.sintomas_de_risco(paciente.usuario_id)
        if not risco:
            return 0

        assinatura = hashlib.sha256(
            ",".join(sorted(str(s.id) for s, _ in risco)).encode()
        ).hexdigest()[:16]

        # Explícito: o perfil pode chegar sem a relação carregada, e lazy load
        # não existe em sessão async.
        usuario_paciente = await self.sessao.get(Usuario, paciente.usuario_id)
        if usuario_paciente is None:
            return 0

        notificacoes = NotificacaoService(self.sessao)
        enviados = 0
        for profissional in await self.profissionais_vinculados(paciente.usuario_id):
            criada = await notificacoes.enfileirar(
                usuario=profissional.usuario,
                canal=CanalNotificacao.EMAIL,
                template=TEMPLATE_ALERTA_RISCO,
                # Só o primeiro nome: nada de sintoma no corpo do e-mail.
                contexto={"paciente_nome": usuario_paciente.primeiro_nome},
                chave_idempotencia=(
                    f"risco:{profissional.usuario_id}:{paciente.usuario_id}:{assinatura}"
                ),
            )
            enviados += 1 if criada else 0

        if enviados:
            # IDs apenas: o nome do sintoma é dado de saúde e não vai para log.
            log.warning(
                "risco.profissionais_alertados",
                paciente_id=str(paciente.usuario_id),
                profissionais=enviados,
            )
        return enviados

    async def alertas_do_profissional(self, profissional_id: uuid.UUID) -> list[AlertaRisco]:
        """Pacientes vinculados ao profissional que têm sinal de risco ativo."""
        pacientes = select(Agendamento.paciente_id).where(
            Agendamento.profissional_id == profissional_id,
            Agendamento.status.in_(VINCULO),
        )
        linhas = await self.sessao.execute(
            select(PerfilPaciente, Sintoma.nome, PacienteSintoma.registrado_em)
            .join(PacienteSintoma, PacienteSintoma.paciente_id == PerfilPaciente.usuario_id)
            .join(Sintoma, PacienteSintoma.sintoma_id == Sintoma.id)
            .where(
                PerfilPaciente.usuario_id.in_(pacientes),
                Sintoma.bandeira_risco.is_(True),
            )
            .order_by(PacienteSintoma.registrado_em.desc(), Sintoma.ordem_exibicao)
        )

        por_paciente: dict[uuid.UUID, AlertaRisco] = {}
        for paciente, nome_sintoma, quando in linhas.all():
            alerta = por_paciente.get(paciente.usuario_id)
            if alerta is None:
                alerta = AlertaRisco(
                    paciente_id=paciente.usuario_id,
                    paciente_nome=paciente.usuario.nome_completo,
                    sintomas=[],
                    registrado_em=quando,
                )
                por_paciente[paciente.usuario_id] = alerta
            alerta.sintomas.append(nome_sintoma)
        return list(por_paciente.values())

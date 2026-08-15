"""Tarefas periódicas de agenda.

Três coisas que precisam acontecer sem ninguém clicar em nada:

1. **Preparar a sala em T-20min** e enfileirar o link (R8).
2. **Expirar reservas** cujo pagamento não veio — sem isso um checkout
   abandonado bloquearia o horário para sempre, já que ``PENDENTE_PAGAMENTO``
   ocupa a agenda.
3. **Marcar no-show** depois da tolerância (R7).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import (
    Agendamento,
    Sessao,
    StatusAgendamento,
    StatusSessao,
)
from app.providers.base import VideoProvider
from app.services.agendamento_service import AgendamentoService
from app.services.notificacao_service import NotificacaoService
from app.services.parametros_service import ParametrosService
from app.services.sessao_service import SessaoService

log = get_logger(__name__)


async def preparar_salas(
    sessao: AsyncSession, parametros: ParametrosService, video: VideoProvider
) -> int:
    """Cria a sala das consultas que começam em breve e enfileira o link."""
    antecedencia = await parametros.antecedencia_link_min()
    agora = agora_utc()
    limite = agora + timedelta(minutes=antecedencia)

    candidatos = list(
        (
            await sessao.execute(
                select(Agendamento)
                .outerjoin(Sessao, Sessao.agendamento_id == Agendamento.id)
                .where(
                    Agendamento.status == StatusAgendamento.CONFIRMADO,
                    Agendamento.inicio_utc <= limite,
                    # Ainda não começou (nem terminou): não adianta abrir sala
                    # de consulta que já passou.
                    Agendamento.fim_utc > agora,
                    Sessao.id.is_(None),
                )
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    servico_sessao = SessaoService(sessao, parametros, video)
    notificacoes = NotificacaoService(sessao)
    preparadas = 0

    for agendamento in candidatos:
        sessao_atendimento = await servico_sessao.preparar_sala(agendamento)
        if sessao_atendimento.sala_url:
            await notificacoes.agendar_link_da_sessao(
                agendamento, sessao_atendimento.sala_url, antecedencia
            )
            sessao_atendimento.link_enviado_em = agora_utc()
            preparadas += 1

    await sessao.flush()

    if preparadas:
        log.info("worker.salas_preparadas", quantidade=preparadas)
    return preparadas


async def expirar_reservas(sessao: AsyncSession, parametros: ParametrosService) -> int:
    """Devolve ao mercado os horários cujo pagamento não veio."""
    return await AgendamentoService(sessao, parametros).expirar_reservas_vencidas()


async def marcar_no_show(sessao: AsyncSession, parametros: ParametrosService) -> int:
    """R7 — passada a tolerância, quem não entrou levou falta.

    A decisão usa ``Sessao.paciente_entrou_em`` / ``profissional_entrou_em``,
    que vêm da trilha de eventos. Consultas sem sala preparada não entram aqui:
    se a sala nunca abriu, a falha não foi de ninguém.
    """
    tolerancia = await parametros.tolerancia_atraso_min()
    corte = agora_utc() - timedelta(minutes=tolerancia)

    candidatas = list(
        (
            await sessao.execute(
                select(Sessao)
                .join(Agendamento, Sessao.agendamento_id == Agendamento.id)
                .where(
                    Agendamento.status == StatusAgendamento.CONFIRMADO,
                    Agendamento.inicio_utc < corte,
                    Sessao.status.in_((StatusSessao.SALA_PRONTA, StatusSessao.LOBBY)),
                )
                .limit(50)
            )
        )
        .scalars()
        .all()
    )

    marcadas = 0
    for sessao_atendimento in candidatas:
        agendamento = sessao_atendimento.agendamento
        if sessao_atendimento.paciente_entrou_em is None:
            agendamento.status = StatusAgendamento.NO_SHOW_PACIENTE
        elif sessao_atendimento.profissional_entrou_em is None:
            agendamento.status = StatusAgendamento.NO_SHOW_PROFISSIONAL
        else:
            continue
        sessao_atendimento.status = StatusSessao.EXPIRADA
        marcadas += 1

    # Emite o SQL aqui, e não só no commit do loop: assim uma violação de
    # constraint estoura dentro do try/except desta tarefa, sem derrubar as
    # outras do ciclo.
    await sessao.flush()

    if marcadas:
        log.info("worker.no_show_marcados", quantidade=marcadas)
    return marcadas


async def expirar_creditos(sessao: AsyncSession) -> int:
    """Marca créditos vencidos. Ver `CreditoService.expirar_vencidos`."""
    from app.services.credito_service import CreditoService

    return await CreditoService(sessao).expirar_vencidos()

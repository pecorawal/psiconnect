"""Envio das notificações pendentes.

Retry com espera exponencial: provedor externo cai, e insistir de imediato só
piora. Depois de ``MAX_TENTATIVAS`` a linha fica em ``FALHA`` com o erro — não
some, para que dê para investigar.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import StatusNotificacao
from app.providers.base import NotificationProvider
from app.services.notificacao_service import NotificacaoService

log = get_logger(__name__)

MAX_TENTATIVAS = 5
#: 1min, 2min, 4min, 8min, 16min.
BASE_ESPERA_MIN = 1


async def enviar_pendentes(
    sessao: AsyncSession, provedor: NotificationProvider, limite: int = 50
) -> tuple[int, int]:
    """Devolve ``(enviadas, falhas)``."""
    servico = NotificacaoService(sessao)
    pendentes = await servico.pendentes(limite)

    enviadas = 0
    falhas = 0

    for notificacao in pendentes:
        if not provedor.suporta(notificacao.canal):
            # Canal ainda não implementado (WhatsApp antes da Fase 3): não é
            # erro, é indisponibilidade. Cancelar evita retry eterno.
            notificacao.status = StatusNotificacao.CANCELADA
            notificacao.erro = f"canal {notificacao.canal.value} indisponível"
            continue

        notificacao.tentativas += 1
        sucesso = False
        erro = ""
        try:
            resultado = await provedor.enviar(
                notificacao.canal,
                notificacao.destino,
                notificacao.template,
                notificacao.contexto or {},
                notificacao.chave_idempotencia,
            )
        except Exception as exc:  # provedor externo pode levantar qualquer coisa
            erro = f"{type(exc).__name__}: {exc}"
        else:
            sucesso = resultado.sucesso
            erro = resultado.erro or ""
            if sucesso:
                notificacao.status = StatusNotificacao.ENVIADA
                notificacao.enviada_em = agora_utc()
                notificacao.provedor_msg_id = resultado.provedor_msg_id
                notificacao.erro = None
                enviadas += 1

        if sucesso:
            continue

        notificacao.erro = erro[:500]
        if notificacao.tentativas >= MAX_TENTATIVAS:
            notificacao.status = StatusNotificacao.FALHA
            falhas += 1
            log.warning(
                "outbox.falha_definitiva",
                notificacao_id=str(notificacao.id),
                template=notificacao.template,
                tentativas=notificacao.tentativas,
            )
        else:
            espera = BASE_ESPERA_MIN * (2 ** (notificacao.tentativas - 1))
            notificacao.proxima_tentativa_em = agora_utc() + timedelta(minutes=espera)
            notificacao.agendada_para = notificacao.proxima_tentativa_em

    await sessao.flush()

    if enviadas or falhas:
        log.info("outbox.processado", enviadas=enviadas, falhas=falhas)
    return enviadas, falhas

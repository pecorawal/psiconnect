"""Processamento de webhooks de pagamento.

O provedor **reenvia**: se não devolvermos 200 rápido, ou se a rede falhar no
meio, o mesmo evento chega de novo — às vezes fora de ordem, às vezes em
paralelo. Todo o desenho aqui parte disso.

## A ordem das operações

1. Valida a assinatura (fora daqui, no provider).
2. **Insere** `EventoWebhook` — se a `UNIQUE` reclamar, já processamos: 200 e fim.
3. Consulta o pagamento **no provedor**, ignorando o status do corpo recebido.
4. Aplica o efeito.
5. Marca `processado_em`.

O passo 3 é o que muita integração erra: o corpo do webhook do Mercado Pago só
diz "o recurso X mudou". Confiar no status que veio no corpo é aceitar o valor
que o remetente escolheu — quem forjar um corpo com ``"status": "approved"``
ganharia uma sessão de graça. A verdade vem da consulta autenticada.

O passo 2 antes do 4 é deliberado: se o processamento estourar, fica uma linha
com `processado_em` nulo e o erro registrado — uma fila de reprocessamento, em
vez de um pagamento perdido em silêncio.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import Agendamento, EventoWebhook, Pagamento, StatusPagamento
from app.providers.base import EventoPagamento, PaymentProvider

if TYPE_CHECKING:
    from app.services.checkout_service import CheckoutService

log = get_logger(__name__)


class WebhookService:
    def __init__(
        self,
        sessao: AsyncSession,
        pagamentos: PaymentProvider,
        checkout: CheckoutService,
    ) -> None:
        self.sessao = sessao
        self.pagamentos = pagamentos
        self.checkout = checkout

    async def processar(self, evento: EventoPagamento) -> bool:
        """Devolve ``True`` se processou agora, ``False`` se era repetição.

        Nunca levanta por evento duplicado: para o provedor, repetição tratada é
        sucesso. Levantar viraria retry infinito do lado dele.
        """
        registro = EventoWebhook(
            provedor=self.pagamentos.nome,
            evento_id_externo=evento.evento_id_externo,
            tipo=evento.tipo,
            provedor_pagamento_id=evento.provedor_pagamento_id,
            payload=dict(evento.payload),
        )

        # Savepoint: a violação de UNIQUE invalida a transação corrente no
        # Postgres. Sem isolar aqui, um evento repetido derrubaria tudo que
        # viesse depois na mesma transação.
        try:
            async with self.sessao.begin_nested():
                self.sessao.add(registro)
                await self.sessao.flush()
        except IntegrityError:
            log.info(
                "webhook.duplicado",
                evento_id=evento.evento_id_externo,
                pagamento_id=evento.provedor_pagamento_id,
            )
            return False

        try:
            await self._aplicar(evento)
        except Exception as exc:
            # A linha permanece com processado_em nulo: fica na fila de
            # reprocessamento em vez de sumir.
            registro.erro = f"{type(exc).__name__}: {exc}"
            registro.tentativas += 1
            log.exception(
                "webhook.falha_processamento",
                evento_id=evento.evento_id_externo,
            )
            raise

        registro.processado_em = agora_utc()
        log.info(
            "webhook.processado",
            evento_id=evento.evento_id_externo,
            pagamento_id=evento.provedor_pagamento_id,
        )
        return True

    async def _aplicar(self, evento: EventoPagamento) -> None:
        if not evento.provedor_pagamento_id:
            return

        pagamento = await self.sessao.scalar(
            select(Pagamento).where(Pagamento.provedor_pagamento_id == evento.provedor_pagamento_id)
        )
        if pagamento is None:
            # Acontece de verdade: o webhook pode chegar antes de o POST que
            # criou a cobrança ter commitado. Não é erro -- o provedor reenvia,
            # e o registro fica na fila para reprocessar.
            log.warning(
                "webhook.pagamento_desconhecido",
                pagamento_id=evento.provedor_pagamento_id,
            )
            raise PagamentoAindaNaoRegistrado(evento.provedor_pagamento_id)

        # A fonte da verdade é a consulta autenticada, nunca o corpo recebido.
        cobranca = await self.pagamentos.consultar(evento.provedor_pagamento_id)

        if cobranca.status is pagamento.status:
            return

        anterior = pagamento.status
        pagamento.status = cobranca.status
        pagamento.taxa_provedor_centavos = cobranca.taxa_provedor_centavos
        pagamento.payload_bruto = dict(cobranca.payload_bruto)

        agendamento = (
            await self.sessao.get(Agendamento, pagamento.agendamento_origem_id)
            if pagamento.agendamento_origem_id
            else None
        )
        if agendamento is None:
            log.warning(
                "webhook.sem_agendamento_origem",
                pagamento_id=evento.provedor_pagamento_id,
            )
            return

        if cobranca.status is StatusPagamento.APROVADO:
            await self.checkout.confirmar_pagamento(pagamento, agendamento)
        elif cobranca.status in (
            StatusPagamento.RECUSADO,
            StatusPagamento.CANCELADO,
            StatusPagamento.ESTORNADO,
        ):
            await self.checkout.desfazer_pagamento(pagamento, agendamento)

        log.info(
            "webhook.status_alterado",
            pagamento_id=evento.provedor_pagamento_id,
            de=anterior.value,
            para=cobranca.status.value,
        )


class PagamentoAindaNaoRegistrado(Exception):
    """O webhook chegou antes de o pagamento ter sido gravado.

    Não é erro de programação: é corrida normal entre o POST que cria a cobrança
    e a notificação do provedor. Fica na fila para o reenvio resolver.
    """

    def __init__(self, pagamento_id: str) -> None:
        super().__init__(f"pagamento {pagamento_id} ainda não registrado")

"""Endpoint de webhook do Mercado Pago.

## O código HTTP é a decisão

Para o provedor, a resposta significa "reenvie" ou "não reenvie". Errar isso
custa: 200 em algo que falhou perde o pagamento em silêncio; 500 em algo já
tratado gera reenvio eterno.

| Situação | Resposta | Por quê |
|---|---|---|
| Assinatura inválida | **401** | Não é nosso; nunca reenviar |
| Evento repetido | **200** | Já tratado — reenviar não muda nada |
| Pagamento ainda não gravado | **409** | Corrida normal; queremos o reenvio |
| Erro inesperado | **500** | Queremos o reenvio |
| Sucesso | **200** | — |

## Isenção de CSRF

Esta rota está em ``PREFIXOS_SEM_CSRF``: quem chama é o Mercado Pago, que não
tem como carregar nosso token. A autenticação aqui é a assinatura HMAC, não o
cookie de sessão — por isso a validação da assinatura não é opcional em
nenhum ambiente.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.core.deps import Config, DbSession, ProvidersAtuais
from app.core.logging import get_logger
from app.db.sessao import UnitOfWork
from app.services.checkout_service import CheckoutService
from app.services.parametros_service import ParametrosService
from app.services.webhook_service import PagamentoAindaNaoRegistrado, WebhookService

log = get_logger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/mercadopago")
async def receber_mercadopago(
    request: Request,
    sessao: DbSession,
    providers: ProvidersAtuais,
    settings: Config,
) -> Response:
    corpo = await request.body()

    try:
        evento = providers.pagamento.validar_webhook(dict(request.headers), corpo)
    except Exception:
        # Sem detalhe na resposta: quem está sondando o endpoint não deve
        # descobrir se errou o segredo, o formato ou o id.
        log.warning("webhook.assinatura_recusada", tamanho=len(corpo))
        return Response(status_code=401)

    servico = WebhookService(
        sessao,
        providers.pagamento,
        CheckoutService(sessao, ParametrosService(sessao, settings), providers.pagamento),
    )

    try:
        async with UnitOfWork(sessao):
            processou = await servico.processar(evento)
    except PagamentoAindaNaoRegistrado:
        # 409 e não 500: é corrida esperada entre o POST do checkout e a
        # notificação, não defeito. O reenvio do provedor resolve.
        return Response(status_code=409)

    # Repetição também é 200: para o provedor, "já tratei" é sucesso. O header
    # existe só para os testes e para depuração distinguirem os dois casos.
    return Response(
        status_code=200,
        headers={"X-Psiconnect-Processado": "1" if processou else "0"},
    )

"""Mercado Pago — Pix e cartão, com split de marketplace.

Ver [ADR 0004](../../../docs/adr/0004-mercado-pago-split.md). O ponto central é
que **o dinheiro não transita pela plataforma**: o pagamento é criado com o
token do profissional (obtido por OAuth) e a comissão vai como
``application_fee``. No modelo "recebo tudo e repasso depois", a plataforma
passaria a movimentar recursos de terceiros, o que caracteriza arranjo de
pagamento e atrai exigências do BACEN.

Usa ``httpx`` direto, não o SDK oficial: o SDK é síncrono, e são só três
chamadas REST. Menos uma dependência para manter, e o retry fica sob nosso
controle.

## Idempotência

Toda criação de pagamento vai com ``X-Idempotency-Key``. Sem isso, um clique
duplo ou um retry de rede cobraria duas vezes — e estorno de cartão é atrito
com o paciente, não só um lançamento contábil.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import Settings
from app.core.erros import ErroDominio
from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models.enums import MetodoPagamento, StatusPagamento
from app.providers.base import Cobranca, CobrancaRequest, EventoPagamento
from app.providers.pagamento.assinatura import validar_assinatura_mp

log = get_logger(__name__)

BASE_URL = "https://api.mercadopago.com"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

#: Taxas de referência para o simulador. Os valores reais dependem do contrato e
#: do prazo de liberação; o que o profissional recebe de fato vem no webhook.
TAXAS_REFERENCIA: dict[MetodoPagamento, Decimal] = {
    MetodoPagamento.PIX: Decimal("0.99"),
    MetodoPagamento.CARTAO_DEBITO: Decimal("1.99"),
    MetodoPagamento.CARTAO_CREDITO: Decimal("4.98"),
}

#: status do Mercado Pago -> nosso StatusPagamento
MAPA_STATUS = {
    "pending": StatusPagamento.PENDENTE,
    "in_process": StatusPagamento.PENDENTE,
    "authorized": StatusPagamento.PENDENTE,
    "approved": StatusPagamento.APROVADO,
    "rejected": StatusPagamento.RECUSADO,
    "cancelled": StatusPagamento.CANCELADO,
    "refunded": StatusPagamento.ESTORNADO,
    "charged_back": StatusPagamento.ESTORNADO,
}

MEIO_DE_PAGAMENTO = {
    MetodoPagamento.PIX: "pix",
    MetodoPagamento.CARTAO_CREDITO: "credit_card",
    MetodoPagamento.CARTAO_DEBITO: "debit_card",
}


class FalhaNoPagamento(ErroDominio):
    codigo = "falha_pagamento"
    status_http = 502
    mensagem_padrao = (
        "Não foi possível falar com o meio de pagamento agora. Tente novamente."
    )


class WebhookInvalido(ErroDominio):
    codigo = "webhook_invalido"
    status_http = 401
    mensagem_padrao = "Assinatura inválida."


class MercadoPagoPaymentProvider:
    nome = "mercadopago"

    def __init__(self, settings: Settings, cliente: httpx.AsyncClient | None = None) -> None:
        self._token = settings.mercadopago_access_token
        self._webhook_secret = settings.mercadopago_webhook_secret
        self._base_url = settings.app_base_url.rstrip("/")
        # Cliente injetável para que os contract tests interceptem com respx
        # sem tocar na rede.
        self._cliente = cliente

    def taxa_estimada(self, metodo: MetodoPagamento, valor_centavos: int) -> Decimal:
        return TAXAS_REFERENCIA[metodo]

    # --- HTTP ---------------------------------------------------------------

    def _headers(self, chave_idempotencia: str | None = None) -> dict[str, str]:
        cabecalhos = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        if chave_idempotencia:
            cabecalhos["X-Idempotency-Key"] = chave_idempotencia
        return cabecalhos

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _requisitar(
        self,
        metodo: str,
        caminho: str,
        *,
        json_body: dict[str, Any] | None = None,
        chave_idempotencia: str | None = None,
    ) -> dict[str, Any]:
        """Faz a chamada. Repete só em falha de transporte.

        Erro 4xx **não** é repetido: o pedido chegou e foi recusado; insistir
        não muda a resposta e pode duplicar cobrança.
        """
        cliente = self._cliente or httpx.AsyncClient(timeout=TIMEOUT)
        proprio = self._cliente is None
        try:
            resposta = await cliente.request(
                metodo,
                f"{BASE_URL}{caminho}",
                json=json_body,
                headers=self._headers(chave_idempotencia),
            )
        except httpx.TransportError:
            raise
        finally:
            if proprio:
                await cliente.aclose()

        if resposta.status_code >= 500:
            log.error(
                "mercadopago.erro_servidor",
                status=resposta.status_code,
                caminho=caminho,
            )
            raise FalhaNoPagamento()

        corpo: dict[str, Any] = resposta.json() if resposta.content else {}

        if resposta.status_code >= 400:
            # A mensagem do provedor vai para o log, nunca para o usuário: pode
            # conter detalhe de conta e não ajuda quem está tentando pagar.
            log.warning(
                "mercadopago.erro_requisicao",
                status=resposta.status_code,
                erro=corpo.get("message"),
                causa=corpo.get("cause"),
            )
            raise FalhaNoPagamento()

        return corpo

    # --- Operações ----------------------------------------------------------

    async def criar_cobranca(self, req: CobrancaRequest) -> Cobranca:
        corpo: dict[str, Any] = {
            "transaction_amount": float(Decimal(req.valor_centavos) / 100),
            "description": req.descricao,
            "payment_method_id": MEIO_DE_PAGAMENTO[req.metodo],
            "payer": {"email": req.pagador_email, "first_name": req.pagador_nome[:40]},
            "notification_url": f"{self._base_url}/api/webhooks/mercadopago",
            "external_reference": req.chave_idempotencia,
            "metadata": dict(req.metadados),
        }

        # O split: a comissão fica com a plataforma, o resto vai direto para o
        # profissional. Só faz sentido quando ele já conectou a conta por OAuth.
        if req.comissao_centavos and req.recebedor_externo_id:
            corpo["application_fee"] = float(Decimal(req.comissao_centavos) / 100)

        if req.metodo is MetodoPagamento.PIX:
            # Sem isso o Pix nasce sem prazo e o horário reservado ficaria preso
            # esperando um pagamento que talvez nunca venha.
            corpo["date_of_expiration"] = _formatar_expiracao(
                agora_utc().replace(microsecond=0)
            )

        dados = await self._requisitar(
            "POST", "/v1/payments", json_body=corpo, chave_idempotencia=req.chave_idempotencia
        )
        return _para_cobranca(dados)

    async def consultar(self, provedor_pagamento_id: str) -> Cobranca:
        dados = await self._requisitar("GET", f"/v1/payments/{provedor_pagamento_id}")
        return _para_cobranca(dados)

    async def estornar(
        self, provedor_pagamento_id: str, valor_centavos: int | None = None
    ) -> Cobranca:
        corpo = (
            {"amount": float(Decimal(valor_centavos) / 100)}
            if valor_centavos is not None
            else None
        )
        await self._requisitar(
            "POST",
            f"/v1/payments/{provedor_pagamento_id}/refunds",
            json_body=corpo,
            chave_idempotencia=f"estorno:{provedor_pagamento_id}:{valor_centavos or 'total'}",
        )
        # O refund devolve o próprio estorno, não o pagamento; consultamos para
        # ter o status final da cobrança.
        return await self.consultar(provedor_pagamento_id)

    # --- Webhook ------------------------------------------------------------

    def validar_webhook(self, headers: Mapping[str, str], corpo: bytes) -> EventoPagamento:
        return validar_assinatura_mp(
            headers, corpo, segredo=self._webhook_secret, erro=WebhookInvalido
        )


def _para_cobranca(dados: Mapping[str, Any]) -> Cobranca:
    status = MAPA_STATUS.get(str(dados.get("status", "")), StatusPagamento.PENDENTE)
    transacao = dados.get("point_of_interaction") or {}
    dados_transacao = (transacao.get("transaction_data") or {}) if transacao else {}

    detalhes = dados.get("fee_details") or []
    taxa_centavos = sum(
        _centavos(d.get("amount", 0))
        for d in detalhes
        if d.get("type") in ("mercadopago_fee", "application_fee")
    )

    return Cobranca(
        provedor_pagamento_id=str(dados.get("id", "")),
        status=status,
        valor_centavos=_centavos(dados.get("transaction_amount", 0)),
        taxa_provedor_centavos=taxa_centavos,
        pix_qrcode=dados_transacao.get("qr_code_base64"),
        pix_copia_cola=dados_transacao.get("qr_code"),
        pix_expira_em=_ler_data(dados.get("date_of_expiration")),
        url_checkout=dados_transacao.get("ticket_url"),
        payload_bruto=dict(dados),
    )


def _centavos(valor: object) -> int:
    """Reais -> centavos, via `Decimal` e `ROUND_HALF_UP`.

    Passa por `str` de propósito: `Decimal(149.99)` carrega o erro do float
    (`149.990000000000009...`), enquanto `Decimal("149.99")` é exato. E
    `round()` do Python arredonda para o par mais próximo -- num caminho de
    dinheiro, isso é diferença que aparece na conciliação.
    """
    return int(
        (Decimal(str(valor or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _ler_data(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return None


def _formatar_expiracao(quando: datetime) -> str:
    """O Mercado Pago exige offset explícito, no formato ``-03:00``."""
    from datetime import timedelta

    from app.core.tempo import TZ_BR

    local = (quando + timedelta(minutes=30)).astimezone(TZ_BR)
    return local.strftime("%Y-%m-%dT%H:%M:%S.000%z")[:-2] + ":" + local.strftime("%z")[-2:]

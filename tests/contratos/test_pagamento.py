"""Suíte de contrato do `PaymentProvider`.

A mesma bateria roda contra o **fake** e contra o **Mercado Pago real**, este
último com `respx` interceptando o HTTP e devolvendo respostas gravadas da
documentação do provedor.

O objetivo é específico: impedir que o fake minta. Um fake que aceita o que o
real recusa faz a suíte inteira passar e o checkout quebrar em produção — e o
sintoma aparece longe da causa. Aqui, divergência de comportamento entre os dois
é falha de teste.

Nenhum teste toca a rede, então tudo isto roda sem conta no Mercado Pago.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
import respx

from app.core.config import Settings
from app.models.enums import MetodoPagamento, StatusPagamento
from app.providers.base import CobrancaRequest, PaymentProvider
from app.providers.pagamento.fake import SEGREDO_DEV, FakePaymentProvider
from app.providers.pagamento.mercadopago import (
    BASE_URL,
    FalhaNoPagamento,
    MercadoPagoPaymentProvider,
    WebhookInvalido,
)

SEGREDO = "segredo-de-teste"


def _settings() -> Settings:
    return Settings(
        mercadopago_access_token="TEST-token",
        mercadopago_webhook_secret=SEGREDO,
        app_base_url="https://psiconnect.test",
        chave_criptografia="0" * 44,
        secret_key="x" * 40,
    )


def _pagamento_aprovado(**extra: Any) -> dict[str, Any]:
    """Resposta de `POST /v1/payments`, campos conforme a doc do Mercado Pago."""
    base = {
        "id": 1234567890,
        "status": "approved",
        "status_detail": "accredited",
        "transaction_amount": 150.0,
        "currency_id": "BRL",
        "external_reference": "chave-1",
        "fee_details": [{"type": "mercadopago_fee", "amount": 1.49}],
    }
    base.update(extra)
    return base


def _pix_pendente() -> dict[str, Any]:
    return {
        "id": 987654321,
        "status": "pending",
        "transaction_amount": 150.0,
        "date_of_expiration": "2026-08-13T15:30:00.000-03:00",
        "point_of_interaction": {
            "transaction_data": {
                "qr_code": "00020126580014BR.GOV.BCB.PIX0136chave-pix-aqui5204000053039865802BR",
                "qr_code_base64": "aVZCT1J3MEtHZ29BQUFBTlN...",
                "ticket_url": "https://www.mercadopago.com.br/payments/987654321/ticket",
            }
        },
    }


def _requisicao(
    valor: int = 15000, metodo: MetodoPagamento = MetodoPagamento.PIX
) -> CobrancaRequest:
    return CobrancaRequest(
        valor_centavos=valor,
        metodo=metodo,
        descricao="Sessão de psicoterapia",
        chave_idempotencia="chave-1",
        pagador_nome="Maria de Souza",
        pagador_email="maria@exemplo.test",
        comissao_centavos=1800,
        recebedor_externo_id="mp-user-42",
    )


def assinar(
    recurso_id: str, request_id: str, segredo: str = SEGREDO, ts: str = "1755000000"
) -> str:
    manifest = f"id:{recurso_id};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(segredo.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"


# ---------------------------------------------------------------------------
# Bateria comum: roda contra os dois provedores
# ---------------------------------------------------------------------------


@pytest.fixture(params=["fake", "mercadopago"])
def provedor(request: pytest.FixtureRequest) -> tuple[PaymentProvider, str]:
    """Devolve o provedor e **o segredo que ele espera**.

    O fake usa um segredo fixo e público; o real, o das settings. O contrato
    testado é "exige assinatura válida", não "usa este segredo".
    """
    if request.param == "fake":
        return FakePaymentProvider(), SEGREDO_DEV
    return MercadoPagoPaymentProvider(_settings()), SEGREDO


class TestContratoComum:
    async def test_cobranca_pix_traz_copia_e_cola(
        self, provedor: tuple[PaymentProvider, str]
    ) -> None:
        """Sem o copia-e-cola não há como pagar no celular — é obrigatório."""
        provedor, _ = provedor
        with respx.mock:
            respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(201, json=_pix_pendente())
            )
            cobranca = await provedor.criar_cobranca(_requisicao())

        assert cobranca.provedor_pagamento_id
        assert cobranca.pix_copia_cola
        assert cobranca.pix_copia_cola.startswith("00020126")
        assert cobranca.status is StatusPagamento.PENDENTE

    async def test_valor_preservado_em_centavos(
        self, provedor: tuple[PaymentProvider, str]
    ) -> None:
        """Ida e volta por `float` não pode perder centavo."""
        provedor, _ = provedor
        with respx.mock:
            respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(
                    201, json=_pagamento_aprovado(transaction_amount=149.99)
                )
            )
            cobranca = await provedor.criar_cobranca(
                _requisicao(valor=14999, metodo=MetodoPagamento.CARTAO_CREDITO)
            )

        assert cobranca.valor_centavos == 14999

    async def test_taxa_estimada_por_metodo(self, provedor: tuple[PaymentProvider, str]) -> None:
        provedor, _ = provedor
        pix = provedor.taxa_estimada(MetodoPagamento.PIX, 15000)
        credito = provedor.taxa_estimada(MetodoPagamento.CARTAO_CREDITO, 15000)
        # Crédito é sempre mais caro que Pix; o simulador depende disso.
        assert 0 < pix < credito

    async def test_webhook_sem_assinatura_e_recusado(
        self, provedor: tuple[PaymentProvider, str]
    ) -> None:
        provedor, _ = provedor
        corpo = json.dumps({"type": "payment", "data": {"id": "1234567890"}}).encode()
        with pytest.raises(WebhookInvalido):
            provedor.validar_webhook({"x-request-id": "req-1"}, corpo)

    async def test_webhook_com_assinatura_valida(
        self, provedor: tuple[PaymentProvider, str]
    ) -> None:
        provedor, segredo = provedor
        corpo = json.dumps({"type": "payment", "data": {"id": "1234567890"}}).encode()
        evento = provedor.validar_webhook(
            {
                "x-signature": assinar("1234567890", "req-1", segredo),
                "x-request-id": "req-1",
            },
            corpo,
        )
        assert evento.provedor_pagamento_id == "1234567890"
        # O id do evento é o do request: é ele que se repete no reenvio.
        assert evento.evento_id_externo == "req-1"


# ---------------------------------------------------------------------------
# Específico do adaptador real: o que o fake não tem como exercitar
# ---------------------------------------------------------------------------


class TestMercadoPago:
    @pytest.fixture
    def mp(self) -> MercadoPagoPaymentProvider:
        return MercadoPagoPaymentProvider(_settings())

    async def test_split_vai_como_application_fee(self, mp: MercadoPagoPaymentProvider) -> None:
        """A comissão precisa sair no `application_fee`, em reais.

        Se for para o campo errado, o pagamento é aceito e a plataforma
        simplesmente não recebe — falha silenciosa e cara.
        """
        with respx.mock:
            rota = respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(201, json=_pagamento_aprovado())
            )
            await mp.criar_cobranca(_requisicao())

        enviado = json.loads(rota.calls.last.request.content)
        assert enviado["application_fee"] == 18.0
        assert enviado["transaction_amount"] == 150.0

    async def test_sem_recebedor_conectado_nao_ha_split(
        self, mp: MercadoPagoPaymentProvider
    ) -> None:
        """Profissional que não conectou a conta por OAuth não pode ter split."""
        req = CobrancaRequest(
            valor_centavos=15000,
            metodo=MetodoPagamento.PIX,
            descricao="Sessão",
            chave_idempotencia="chave-2",
            pagador_nome="Maria",
            pagador_email="maria@exemplo.test",
            comissao_centavos=1800,
            recebedor_externo_id=None,
        )
        with respx.mock:
            rota = respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(201, json=_pagamento_aprovado())
            )
            await mp.criar_cobranca(req)

        assert "application_fee" not in json.loads(rota.calls.last.request.content)

    async def test_chave_de_idempotencia_no_header(self, mp: MercadoPagoPaymentProvider) -> None:
        """Sem isso, clique duplo cobra duas vezes."""
        with respx.mock:
            rota = respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(201, json=_pagamento_aprovado())
            )
            await mp.criar_cobranca(_requisicao())

        assert rota.calls.last.request.headers["X-Idempotency-Key"] == "chave-1"

    async def test_pix_nasce_com_expiracao(self, mp: MercadoPagoPaymentProvider) -> None:
        """Pix sem prazo prenderia o horário reservado indefinidamente."""
        with respx.mock:
            rota = respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(201, json=_pix_pendente())
            )
            await mp.criar_cobranca(_requisicao())

        enviado = json.loads(rota.calls.last.request.content)
        assert "date_of_expiration" in enviado
        # Offset explícito é exigência do provedor; sem ele a API recusa.
        assert enviado["date_of_expiration"].endswith(("-03:00", "-02:00"))

    async def test_erro_4xx_nao_e_repetido(self, mp: MercadoPagoPaymentProvider) -> None:
        """Repetir uma recusa não muda a resposta e arrisca cobrar duas vezes."""
        with respx.mock:
            rota = respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(400, json={"message": "invalid card"})
            )
            with pytest.raises(FalhaNoPagamento):
                await mp.criar_cobranca(_requisicao())

        assert rota.call_count == 1

    async def test_falha_de_rede_e_repetida(self, mp: MercadoPagoPaymentProvider) -> None:
        with respx.mock:
            rota = respx.post(f"{BASE_URL}/v1/payments").mock(
                side_effect=httpx.ConnectError("sem rede")
            )
            with pytest.raises(httpx.ConnectError):
                await mp.criar_cobranca(_requisicao())

        assert rota.call_count == 3

    async def test_mensagem_do_provedor_nao_vaza_ao_usuario(
        self, mp: MercadoPagoPaymentProvider
    ) -> None:
        """O texto do provedor pode conter detalhe de conta e não ajuda quem paga."""
        with respx.mock:
            respx.post(f"{BASE_URL}/v1/payments").mock(
                return_value=httpx.Response(
                    400, json={"message": "collector_id 602860026 not allowed"}
                )
            )
            with pytest.raises(FalhaNoPagamento) as exc:
                await mp.criar_cobranca(_requisicao())

        assert "602860026" not in exc.value.mensagem_usuario
        assert "collector" not in exc.value.mensagem_usuario.lower()

    async def test_assinatura_de_outro_recurso_e_recusada(
        self, mp: MercadoPagoPaymentProvider
    ) -> None:
        """Assinatura válida para o pagamento X não vale para o pagamento Y.

        É o ataque óbvio: capturar um webhook legítimo e reenviá-lo trocando o
        id para confirmar outra cobrança.
        """
        corpo = json.dumps({"type": "payment", "data": {"id": "111"}}).encode()
        with pytest.raises(WebhookInvalido):
            mp.validar_webhook(
                {"x-signature": assinar("999", "req-1"), "x-request-id": "req-1"},
                corpo,
            )

    async def test_estorno_consulta_status_final(self, mp: MercadoPagoPaymentProvider) -> None:
        with respx.mock:
            respx.post(f"{BASE_URL}/v1/payments/123/refunds").mock(
                return_value=httpx.Response(201, json={"id": 55, "amount": 150.0})
            )
            respx.get(f"{BASE_URL}/v1/payments/123").mock(
                return_value=httpx.Response(
                    200, json=_pagamento_aprovado(id=123, status="refunded")
                )
            )
            cobranca = await mp.estornar("123")

        assert cobranca.status is StatusPagamento.ESTORNADO

    async def test_status_desconhecido_nao_vira_aprovado(
        self, mp: MercadoPagoPaymentProvider
    ) -> None:
        """Um status novo do provedor deve cair em PENDENTE, nunca em APROVADO.

        Falhar para o lado de "ainda não pago" mantém o dinheiro fora do
        sistema até alguém olhar; o contrário libera sessão sem pagamento.
        """
        with respx.mock:
            respx.get(f"{BASE_URL}/v1/payments/123").mock(
                return_value=httpx.Response(
                    200, json=_pagamento_aprovado(status="status_que_nao_existe_ainda")
                )
            )
            cobranca = await mp.consultar("123")

        assert cobranca.status is StatusPagamento.PENDENTE

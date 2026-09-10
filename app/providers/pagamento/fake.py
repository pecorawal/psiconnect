"""Provedor de pagamento falso.

**Não é um no-op.** Aprova de verdade, calcula taxa de verdade e tem um caminho
de recusa determinístico -- porque o teste que só percorre o caminho feliz não
prova nada sobre o que acontece quando o cartão é negado.

Convenção de teste: **valor terminado em 13 centavos é recusado**. Escolhido por
ser fácil de lembrar e improvável num preço real de consulta.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal

from app.core.dinheiro import percentual_de
from app.core.tempo import agora_utc
from app.models.enums import MetodoPagamento, StatusPagamento
from app.providers.base import Cobranca, CobrancaRequest, EventoPagamento
from app.providers.pagamento.assinatura import validar_assinatura_mp
from app.providers.pagamento.mercadopago import WebhookInvalido

#: Segredo fixo e público do fake. Não protege nada -- existe para o fake exigir
#: assinatura do mesmo jeito que o real, e assim manter a rota honesta.
SEGREDO_DEV = "psiconnect-webhook-dev"


#: Taxas aproximadas de mercado, usadas pelo simulador de recebimento.
TAXAS: dict[MetodoPagamento, Decimal] = {
    MetodoPagamento.PIX: Decimal("0.99"),
    MetodoPagamento.CARTAO_DEBITO: Decimal("1.99"),
    # Maior que a comissão padrão de 5%: no crédito, se a plataforma absorver a
    # taxa, ela perde dinheiro na venda. Ver questão aberta em docs/05-roadmap.md.
    MetodoPagamento.CARTAO_CREDITO: Decimal("4.98"),
}

CENTAVOS_QUE_RECUSAM = 13


class FakePaymentProvider:
    nome = "fake"

    def __init__(self) -> None:
        self._cobrancas: dict[str, Cobranca] = {}

    def taxa_estimada(self, metodo: MetodoPagamento, valor_centavos: int) -> Decimal:
        return TAXAS[metodo]

    async def criar_cobranca(self, req: CobrancaRequest) -> Cobranca:
        recusar = req.valor_centavos % 100 == CENTAVOS_QUE_RECUSAM
        cobranca_id = f"fake-{uuid.uuid4().hex[:16]}"
        taxa = percentual_de(req.valor_centavos, TAXAS[req.metodo])

        if recusar:
            cobranca = Cobranca(
                provedor_pagamento_id=cobranca_id,
                status=StatusPagamento.RECUSADO,
                valor_centavos=req.valor_centavos,
                taxa_provedor_centavos=0,
                payload_bruto={"motivo_recusa": "saldo_insuficiente", "simulado": True},
            )
        elif req.metodo is MetodoPagamento.PIX:
            # Pix real fica PENDENTE até o pagador escanear; o fake também, para
            # que a tela de "aguardando pagamento" seja exercitada.
            cobranca = Cobranca(
                provedor_pagamento_id=cobranca_id,
                status=StatusPagamento.PENDENTE,
                valor_centavos=req.valor_centavos,
                taxa_provedor_centavos=taxa,
                pix_qrcode=f"data:image/svg+xml;base64,{_qrcode_svg_base64(cobranca_id)}",
                pix_copia_cola=f"00020126580014BR.GOV.BCB.PIX{cobranca_id}5204000053039865802BR",
                pix_expira_em=agora_utc() + timedelta(minutes=30),
                payload_bruto={"simulado": True},
            )
        else:
            cobranca = Cobranca(
                provedor_pagamento_id=cobranca_id,
                status=StatusPagamento.APROVADO,
                valor_centavos=req.valor_centavos,
                taxa_provedor_centavos=taxa,
                payload_bruto={"simulado": True},
            )

        self._cobrancas[cobranca_id] = cobranca
        return cobranca

    async def consultar(self, provedor_pagamento_id: str) -> Cobranca:
        cobranca = self._cobrancas.get(provedor_pagamento_id)
        if cobranca is None:
            raise KeyError(f"cobrança desconhecida: {provedor_pagamento_id}")
        return cobranca

    async def confirmar_pix(self, provedor_pagamento_id: str) -> Cobranca:
        """Simula o pagador escaneando o QR. Só existe no fake, usado em /dev."""
        cobranca = await self.consultar(provedor_pagamento_id)
        aprovada = Cobranca(
            provedor_pagamento_id=cobranca.provedor_pagamento_id,
            status=StatusPagamento.APROVADO,
            valor_centavos=cobranca.valor_centavos,
            taxa_provedor_centavos=cobranca.taxa_provedor_centavos,
            payload_bruto={"simulado": True, "confirmado_manualmente": True},
        )
        self._cobrancas[provedor_pagamento_id] = aprovada
        return aprovada

    async def estornar(
        self, provedor_pagamento_id: str, valor_centavos: int | None = None
    ) -> Cobranca:
        cobranca = await self.consultar(provedor_pagamento_id)
        estornada = Cobranca(
            provedor_pagamento_id=cobranca.provedor_pagamento_id,
            status=StatusPagamento.ESTORNADO,
            valor_centavos=valor_centavos or cobranca.valor_centavos,
            taxa_provedor_centavos=cobranca.taxa_provedor_centavos,
            payload_bruto={"simulado": True},
        )
        self._cobrancas[provedor_pagamento_id] = estornada
        return estornada

    def validar_webhook(self, headers: Mapping[str, str], corpo: bytes) -> EventoPagamento:
        """Mesmo formato e **mesma exigência de assinatura** do provedor real.

        As duas coisas foram divergentes até a suíte de contrato apontar. O
        fake lia um payload inventado (``provedor_pagamento_id`` na raiz) e
        aceitava qualquer requisição sem assinatura. Como a rota de webhook é
        testada contra o fake, um esquecimento de validação na rota passaria
        despercebido -- e trocar o provider por ``fake`` fora de dev deixaria o
        endpoint aberto para qualquer um confirmar pagamento.

        O segredo é fixo e público (``SEGREDO_DEV``): o objetivo aqui é manter o
        formato honesto, não proteger nada.
        """
        return validar_assinatura_mp(headers, corpo, segredo=SEGREDO_DEV, erro=WebhookInvalido)


def _qrcode_svg_base64(texto: str) -> str:
    """Placeholder visual: um quadrado com o id. Não é um QR válido."""
    import base64

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="180" height="180">'
        '<rect width="180" height="180" fill="#fff" stroke="#0f766e" stroke-width="4"/>'
        '<text x="90" y="86" text-anchor="middle" font-family="monospace" font-size="11" '
        'fill="#0f766e">QR simulado</text>'
        f'<text x="90" y="104" text-anchor="middle" font-family="monospace" font-size="8" '
        f'fill="#64748b">{texto[:20]}</text></svg>'
    )
    return base64.b64encode(svg.encode()).decode()

"""Validação da assinatura de webhook do Mercado Pago.

Módulo compartilhado **de propósito**: o fake e o adaptador real usam esta mesma
função. Quando eram duas implementações, elas divergiram — o fake aceitava
qualquer requisição sem assinatura e lia um payload de formato inventado. Como a
rota de webhook é exercitada contra o fake nos testes, a divergência escondia
dois riscos: uma validação esquecida na rota passaria despercebida, e apontar
``PAGAMENTO_PROVIDER=fake`` fora de dev abriria o endpoint.

Com uma função só, o fake não tem como mentir sobre este contrato.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger
from app.models.enums import StatusPagamento
from app.providers.base import EventoPagamento

log = get_logger(__name__)


def validar_assinatura_mp(
    headers: Mapping[str, str],
    corpo: bytes,
    *,
    segredo: str,
    erro: type[Exception],
) -> EventoPagamento:
    """Confere o HMAC-SHA256 e extrai o evento.

    O Mercado Pago assina um *manifest* montado com o id do recurso, o header
    ``x-request-id`` e o timestamp. Amarrar a assinatura ao id do recurso é o
    que impede capturar um webhook legítimo e reenviá-lo trocando o id para
    confirmar outra cobrança.
    """
    if not segredo:
        # Aceitar sem segredo configurado deixaria qualquer um que descubra a
        # URL confirmar pagamentos que nunca aconteceram.
        log.error("mercadopago.webhook_sem_segredo")
        raise erro("Webhook não configurado.")

    cabecalhos = {k.lower(): v for k, v in headers.items()}
    assinatura = cabecalhos.get("x-signature", "")
    request_id = cabecalhos.get("x-request-id", "")

    try:
        dados: dict[str, Any] = json.loads(corpo or b"{}")
    except json.JSONDecodeError as exc:
        raise erro("Corpo inválido.") from exc

    recurso_id = str((dados.get("data") or {}).get("id") or dados.get("id") or "")

    partes = dict(p.strip().split("=", 1) for p in assinatura.split(",") if "=" in p)
    ts = partes.get("ts", "")
    recebido = partes.get("v1", "")

    manifest = f"id:{recurso_id};request-id:{request_id};ts:{ts};"
    esperado = hmac.new(segredo.encode(), manifest.encode(), hashlib.sha256).hexdigest()

    # compare_digest: comparação em tempo constante, para não permitir descobrir
    # a assinatura byte a byte medindo o tempo de resposta.
    if not recebido or not hmac.compare_digest(esperado, recebido):
        log.warning("mercadopago.webhook_assinatura_invalida", recurso_id=recurso_id)
        raise erro()

    return EventoPagamento(
        # O id do evento é o do request: é ele que se repete quando o Mercado
        # Pago reenvia, então é por ele que a idempotência funciona.
        evento_id_externo=request_id or recurso_id,
        tipo=str(dados.get("type") or dados.get("action") or "payment"),
        provedor_pagamento_id=recurso_id,
        # O status real vem da consulta ao pagamento, nunca do corpo do
        # webhook: o corpo só avisa "algo mudou no recurso X". Confiar no
        # status do corpo seria aceitar o valor que o remetente escolheu.
        status=StatusPagamento.PENDENTE,
        payload=dados,
    )
